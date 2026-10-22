"""Bibliotheque MP3 locale (CDC §8, §10) : scan du dossier MP3_LIBRARY_PATH,
lecture des tags ID3 et alimentation du catalogue partage MusicArtist ->
MusicAlbum -> MusicTrack (cf. music_catalog).

Le scan tourne en tache planifiee (job 'mp3_scan', app/scheduler.py), qui
recherche aussi les pochettes/photos manquantes : les ecrans n'ont alors plus
d'appel reseau a faire. Il est aussi lance apres chaque modification faite
depuis l'explorateur MP3 de l'admin (app/mp3_files_service.py). Il n'est plus
lance a l'affichage de /library : sur une grosse bibliotheque (10 000 MP3),
l'ecran attendait la fin du parcours du disque."""

import logging
import os
import re
import threading
import time
from datetime import datetime

from mutagen import File as MutagenFile
from mutagen.id3 import ID3

from app.extensions import db
from app.models import MusicAlbum, MusicTrack
from app import music_catalog

logger = logging.getLogger(__name__)

SCAN_THROTTLE_SECONDS = 60
SCAN_COMMIT_EVERY = 50

# Pochette locale d'un album MP3 : image integree au MP3 (tag APIC), sinon
# image du dossier. Prioritaire sur Deezer/iTunes : c'est la bonne edition.
LOCAL_COVER_URL = "/media/album_cover/{album_id}"  # cf. app/media.py
_FOLDER_COVER_NAMES = ("cover", "folder", "front", "album")
_IMAGE_EXTENSIONS = (".jpg", ".jpeg", ".png")
_last_scan_at = 0.0
# Un seul scan a la fois (job planifie, explorateur MP3 de l'admin).
_scan_lock = threading.Lock()


def _library_root() -> str:
    return os.path.abspath(os.environ.get("MP3_LIBRARY_PATH", "./mp3"))


def _first_int(value: str | None) -> int | None:
    """'3/12' -> 3, '2001-03-12' -> 2001."""
    match = re.match(r"\s*(\d+)", value or "")
    return int(match.group(1)) if match else None


def _read_tags(absolute_path: str) -> dict:
    """Tags utiles du MP3, avec repli sur le nom de fichier pour le titre.
    Album absent -> None (morceau hors album, pas un faux 'Unknown Album')."""
    tags = {"title": os.path.splitext(os.path.basename(absolute_path))[0]}
    try:
        audio = MutagenFile(absolute_path, easy=True)
    except Exception:
        audio = None
    if not audio:
        return tags

    if audio.info and getattr(audio.info, "length", None):
        tags["duration"] = int(audio.info.length)
    if not audio.tags:
        return tags

    def first(name):
        values = audio.tags.get(name) or []
        return values[0].strip() if values and values[0] else None

    tags["title"] = first("title") or tags["title"]
    tags["artist"] = first("artist")
    tags["album"] = first("album")
    tags["album_artist"] = first("albumartist")
    tags["track_number"] = _first_int(first("tracknumber"))
    tags["year"] = _first_int(first("date"))
    if first("compilation") == "1" and not tags["album_artist"]:
        tags["album_artist"] = music_catalog.VARIOUS_ARTISTS
    return tags


def _embedded_cover(absolute_path: str) -> tuple[bytes, str] | None:
    try:
        pictures = ID3(absolute_path).getall("APIC")
    except Exception:
        return None
    pictures = [p for p in pictures if p.data]
    if not pictures:
        return None
    front = next((p for p in pictures if p.type == 3), pictures[0])  # 3 = Front Cover
    return front.data, front.mime or "image/jpeg"


def _folder_cover(directory: str) -> str | None:
    """cover.jpg / folder.jpg / front.jpg / album.jpg, ou a defaut la grande
    image deposee par le lecteur Windows (AlbumArt_{...}_Large.jpg)."""
    try:
        names = os.listdir(directory)
    except OSError:
        return None
    images = {n.lower(): n for n in names if n.lower().endswith(_IMAGE_EXTENSIONS)}
    for base in _FOLDER_COVER_NAMES:
        for ext in _IMAGE_EXTENSIONS:
            if base + ext in images:
                return os.path.join(directory, images[base + ext])
    for lower, name in sorted(images.items()):
        if lower.startswith("albumart") and lower.endswith("_large.jpg"):
            return os.path.join(directory, name)
    return None


def local_cover(album_id: int) -> tuple[bytes, str] | str | None:
    """Pochette locale de l'album : (octets, type MIME) si integree au MP3,
    chemin du fichier si image du dossier, None si aucune."""
    tracks = MusicTrack.query.filter_by(
        AlbumId=album_id, Source=music_catalog.SOURCE_LOCAL, IsAvailable=True
    ).order_by(MusicTrack.TrackNumber).limit(5)
    paths = [p for p in (absolute_path_for(t.SourceRef) for t in tracks) if p]
    for path in paths:
        embedded = _embedded_cover(path)
        if embedded:
            return embedded
    for directory in dict.fromkeys(os.path.dirname(p) for p in paths):
        folder = _folder_cover(directory)
        if folder:
            return folder
    return None


def apply_local_covers(album_ids=None) -> None:
    """Donne aux albums MP3 leur pochette locale (a la place d'une pochette
    Deezer/iTunes), et retire une pochette locale qui a disparu - l'album
    repasse alors par la recherche Deezer/iTunes. `album_ids` None = tous les
    albums ayant des MP3 (scan planifie)."""
    query = MusicAlbum.query.filter(
        MusicAlbum.AlbumId.in_(
            db.session.query(MusicTrack.AlbumId).filter(MusicTrack.Source == music_catalog.SOURCE_LOCAL)
        )
    )
    if album_ids is not None:
        album_ids = {a for a in album_ids if a}
        if not album_ids:
            return
        query = query.filter(MusicAlbum.AlbumId.in_(album_ids))
    for album in query:
        local_url = LOCAL_COVER_URL.format(album_id=album.AlbumId)
        if local_cover(album.AlbumId):
            if album.CoverUrl != local_url:
                album.CoverUrl = local_url
                album.CheckedAt = datetime.utcnow()
        elif album.CoverUrl == local_url:
            album.CoverUrl = None
            album.CheckedAt = None


def scan_library(force: bool = False, resolve_images: bool = False) -> dict:
    """Parcourt MP3_LIBRARY_PATH, ajoute les nouveaux MP3 au catalogue et
    relit les tags des MP3 modifies depuis le dernier scan (date de
    modification ou taille differente - pas de MD5, qui obligerait a relire
    chaque fichier en entier a chaque passage).
    `resolve_images` : cherche aussi les pochettes/photos manquantes de tout
    le catalogue (job planifie uniquement - trop long pour une requete HTTP).
    Renvoie les compteurs {"added", "updated", "hidden", "restored"}."""
    global _last_scan_at
    stats = {"added": 0, "updated": 0, "hidden": 0, "restored": 0}
    if not force and (time.monotonic() - _last_scan_at) < SCAN_THROTTLE_SECONDS:
        return stats
    # Scan force (job, admin) : attend la fin d'un scan en cours ; sinon on
    # laisse le scan en cours faire le travail.
    if not _scan_lock.acquire(blocking=force):
        return stats
    try:
        _last_scan_at = time.monotonic()
        _scan_files(stats, all_covers=resolve_images)
    finally:
        _scan_lock.release()
    if resolve_images:
        music_catalog.ensure_all_images()
    return stats


def _scan_files(stats: dict, all_covers: bool) -> None:
    root = _library_root()
    new_tracks = []
    touched = []  # MP3 ajoutes ou dont les tags ont ete relus
    moved = False
    if not os.path.isdir(root):
        # Dossier absent (disque reseau non monte ?) : on ne conclut surtout
        # pas que tous les MP3 ont disparu.
        logger.warning("Scan MP3 : dossier %s introuvable, scan ignore", root)
        return

    local_tracks = {t.SourceRef: t for t in MusicTrack.query.filter_by(Source=music_catalog.SOURCE_LOCAL)}
    present = set()
    for dirpath, _dirnames, filenames in os.walk(root):
        for filename in filenames:
            if not filename.lower().endswith(".mp3"):
                continue
            absolute_path = os.path.join(dirpath, filename)
            relative_path = os.path.relpath(absolute_path, root).replace(os.sep, "/")
            present.add(relative_path)
            try:
                stat = os.stat(absolute_path)
            except OSError:
                continue
            track = local_tracks.get(relative_path)
            if track is not None and track.FileMtime == stat.st_mtime and track.FileSize == stat.st_size:
                continue

            # Commit regulier : un premier scan d'une grosse bibliotheque ne
            # doit pas garder la base verrouillee en ecriture pendant des
            # minutes (requetes des utilisateurs en "database is locked").
            if touched and len(touched) % SCAN_COMMIT_EVERY == 0:
                db.session.commit()
            tags = _read_tags(absolute_path)
            if track is not None:
                # Deja connu mais modifie (ou jamais date : scan anterieur a
                # FileMtime) : on reapplique les tags au meme morceau.
                if music_catalog.update_track(
                    track,
                    tags["title"],
                    tags.get("artist"),
                    album_title=tags.get("album"),
                    album_artist_name=tags.get("album_artist"),
                    track_number=tags.get("track_number"),
                    duration=tags.get("duration"),
                    year=tags.get("year"),
                ):
                    moved = True
                    stats["updated"] += 1
                track.FileMtime, track.FileSize = stat.st_mtime, stat.st_size
                touched.append(track)
                continue

            track, _ = music_catalog.get_or_create_track(
                music_catalog.SOURCE_LOCAL,
                relative_path,
                tags["title"],
                tags.get("artist"),
                album_title=tags.get("album"),
                album_artist_name=tags.get("album_artist"),
                track_number=tags.get("track_number"),
                duration=tags.get("duration"),
                year=tags.get("year"),
            )
            track.FileMtime, track.FileSize = stat.st_mtime, stat.st_size
            local_tracks[relative_path] = track
            new_tracks.append(track)
            touched.append(track)

    stats["hidden"], stats["restored"] = _update_availability(local_tracks, present)

    if new_tracks:
        music_catalog.notify_new_tracks(new_tracks)
    if moved:
        db.session.flush()
        music_catalog.delete_orphans()  # ex-'Unknown Artist', ancien album...
    # Pochettes locales : tous les albums au scan planifie, sinon seulement
    # ceux des MP3 ajoutes/modifies (scan a la demande, qui doit rester rapide).
    apply_local_covers(None if all_covers else {t.AlbumId for t in touched})
    db.session.commit()
    stats["added"] = len(new_tracks)


def _update_availability(local_tracks: dict, present: set) -> tuple[int, int]:
    """Masque les MP3 disparus du disque (IsAvailable=False) et reaffiche
    ceux qui sont revenus au meme chemin, avec leur historique intact.
    Sans risque meme si un disque reseau est monte vide : le masquage est
    reversible, et la purge definitive n'intervient qu'apres 30 jours
    (cf. music_catalog.purge_missing_tracks). Renvoie (masques, reaffiches)."""
    if not present and any(t.IsAvailable for t in local_tracks.values()):
        logger.warning("Scan MP3 : aucun fichier trouve sous %s, tous les MP3 sont masques", _library_root())

    now = datetime.utcnow()
    hidden = restored = 0
    for ref, track in local_tracks.items():
        if ref in present:
            if not track.IsAvailable:
                track.IsAvailable = True
                track.MissingSince = None
                restored += 1
        elif track.IsAvailable:
            track.IsAvailable = False
            track.MissingSince = now
            hidden += 1
    return hidden, restored


def absolute_path_for(path_file: str) -> str | None:
    root = _library_root()
    candidate = os.path.realpath(os.path.join(root, path_file))
    if not candidate.startswith(os.path.realpath(root) + os.sep):
        return None
    return candidate if os.path.isfile(candidate) else None
