"""Explorateur de fichiers MP3 (Admin, onglet MP3) : navigation dans
MP3_LIBRARY_PATH, creation/renommage/deplacement/suppression de fichiers et
dossiers, upload de MP3 et edition de leurs tags ID3.

Tous les chemins recus sont relatifs a la racine MP3 et passent par
_safe_path(), qui refuse tout ce qui en sortirait ('..', chemin absolu, lien
symbolique vers l'exterieur).

Le catalogue (MusicTrack.SourceRef = chemin relatif du MP3) suit les
operations : un renommage/deplacement met a jour SourceRef (le morceau garde
ses ecoutes, likes, commentaires), puis sync_catalog() lance un scan force
qui ajoute les nouveaux MP3, relit les tags modifies et masque les MP3
supprimes (purge definitive par le job mensuel 'music_purge')."""

import logging
import os
import shutil
from datetime import datetime

from mutagen import File as MutagenFile
from mutagen.easyid3 import EasyID3
from mutagen.id3 import ID3NoHeaderError

from app.extensions import db
from app.models import MusicTrack
from app import music_catalog, mp3_library_service

logger = logging.getLogger(__name__)

MP3_EXTENSION = ".mp3"
_FORBIDDEN_CHARS = set('<>:"/\\|?*') | {chr(c) for c in range(32)}
_RESERVED_NAMES = {"con", "prn", "aux", "nul"} | {f"com{i}" for i in range(1, 10)} | {f"lpt{i}" for i in range(1, 10)}

# Tags editables (cles EasyID3), dans l'ordre du formulaire.
TAG_FIELDS = ("title", "artist", "album", "albumartist", "tracknumber", "date")


class Mp3FileError(Exception):
    """Erreur affichable a l'admin : `key` est une cle de traduction."""

    def __init__(self, key: str, **kwargs):
        super().__init__(key)
        self.key = key
        self.kwargs = kwargs


def root() -> str:
    return os.path.realpath(mp3_library_service._library_root())


def _safe_path(rel: str | None) -> str:
    """Chemin absolu de `rel` sous la racine MP3, ou Mp3FileError."""
    base = root()
    rel = (rel or "").replace("\\", "/").strip("/")
    candidate = os.path.realpath(os.path.join(base, rel))
    if candidate != base and not candidate.startswith(base + os.sep):
        raise Mp3FileError("admin_mp3_err_path")
    return candidate


def _rel(absolute: str) -> str:
    rel = os.path.relpath(absolute, root()).replace(os.sep, "/")
    return "" if rel == "." else rel


def _check_name(name: str) -> str:
    name = (name or "").strip()
    if (
        not name
        or name in (".", "..")
        or any(c in _FORBIDDEN_CHARS for c in name)
        or name.endswith(".")
        or os.path.splitext(name)[0].lower() in _RESERVED_NAMES
        or len(name) > 200
    ):
        raise Mp3FileError("admin_mp3_err_name")
    return name


def _is_mp3(name: str) -> bool:
    return name.lower().endswith(MP3_EXTENSION)


# --------------------------------------------------------------------------
# Lecture
# --------------------------------------------------------------------------

def list_dir(rel: str) -> dict:
    """Contenu d'un dossier : sous-dossiers puis MP3, tries par nom."""
    path = _safe_path(rel)
    if not os.path.isdir(path):
        raise Mp3FileError("admin_mp3_err_not_found")

    folders, files = [], []
    with os.scandir(path) as entries:
        for entry in entries:
            try:
                if entry.is_dir():
                    folders.append({"name": entry.name, "path": _rel(entry.path)})
                elif entry.is_file() and _is_mp3(entry.name):
                    stat = entry.stat()
                    files.append({
                        "name": entry.name,
                        "path": _rel(entry.path),
                        "size": stat.st_size,
                        "mtime": datetime.fromtimestamp(stat.st_mtime),
                    })
            except OSError:
                continue
    folders.sort(key=lambda f: f["name"].lower())
    files.sort(key=lambda f: f["name"].lower())

    current = _rel(path)
    crumbs, acc = [], []
    for part in current.split("/") if current else []:
        acc.append(part)
        crumbs.append({"name": part, "path": "/".join(acc)})
    return {"path": current, "crumbs": crumbs, "folders": folders, "files": files}


def count_mp3(rel: str) -> int:
    """Nombre de MP3 dans un dossier et ses sous-dossiers (confirmation de suppression)."""
    path = _safe_path(rel)
    return sum(1 for _d, _s, names in os.walk(path) for n in names if _is_mp3(n))


# --------------------------------------------------------------------------
# Catalogue
# --------------------------------------------------------------------------

def _move_catalog_refs(old_rel: str, new_rel: str, is_dir: bool) -> None:
    """Fait suivre MusicTrack.SourceRef apres un renommage/deplacement.
    Un chemin deja pris par un autre morceau (MP3 masque au meme endroit)
    est laisse tel quel : le scan s'en charge."""
    local = MusicTrack.query.filter_by(Source=music_catalog.SOURCE_LOCAL)
    if is_dir:
        tracks = local.filter(MusicTrack.SourceRef.startswith(old_rel + "/", autoescape=True)).all()
        moves = {t: new_rel + t.SourceRef[len(old_rel):] for t in tracks}
    else:
        track = local.filter_by(SourceRef=old_rel).first()
        moves = {track: new_rel} if track else {}
    if not moves:
        return
    taken = {
        ref for (ref,) in db.session.query(MusicTrack.SourceRef).filter(
            MusicTrack.Source == music_catalog.SOURCE_LOCAL,
            MusicTrack.SourceRef.in_(list(moves.values())),
        )
    }
    for track, ref in moves.items():
        if ref not in taken:
            track.SourceRef = ref
    db.session.commit()


def sync_catalog() -> dict:
    return mp3_library_service.scan_library(force=True)


# --------------------------------------------------------------------------
# Operations
# --------------------------------------------------------------------------

def create_folder(parent_rel: str, name: str) -> str:
    target = os.path.join(_safe_path(parent_rel), _check_name(name))
    if os.path.exists(target):
        raise Mp3FileError("admin_mp3_err_exists", name=name)
    os.makedirs(target)
    logger.info("MP3 admin : dossier cree %s", _rel(target))
    return _rel(target)


def rename(rel: str, new_name: str) -> str:
    source = _safe_path(rel)
    if source == root() or not os.path.exists(source):
        raise Mp3FileError("admin_mp3_err_not_found")
    new_name = _check_name(new_name)
    is_dir = os.path.isdir(source)
    if not is_dir and not _is_mp3(new_name):
        new_name += MP3_EXTENSION
    target = os.path.join(os.path.dirname(source), new_name)
    if target == source:
        return rel
    # Changement de casse seule sur un disque insensible a la casse : meme fichier.
    if os.path.exists(target) and not os.path.samefile(source, target):
        raise Mp3FileError("admin_mp3_err_exists", name=new_name)
    return _move(source, target, is_dir)


def move(rel: str, dest_dir_rel: str) -> str:
    source = _safe_path(rel)
    dest_dir = _safe_path(dest_dir_rel)
    if source == root() or not os.path.exists(source):
        raise Mp3FileError("admin_mp3_err_not_found")
    if not os.path.isdir(dest_dir):
        raise Mp3FileError("admin_mp3_err_not_found")
    is_dir = os.path.isdir(source)
    if os.path.dirname(source) == dest_dir:
        return _rel(source)  # deja dans ce dossier
    if is_dir and (dest_dir == source or dest_dir.startswith(source + os.sep)):
        raise Mp3FileError("admin_mp3_err_move_into_self")
    target = os.path.join(dest_dir, os.path.basename(source))
    if os.path.exists(target):
        raise Mp3FileError("admin_mp3_err_exists", name=os.path.basename(source))
    return _move(source, target, is_dir)


def _move(source: str, target: str, is_dir: bool) -> str:
    old_rel, new_rel = _rel(source), _rel(target)
    shutil.move(source, target)
    logger.info("MP3 admin : %s -> %s", old_rel, new_rel)
    _move_catalog_refs(old_rel, new_rel, is_dir)
    return new_rel


def delete(rel: str) -> None:
    path = _safe_path(rel)
    if path == root() or not os.path.exists(path):
        raise Mp3FileError("admin_mp3_err_not_found")
    if os.path.isdir(path):
        shutil.rmtree(path)
    else:
        os.remove(path)
    logger.info("MP3 admin : supprime %s", rel)


def _unique_target(directory: str, name: str) -> str:
    """Pas d'ecrasement silencieux : 'titre.mp3' -> 'titre (1).mp3'..."""
    base, ext = os.path.splitext(name)
    target, n = os.path.join(directory, name), 1
    while os.path.exists(target):
        target = os.path.join(directory, f"{base} ({n}){ext}")
        n += 1
    return target


def save_upload(dest_rel: str, storage, relative_name: str | None = None) -> str | None:
    """Enregistre un MP3 envoye (FileStorage) sous `dest_rel`. `relative_name`
    ('Album/01 titre.mp3') recree les sous-dossiers d'un dossier envoye.
    Renvoie le chemin relatif cree, ou None si le fichier n'est pas un MP3."""
    parts = [p for p in (relative_name or storage.filename or "").replace("\\", "/").split("/") if p]
    if not parts or not _is_mp3(parts[-1]):
        return None
    parts = [_check_name(p) for p in parts]
    directory = _safe_path("/".join([dest_rel.strip("/")] + parts[:-1]).strip("/"))
    os.makedirs(directory, exist_ok=True)
    target = _unique_target(directory, parts[-1])
    storage.save(target)
    logger.info("MP3 admin : envoi %s", _rel(target))
    return _rel(target)


# --------------------------------------------------------------------------
# Tags ID3
# --------------------------------------------------------------------------

def _load_tags(path: str) -> EasyID3:
    try:
        return EasyID3(path)
    except ID3NoHeaderError:
        return EasyID3()


def read_tags(rel: str) -> dict:
    path = _safe_path(rel)
    if not os.path.isfile(path) or not _is_mp3(path):
        raise Mp3FileError("admin_mp3_err_not_found")
    try:
        tags = _load_tags(path)
    except Exception:
        raise Mp3FileError("admin_mp3_err_tags")
    values = {field: (tags.get(field) or [""])[0] for field in TAG_FIELDS}
    try:
        audio = MutagenFile(path)
        values["duration"] = int(audio.info.length) if audio and audio.info else None
    except Exception:
        values["duration"] = None
    return values


def write_tags(rel: str, values: dict, skip_empty: bool = False) -> None:
    """Ecrit les tags d'un MP3. Champ vide : tag supprime, ou ignore si
    `skip_empty` (edition d'un dossier entier : seuls les champs remplis
    sont appliques)."""
    path = _safe_path(rel)
    if not os.path.isfile(path) or not _is_mp3(path):
        raise Mp3FileError("admin_mp3_err_not_found")
    try:
        tags = _load_tags(path)
        for field in TAG_FIELDS:
            if field not in values:
                continue
            value = (values[field] or "").strip()
            if value:
                tags[field] = value
            elif not skip_empty and field in tags:
                del tags[field]
        tags.save(path)
    except Mp3FileError:
        raise
    except Exception:
        logger.exception("MP3 admin : ecriture des tags impossible pour %s", rel)
        raise Mp3FileError("admin_mp3_err_tags")


def write_folder_tags(rel: str, values: dict) -> int:
    """Applique les champs remplis a tous les MP3 du dossier (pas aux
    sous-dossiers). Renvoie le nombre de MP3 modifies."""
    listing = list_dir(rel)
    values = {k: v for k, v in values.items() if (v or "").strip()}
    if not values:
        return 0
    for f in listing["files"]:
        write_tags(f["path"], values, skip_empty=True)
    return len(listing["files"])
