"""Integration YouTube Music (CDC §8, §10) : recherche via ytmusicapi,
extraction du flux audio via yt-dlp au moment de la lecture (proxifiee par
l'appli, cf. CDC §2 - /media/ytmusic/<id>).

Le flux extrait est une URL signee googlevideo.com valable plusieurs heures :
on la met en cache en memoire process le temps de sa validite pour eviter de
relancer une extraction (~2-3s) a chaque clic sur Play."""

import logging
import time

import yt_dlp
from ytmusicapi import YTMusic

logger = logging.getLogger(__name__)
_ytmusic = None
_stream_cache: dict[str, tuple[str, float]] = {}
STREAM_CACHE_TTL = 60 * 30  # 30 min, marge de securite sous la duree de validite reelle


def _client() -> YTMusic:
    global _ytmusic
    if _ytmusic is None:
        _ytmusic = YTMusic()
    return _ytmusic


def _thumbnail(thumbnails):
    return (thumbnails or [{}])[-1].get("url")


def search(query: str, limit: int = 10) -> list[dict]:
    if not query or len(query) < 2:
        return []
    try:
        results = _client().search(query, filter="songs", limit=limit)
    except Exception:
        return []

    songs = []
    for r in results:
        video_id = r.get("videoId")
        if not video_id:
            continue
        songs.append(
            {
                "video_id": video_id,
                "title": r.get("title") or "?",
                "artist": ", ".join(a["name"] for a in (r.get("artists") or [])),
                "album": (r.get("album") or {}).get("name"),
                "thumbnail": _thumbnail(r.get("thumbnails")),
            }
        )
    return songs[:limit]


def artist_channel_id(artist_name: str) -> str | None:
    """Page artiste YouTube Music correspondant a un nom d'artiste du
    catalogue (meilleur resultat de la recherche d'artistes)."""
    if not artist_name:
        return None
    try:
        matches = _client().search(artist_name, filter="artists", limit=1)
    except Exception:
        return None
    return (matches[0].get("browseId") if matches else None) or None


def top_tracks_for_artist(artist_name: str, limit: int = 10) -> list[dict]:
    """Morceaux les plus populaires d'un artiste sur YouTube Music (playlist
    'Top songs' officielle de sa page artiste, CDC §8)."""
    channel_id = artist_channel_id(artist_name)
    if not channel_id:
        return []
    try:
        artist = _client().get_artist(channel_id)
    except Exception:
        return []

    songs = (artist.get("songs") or {}).get("results") or []
    results = []
    for s in songs[:limit]:
        video_id = s.get("videoId")
        if not video_id:
            continue
        results.append(
            {
                "video_id": video_id,
                "title": s.get("title") or "?",
                "artist": ", ".join(a["name"] for a in (s.get("artists") or [])) or artist_name,
                "album": (s.get("album") or {}).get("name"),
                "thumbnail": _thumbnail(s.get("thumbnails")),
            }
        )
    return results


def search_smart(query: str, limit: int = 20) -> dict:
    """Recherche 'intelligente' pour la section YouTube Music de l'ecran
    Musique (CDC §8) : reprend le principe de la recherche locale (Artiste ->
    Album -> Morceaux) en s'appuyant sur le meilleur resultat ('Top result')
    renvoye par la recherche mixte de YouTube Music - pas une correspondance
    garantie, juste le meme classement que YouTube Music utilise lui-meme."""
    if not query or len(query) < 2:
        return {"mode": "songs", "songs": []}
    try:
        results = _client().search(query, limit=limit)
    except Exception:
        return {"mode": "songs", "songs": []}

    top = next((r for r in results if r.get("category") == "Top result"), None)

    if top and top.get("resultType") == "artist":
        artists = top.get("artists") or []
        if artists and artists[0].get("id") and artists[0].get("name"):
            return {
                "mode": "artist",
                "channel_id": artists[0]["id"],
                "name": artists[0]["name"],
                "thumbnail": _thumbnail(top.get("thumbnails")),
            }

    if top and top.get("resultType") == "album" and top.get("browseId"):
        return {
            "mode": "album",
            "browse_id": top["browseId"],
            "title": top.get("title") or "?",
            "artist": ", ".join(a["name"] for a in (top.get("artists") or [])),
            "thumbnail": _thumbnail(top.get("thumbnails")),
        }

    return {"mode": "songs", "songs": search(query, limit=limit)}


def albums_for_artist(channel_id: str, limit: int = 100) -> list[dict]:
    """Albums d'un artiste YouTube Music (CDC §8), pour le niveau 2 de la
    hierarchie Artiste -> Album -> Morceaux cote recherche YouTube, du plus
    recent au plus ancien. La page artiste n'en donne qu'une dizaine : la
    liste complete demande un appel de plus (get_artist_albums). L'annee est
    celle de la parution sur YouTube Music (souvent une reedition)."""
    if not channel_id:
        return []
    client = _client()
    try:
        section = client.get_artist(channel_id).get("albums") or {}
    except Exception:
        return []

    albums = section.get("results") or []
    if section.get("browseId") and section.get("params"):
        try:
            albums = client.get_artist_albums(section["browseId"], section["params"], limit=limit) or albums
        except Exception:
            pass  # on garde l'apercu de la page artiste

    results = []
    for a in albums[:limit]:
        browse_id = a.get("browseId")
        if not browse_id:
            continue
        year = str(a.get("year") or "")
        results.append(
            {
                "browse_id": browse_id,
                "title": a.get("title") or "?",
                "year": int(year) if year.isdigit() else None,
                "thumbnail": _thumbnail(a.get("thumbnails")),
            }
        )
    # Tri stable : a annee egale, on garde l'ordre de YouTube Music.
    results.sort(key=lambda a: -(a["year"] or 0))
    return results


def tracks_for_album(browse_id: str) -> dict | None:
    """Morceaux d'un album YouTube Music (CDC §8), niveau 3 de la hierarchie
    cote recherche YouTube."""
    if not browse_id:
        return None
    try:
        album = _client().get_album(browse_id)
    except Exception:
        return None
    if not album:
        return None

    artist_name = ", ".join(a["name"] for a in (album.get("artists") or []))
    album_thumbnail = _thumbnail(album.get("thumbnails"))
    tracks = []
    for t in album.get("tracks") or []:
        video_id = t.get("videoId")
        if not video_id:
            continue
        tracks.append(
            {
                "video_id": video_id,
                "title": t.get("title") or "?",
                "artist": ", ".join(a["name"] for a in (t.get("artists") or [])) or artist_name,
                "album": album.get("title"),
                "thumbnail": _thumbnail(t.get("thumbnails")) or album_thumbnail,
            }
        )
    return {"title": album.get("title") or "?", "artist": artist_name, "tracks": tracks}


# "bestaudio"/"worstaudio" bruts choisissent le plus souvent un flux WebM/Opus
# (ex. itag 251) : lu sans probleme par un navigateur, mais illisible par les
# enceintes Bose SoundTouch en DLNA (Cast, app/cast_service.py - pas de
# decodeur Opus), qui elles ne savent lire que MP3/AAC/WMA/FLAC/WAV. On force
# donc un flux m4a/AAC (itag 140 quasi toujours disponible, cf. la radio AAC
# qui elle diffuse deja sans probleme), avec repli sur le meilleur flux audio
# si aucun m4a n'existe pour cette video.
_QUALITY_FORMATS = {
    "low": "worstaudio[ext=m4a]/worstaudio",
    "high": "bestaudio[ext=m4a]/bestaudio",
    "auto": "bestaudio[ext=m4a]/bestaudio",
}


def get_stream_url(video_id: str, quality: str = "auto", refresh: bool = False) -> str | None:
    """`refresh` : ignore le cache - googlevideo peut revoquer une URL bien
    avant STREAM_CACHE_TTL (403 constate en pratique), elle resterait sinon
    servie depuis le cache jusqu'a expiration."""
    cache_key = f"{video_id}:{quality}"
    cached = _stream_cache.get(cache_key)
    if cached and cached[1] > time.monotonic() and not refresh:
        return cached[0]

    ydl_opts = {"format": _QUALITY_FORMATS.get(quality, "bestaudio/best"), "quiet": True, "noplaylist": True}
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(f"https://music.youtube.com/watch?v={video_id}", download=False)
            url = info["url"]
    except Exception as exc:
        logger.warning("YouTube Music : extraction du flux %s en echec : %s", video_id, exc)
        return None

    _stream_cache[cache_key] = (url, time.monotonic() + STREAM_CACHE_TTL)
    return url
