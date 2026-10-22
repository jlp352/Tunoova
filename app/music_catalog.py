"""Catalogue musique partage (CDC §8) : MusicArtist -> MusicAlbum ->
MusicTrack, plus le lien utilisateur <-> morceau (MusicUsers).

Point d'entree unique pour creer/retrouver artistes, albums et morceaux, que
la source soit un MP3 local (scan, cf. mp3_library_service) ou YouTube Music
(ajout depuis l'ecran Musique, cf. app/library/routes.py) - les deux
partagent donc les memes artistes/albums, et la meme pochette."""

from datetime import datetime, timedelta

from app.extensions import db
from app.models import MusicArtist, MusicAlbum, MusicTrack, MusicTrackComment, MusicUsers, Play
from app.cover_service import normalize_key, find_album_covers, find_artist_pictures

SOURCE_LOCAL = "local"
SOURCE_YOUTUBE = "youtube"

UNKNOWN_ARTIST = "Unknown Artist"
VARIOUS_ARTISTS = "Various Artists"
_VARIOUS_ARTISTS_KEYS = {normalize_key(n) for n in (VARIOUS_ARTISTS, "Various", "VA", "Artistes divers", "Multi-interpretes")}

# Image introuvable : on ne la recherche plus a chaque passage, seulement
# apres ce delai (le catalogue Deezer/iTunes peut avoir evolue).
IMAGE_RETRY_AFTER = timedelta(days=7)


def is_various_artists(name: str | None) -> bool:
    return normalize_key(name) in _VARIOUS_ARTISTS_KEYS


# --------------------------------------------------------------------------
# Creation / recherche
# --------------------------------------------------------------------------

def get_or_create_artist(name: str | None) -> MusicArtist:
    name = (name or "").strip() or UNKNOWN_ARTIST
    if is_various_artists(name):
        name = VARIOUS_ARTISTS
    key = normalize_key(name) or normalize_key(UNKNOWN_ARTIST)
    artist = MusicArtist.query.filter_by(NameKey=key).first()
    if artist is None:
        artist = MusicArtist(Name=name, NameKey=key)
        db.session.add(artist)
        db.session.flush()
    return artist


def get_or_create_album(artist: MusicArtist, title: str | None, year: int | None = None) -> MusicAlbum | None:
    """Album `title` de `artist` (artiste de l'album, 'Various Artists' pour
    une compilation). None si le morceau n'a pas d'album."""
    title = (title or "").strip()
    key = normalize_key(title)
    if not key:
        return None
    album = MusicAlbum.query.filter_by(ArtistId=artist.ArtistId, TitleKey=key).first()
    if album is None:
        album = MusicAlbum(
            ArtistId=artist.ArtistId,
            Title=title,
            TitleKey=key,
            IsCompilation=artist.Name == VARIOUS_ARTISTS,
            Year=year,
        )
        db.session.add(album)
        db.session.flush()
    elif year and not album.Year:
        album.Year = year
    return album


def get_or_create_track(
    source: str,
    source_ref: str,
    title: str,
    artist_name: str | None,
    album_title: str | None = None,
    album_artist_name: str | None = None,
    track_number: int | None = None,
    duration: int | None = None,
    year: int | None = None,
) -> tuple[MusicTrack, bool]:
    """Renvoie (morceau, cree). `album_artist_name` = tag 'albumartist' (a
    defaut, l'interprete du morceau) : c'est lui qui regroupe les morceaux
    d'une compilation sous un meme album."""
    track = MusicTrack.query.filter_by(Source=source, SourceRef=source_ref).first()
    if track is not None:
        return track, False

    artist = get_or_create_artist(artist_name)
    album_artist = get_or_create_artist(album_artist_name) if album_artist_name else artist
    album = get_or_create_album(album_artist, album_title, year)
    track = MusicTrack(
        ArtistId=artist.ArtistId,
        AlbumId=album.AlbumId if album else None,
        Title=(title or "").strip() or "?",
        TrackNumber=track_number,
        Duration=duration,
        Source=source,
        SourceRef=source_ref,
        MusicLike=0,
        MusicNotLike=0,
        MusicComment=0,
    )
    db.session.add(track)
    db.session.flush()
    return track, True


def update_track(
    track: MusicTrack,
    title: str,
    artist_name: str | None,
    album_title: str | None = None,
    album_artist_name: str | None = None,
    track_number: int | None = None,
    duration: int | None = None,
    year: int | None = None,
) -> bool:
    """Reapplique les tags d'un MP3 modifie a son morceau existant (meme
    TrackId : ecoutes, likes et commentaires sont conserves). Renvoie True si
    l'artiste, l'album ou le titre a change - l'ancien artiste/album peut
    alors etre devenu vide (cf. delete_orphans)."""
    artist = get_or_create_artist(artist_name)
    album_artist = get_or_create_artist(album_artist_name) if album_artist_name else artist
    album = get_or_create_album(album_artist, album_title, year)
    before = (track.ArtistId, track.AlbumId, track.Title)
    track.ArtistId = artist.ArtistId
    track.AlbumId = album.AlbumId if album else None
    track.Title = (title or "").strip() or "?"
    track.TrackNumber = track_number
    track.Duration = duration or track.Duration
    return (track.ArtistId, track.AlbumId, track.Title) != before


def delete_orphans() -> None:
    """Supprime les albums sans morceau et les artistes sans morceau ni album
    (apres une purge, ou des tags corriges qui ont deplace des morceaux)."""
    used_albums = db.session.query(MusicTrack.AlbumId).filter(MusicTrack.AlbumId.isnot(None))
    MusicAlbum.query.filter(MusicAlbum.AlbumId.notin_(used_albums)).delete(synchronize_session=False)
    used_by_tracks = db.session.query(MusicTrack.ArtistId)
    used_by_albums = db.session.query(MusicAlbum.ArtistId)
    MusicArtist.query.filter(
        MusicArtist.ArtistId.notin_(used_by_tracks), MusicArtist.ArtistId.notin_(used_by_albums)
    ).delete(synchronize_session=False)


def get_user_track(users_id: int, track_id: int, create: bool = True, status: str | None = None) -> MusicUsers | None:
    """Lien utilisateur <-> morceau, cree a la premiere interaction (ecoute,
    like, partage...) - pas de ligne pour les morceaux jamais touches."""
    row = MusicUsers.query.filter_by(UsersId=users_id, TrackId=track_id).first()
    if row is None and create:
        row = MusicUsers(UsersId=users_id, TrackId=track_id, CountListen=0, MusicStatus=status)
        db.session.add(row)
        db.session.flush()
    return row


def delete_user_comments(users_id: int) -> None:
    """Suppression d'un compte : retire ses commentaires et met a jour le
    compteur MusicTrack.MusicComment des morceaux concernes."""
    counts = (
        db.session.query(MusicTrackComment.TrackId, db.func.count())
        .filter(MusicTrackComment.UsersId == users_id)
        .group_by(MusicTrackComment.TrackId)
        .all()
    )
    for track_id, count in counts:
        track = db.session.get(MusicTrack, track_id)
        if track is not None:
            track.MusicComment = max(0, (track.MusicComment or 0) - count)
    MusicTrackComment.query.filter_by(UsersId=users_id).delete(synchronize_session=False)


def new_track_ids(users_id: int, track_ids) -> set[int]:
    """Parmi `track_ids`, ceux marques 'New' pour cet utilisateur (badge)."""
    track_ids = list(track_ids)
    if not track_ids:
        return set()
    return {
        tid
        for (tid,) in db.session.query(MusicUsers.TrackId).filter(
            MusicUsers.UsersId == users_id, MusicUsers.MusicStatus == "New", MusicUsers.TrackId.in_(track_ids)
        )
    }


def notify_new_tracks(tracks: list[MusicTrack]) -> int:
    """Nouveaux MP3 (scan) : notification 'New' uniquement pour les
    utilisateurs qui ecoutent deja cet artiste (au moins un morceau dans leur
    MusicUsers) - pas tout le catalogue pour tout le monde."""
    if not tracks:
        return 0
    artist_ids = {t.ArtistId for t in tracks}
    followers: dict[int, set[int]] = {}
    rows = (
        db.session.query(MusicUsers.UsersId, MusicTrack.ArtistId)
        .join(MusicTrack, MusicUsers.TrackId == MusicTrack.TrackId)
        .filter(MusicTrack.ArtistId.in_(artist_ids))
        .distinct()
    )
    for users_id, artist_id in rows:
        followers.setdefault(artist_id, set()).add(users_id)

    created = 0
    for track in tracks:
        for users_id in followers.get(track.ArtistId, ()):
            if get_user_track(users_id, track.TrackId, create=False) is None:
                get_user_track(users_id, track.TrackId, status="New")
                created += 1
    return created


# --------------------------------------------------------------------------
# Purge des MP3 disparus (job mensuel 'music_purge')
# --------------------------------------------------------------------------

# Un MP3 masque depuis moins longtemps est garde : laisse le temps de
# remettre un fichier deplace/renomme par erreur avant de perdre son historique.
PURGE_MIN_MISSING = timedelta(days=30)


def purge_missing_tracks() -> int:
    """Supprime definitivement les MP3 masques (IsAvailable=False) depuis plus
    de PURGE_MIN_MISSING, avec leurs liens utilisateurs, puis les albums et
    artistes devenus vides. Renvoie le nombre de morceaux supprimes."""
    cutoff = datetime.utcnow() - PURGE_MIN_MISSING
    track_ids = [
        tid
        for (tid,) in db.session.query(MusicTrack.TrackId).filter(
            MusicTrack.Source == SOURCE_LOCAL,
            MusicTrack.IsAvailable.is_(False),
            MusicTrack.MissingSince < cutoff,
        )
    ]
    if not track_ids:
        return 0

    MusicUsers.query.filter(MusicUsers.TrackId.in_(track_ids)).delete(synchronize_session=False)
    MusicTrackComment.query.filter(MusicTrackComment.TrackId.in_(track_ids)).delete(synchronize_session=False)
    Play.query.filter(Play.PlayType == "Music", Play.ItemId.in_(track_ids)).delete(synchronize_session=False)
    MusicTrack.query.filter(MusicTrack.TrackId.in_(track_ids)).delete(synchronize_session=False)
    delete_orphans()

    db.session.commit()
    return len(track_ids)


# --------------------------------------------------------------------------
# Images (pochettes / photos d'artiste)
# --------------------------------------------------------------------------

def _needs_lookup(checked_at: datetime | None, now: datetime) -> bool:
    return checked_at is None or checked_at < now - IMAGE_RETRY_AFTER


def ensure_images(albums=(), artists=(), limit: int = 30) -> None:
    """Cherche (Deezer/iTunes, en parallele) les pochettes et photos
    d'artiste encore inconnues, plafonne a `limit` recherches de chaque type.
    Appele sans plafond par le scan planifie, et avec un petit plafond par
    les ecrans (secours : morceaux YouTube, MP3 ajoute entre deux scans)."""
    now = datetime.utcnow()

    todo_albums = []
    for album in dict.fromkeys(a for a in albums if a is not None):
        if not album.CoverUrl and _needs_lookup(album.CheckedAt, now):
            todo_albums.append(album)
    todo_albums = todo_albums[:limit]

    todo_artists = []
    for artist in dict.fromkeys(a for a in artists if a is not None):
        if artist.Name in (VARIOUS_ARTISTS, UNKNOWN_ARTIST):
            continue
        if not artist.PictureUrl and _needs_lookup(artist.CheckedAt, now):
            todo_artists.append(artist)
    todo_artists = todo_artists[:limit]

    if not todo_albums and not todo_artists:
        return

    album_key = {a.AlbumId: (None if a.IsCompilation else a.artist.Name, a.Title) for a in todo_albums}
    covers = find_album_covers(album_key.values())
    pictures = find_artist_pictures([a.Name for a in todo_artists])

    for album in todo_albums:
        key = album_key[album.AlbumId]
        if key in covers:  # absent = echec reseau, on retentera
            album.CoverUrl = covers[key]
            album.CheckedAt = now
    for artist in todo_artists:
        if artist.Name in pictures:
            artist.PictureUrl = pictures[artist.Name]
            artist.CheckedAt = now
    db.session.commit()


def ensure_all_images() -> None:
    """Toutes les images manquantes du catalogue (job de scan planifie)."""
    now = datetime.utcnow()
    stale = now - IMAGE_RETRY_AFTER
    albums = MusicAlbum.query.filter(
        MusicAlbum.CoverUrl.is_(None), db.or_(MusicAlbum.CheckedAt.is_(None), MusicAlbum.CheckedAt < stale)
    ).all()
    artists = MusicArtist.query.filter(
        MusicArtist.PictureUrl.is_(None), db.or_(MusicArtist.CheckedAt.is_(None), MusicArtist.CheckedAt < stale)
    ).all()
    ensure_images(albums, artists, limit=max(len(albums), len(artists), 1))
