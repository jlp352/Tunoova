"""Recherche des pochettes d'album et des photos d'artiste (CDC §10,
Doc/Documentation technique.md).

Strategie : Deezer en principal (pochettes + photos d'artiste, sans cle API,
~50 requetes / 5 s par IP), iTunes Search en secours pour les pochettes que
Deezer ne trouve pas. L'URL trouvee est stockee une fois pour toutes sur
MusicAlbum.CoverUrl / MusicArtist.PictureUrl (cf. music_catalog.ensure_images,
appele surtout par le scan MP3 planifie) : le gabarit affiche ensuite
directement l'image depuis le CDN.

Ce module ne touche pas a la base : il ne fait que des appels reseau, en
parallele, pour pouvoir etre appele depuis n'importe quel thread."""

import re
import threading
import time
import unicodedata
from collections import deque
from concurrent.futures import ThreadPoolExecutor

import requests

DEEZER_ALBUM_SEARCH_URL = "https://api.deezer.com/search/album"
DEEZER_ARTIST_SEARCH_URL = "https://api.deezer.com/search/artist"
ITUNES_SEARCH_URL = "https://itunes.apple.com/search"
USER_AGENT = "Tunoova/1.0 (homelab personal media app)"
REQUEST_TIMEOUT = 5
MAX_WORKERS = 6

# Deezer : ~50 requetes / 5 s par IP - on reste en dessous avec de la marge.
DEEZER_WINDOW_SECONDS = 5.0
DEEZER_MAX_PER_WINDOW = 40

_deezer_calls: deque[float] = deque()
_deezer_lock = threading.Lock()


class LookupFailed(Exception):
    """Erreur reseau / quota : a distinguer d'un 'introuvable' pour ne pas
    memoriser comme introuvable un simple incident passager."""


# --------------------------------------------------------------------------
# Utilitaires
# --------------------------------------------------------------------------

def normalize_key(value: str | None) -> str:
    """Minuscules, sans accents, sans mentions entre parentheses/crochets
    ('(Remastered)', '[Deluxe Edition]'...) ni ponctuation - sert aussi de cle
    d'unicite aux artistes/albums du catalogue (cf. music_catalog)."""
    value = unicodedata.normalize("NFKD", value or "")
    value = "".join(c for c in value if not unicodedata.combining(c)).lower()
    value = re.sub(r"[\(\[].*?[\)\]]", " ", value)
    value = value.replace("&", " and ")
    value = re.sub(r"[^a-z0-9]+", " ", value)
    return " ".join(value.split())


def _artist_matches(wanted: str, found: str) -> bool:
    wanted, found = normalize_key(wanted), normalize_key(found)
    if not wanted or not found:
        return False
    return wanted == found or wanted.startswith(found + " ") or found.startswith(wanted + " ")


def _pick_album(candidates, artist: str | None, album: str):
    """Choisit la meilleure correspondance parmi (titre, artiste, url) :
    titre exact d'abord, puis titre qui commence pareil (editions deluxe,
    remasters...), toujours chez le bon artiste. Sans artiste (compilation),
    seul un titre exact est accepte."""
    wanted_album = normalize_key(album)
    candidates = [c for c in candidates if c[2]]
    if artist:
        candidates = [c for c in candidates if _artist_matches(artist, c[1])]
    for title, _, url in candidates:
        if normalize_key(title) == wanted_album:
            return url
    if not artist:
        return None
    for title, _, url in candidates:
        found = normalize_key(title)
        if found and (found.startswith(wanted_album) or wanted_album.startswith(found)):
            return url
    return None


def _search_terms(*parts: str | None) -> str:
    """Sans les mentions '(Remastered)', '[Deluxe]'... qui font rater la recherche."""
    text = " ".join(p for p in parts if p)
    return " ".join(re.sub(r"[\(\[].*?[\)\]]", " ", text).split())


def _get_json(url: str, params: dict) -> dict:
    try:
        resp = requests.get(url, params=params, headers={"User-Agent": USER_AGENT}, timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
        return resp.json()
    except (requests.RequestException, ValueError) as exc:
        raise LookupFailed(str(exc)) from exc


def _deezer_get(url: str, params: dict) -> dict:
    with _deezer_lock:
        now = time.monotonic()
        while _deezer_calls and now - _deezer_calls[0] > DEEZER_WINDOW_SECONDS:
            _deezer_calls.popleft()
        if len(_deezer_calls) >= DEEZER_MAX_PER_WINDOW:
            time.sleep(DEEZER_WINDOW_SECONDS - (now - _deezer_calls[0]))
        _deezer_calls.append(time.monotonic())
    data = _get_json(url, params)
    if "error" in data:  # Deezer renvoie un HTTP 200 avec {"error": ...} en cas de quota
        raise LookupFailed(str(data["error"]))
    return data


def _is_real_deezer_image(url: str | None) -> bool:
    # Sans image, Deezer renvoie une URL avec un hash vide (".../images/cover//...").
    return bool(url) and "//" not in url.split("://", 1)[-1]


# --------------------------------------------------------------------------
# Recherche
# --------------------------------------------------------------------------

def _deezer_album_cover(artist: str | None, album: str) -> str | None:
    data = _deezer_get(DEEZER_ALBUM_SEARCH_URL, {"q": _search_terms(artist, album), "limit": 10})
    candidates = [
        (a.get("title"), (a.get("artist") or {}).get("name"), a.get("cover_xl") or a.get("cover_big"))
        for a in data.get("data") or []
    ]
    candidates = [c for c in candidates if _is_real_deezer_image(c[2])]
    return _pick_album(candidates, artist, album)


def _itunes_album_cover(artist: str | None, album: str) -> str | None:
    data = _get_json(ITUNES_SEARCH_URL, {"term": _search_terms(artist, album), "entity": "album", "limit": 10})
    candidates = [
        (r.get("collectionName"), r.get("artistName"), r.get("artworkUrl100"))
        for r in data.get("results") or []
    ]
    url = _pick_album(candidates, artist, album)
    # L'URL iTunes est editable : 100x100bb -> 1000x1000bb (cf. sized()).
    return sized(url, 1000) if url else None


def find_album_cover(artist: str | None, album: str) -> str | None:
    """URL de la pochette de l'album, Deezer puis iTunes. `artist` None pour
    une compilation (recherche sur le seul titre). Renvoie None si
    introuvable ; leve LookupFailed si une source n'a pas pu repondre."""
    if not album:
        return None
    failed = False
    for lookup in (_deezer_album_cover, _itunes_album_cover):
        try:
            url = lookup(artist, album)
        except LookupFailed:
            failed = True
            continue
        if url:
            return url
    if failed:
        raise LookupFailed(f"{artist} - {album}")
    return None


def find_artist_picture(artist: str) -> str | None:
    """URL de la photo d'artiste (Deezer uniquement : iTunes n'en fournit pas)."""
    if not artist:
        return None
    data = _deezer_get(DEEZER_ARTIST_SEARCH_URL, {"q": artist, "limit": 5})
    for a in data.get("data") or []:
        url = a.get("picture_xl") or a.get("picture_big")
        if _is_real_deezer_image(url) and normalize_key(a.get("name")) == normalize_key(artist):
            return url
    return None


def _run_parallel(func, keys) -> dict:
    """Appelle func(*key) en parallele pour chaque cle. Resultat : cle -> URL
    (ou None si introuvable) ; les cles en echec (reseau/quota) sont absentes,
    pour que l'appelant retente plus tard au lieu de conclure 'introuvable'."""
    keys = list(dict.fromkeys(keys))
    if not keys:
        return {}

    def _lookup(key):
        try:
            return key, func(*key), True
        except LookupFailed:
            return key, None, False

    with ThreadPoolExecutor(max_workers=min(MAX_WORKERS, len(keys))) as pool:
        return {key: url for key, url, ok in pool.map(_lookup, keys) if ok}


def find_album_covers(keys) -> dict:
    """Pochettes d'une liste de (artiste ou None, titre d'album), en parallele."""
    return _run_parallel(find_album_cover, keys)


def find_artist_pictures(names) -> dict:
    """Photos d'une liste de noms d'artiste, en parallele (cle = nom)."""
    return {key[0]: url for key, url in _run_parallel(find_artist_picture, [(n,) for n in names]).items()}


# --------------------------------------------------------------------------
# Affichage
# --------------------------------------------------------------------------

def sized(url: str | None, size: int) -> str | None:
    """Adapte la taille d'une URL Deezer ('/1000x1000-000000-...') ou iTunes
    ('/100x100bb.jpg') : les deux CDN generent l'image a la taille demandee."""
    if not url:
        return None
    return re.sub(r"/\d+x\d+(?=[-b.])", f"/{size}x{size}", url, count=1)


def cover_for(item, size: int = 250) -> str | None:
    """URL de l'image a afficher pour un MusicArtist, MusicAlbum, MusicTrack
    ou MusicUsers : pochette de l'album pour un album ou un morceau, et a
    defaut (album introuvable sur Deezer/iTunes, morceau sans album) photo de
    l'artiste."""
    if item is None:
        return None
    track = getattr(item, "track", None)  # MusicUsers -> MusicTrack
    if track is not None:
        item = track
    if hasattr(item, "PictureUrl"):  # MusicArtist
        return sized(item.PictureUrl, size)
    if hasattr(item, "CoverUrl"):  # MusicAlbum : a defaut, photo de son artiste
        if item.CoverUrl:
            return sized(item.CoverUrl, size)
        return sized(item.artist.PictureUrl, size) if item.artist is not None else None
    album = getattr(item, "album", None)
    if album is not None and album.CoverUrl:
        return sized(album.CoverUrl, size)
    artist = getattr(item, "artist", None)
    return sized(artist.PictureUrl, size) if artist is not None else None
