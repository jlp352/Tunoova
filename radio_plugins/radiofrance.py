"""Plug-in "titre en cours" Radio France (France Inter, franceinfo, France
Culture, France Musique, FIP et ses webradios, Mouv').

Source : https://api.radiofrance.fr/livemeta/pull/<station>, le JSON qu'utilise
le lecteur du site (non documente, sans cle). Chaque niveau de "levels"
designe son etape en cours par items[position] : le premier niveau porte
l'emission, un niveau plus profond le morceau ou la sequence diffusee.
Certaines webradios recentes (FIP Pop, Hip-Hop, Sacre francais) n'y existent
pas : repli sur livemeta/live/<station>/transistor_musical_player (format du
lecteur "transistor" : now.firstLine / now.secondLine).

Contrat commun a tous les plug-ins : cf. app/now_playing.py.
"""

import re
import time
from urllib.parse import urlparse

import requests

NAME = "Radio France"

LIVEMETA_URL = "https://api.radiofrance.fr/livemeta/pull/{ref}"
TRANSISTOR_URL = "https://api.radiofrance.fr/livemeta/live/{ref}/transistor_musical_player"
VISUAL_URL = "https://api.radiofrance.fr/v1/services/embed/image/{uuid}?preset=400x400"
TIMEOUT = 6
USER_AGENT = "Tunoova/1.0"

# Nom de la station dans l'URL du flux (icecast.radiofrance.fr/<slug>-hifi.aac)
# -> numero livemeta. Releve station par station sur l'API
# (www.radiofrance.fr/transistor/live/<numero> donne la marque de chaque numero).
STATIONS = {
    "franceinter": "1",
    "franceinfo": "2",
    "francemusique": "4",
    "franceculture": "5",
    "mouv": "6",
    "fip": "7",
    "fiprock": "64",
    "fipjazz": "65",
    "fipgroove": "66",
    "fipworld": "69",
    "fipnouveautes": "70",
    "fipreggae": "71",
    "fipelectro": "74",
    "fipmetal": "77",
    "fippop": "78",
    "fiphiphop": "95",
    "fipsacrefrancais": "96",
}

# Radios prises en charge (nom affiche, Station ID) : liste affichee dans l'Admin
SUPPORTED_STATIONS = [
    ("France Inter", "1"), ("franceinfo", "2"), ("France Musique", "4"), ("France Culture", "5"),
    ("Mouv'", "6"), ("FIP", "7"), ("FIP Rock", "64"), ("FIP Jazz", "65"), ("FIP Groove", "66"),
    ("FIP Monde", "69"), ("FIP Nouveautés", "70"), ("FIP Reggae", "71"), ("FIP Electro", "74"),
    ("FIP Metal", "77"), ("FIP Pop", "78"), ("FIP Hip-Hop", "95"), ("FIP Sacré français", "96"),
]

_HOSTS = ("radiofrance.fr", "franceinter.fr", "franceinfo.fr", "franceculture.fr", "francemusique.fr", "fip.fr", "mouv.fr")


def _slug(text: str) -> str:
    return re.sub(r"[^a-z]", "", text.lower().replace("é", "e").replace("'", ""))


def detect(stream_url: str, name: str) -> str | None:
    host = (urlparse(stream_url).hostname or "").lower()
    if not any(host == h or host.endswith("." + h) for h in _HOSTS):
        return None
    # Dernier segment du chemin : "franceinter-hifi.aac", "fip_hifi.m3u8"...
    segment = urlparse(stream_url).path.rstrip("/").rsplit("/", 1)[-1]
    slug = re.split(r"[-_.]", segment, maxsplit=1)[0]
    return STATIONS.get(slug) or STATIONS.get(_slug(name))


def _visual(step: dict) -> str | None:
    visual = step.get("visual")
    if not visual:
        return None
    return visual if visual.startswith("http") else VISUAL_URL.format(uuid=visual)


def _song_label(step: dict) -> str:
    artists = ", ".join(step.get("highlightedArtists") or []) or step.get("authors") or step.get("performers") or ""
    title = (step.get("title") or "").strip()
    return f"{artists} - {title}" if artists and title else title or artists


def _fetch_transistor(ref: str) -> dict | None:
    resp = requests.get(TRANSISTOR_URL.format(ref=ref), headers={"User-Agent": USER_AGENT}, timeout=TIMEOUT)
    resp.raise_for_status()
    now_item = resp.json().get("now") or {}
    first = (now_item.get("firstLine") or "").strip()
    second = (now_item.get("secondLine") or "").strip()
    cover = now_item.get("cover")
    common = {
        "image_url": VISUAL_URL.format(uuid=cover) if cover else None,
        "start": now_item.get("startTime"),
        "end": now_item.get("endTime"),
    }
    if now_item.get("secondLineSongUuid") and second:  # morceau : "Artiste • Titre"
        return {"kind": "song", "show": second.replace(" • ", " - "), "detail": None, **common}
    if not first:
        return None
    return {"kind": "show", "show": first, "detail": second or None, **common}


def fetch(ref: str, stream_url: str, api_key: str | None) -> dict | None:
    resp = requests.get(LIVEMETA_URL.format(ref=ref), headers={"User-Agent": USER_AGENT}, timeout=TIMEOUT)
    if resp.status_code == 404 or not resp.content.lstrip().startswith(b"{"):
        return _fetch_transistor(ref)  # station absente de livemeta/pull
    resp.raise_for_status()
    data = resp.json()

    steps = data.get("steps") or {}
    now = time.time()
    current = []
    for level in data.get("levels") or []:
        items, position = level.get("items") or [], level.get("position")
        if position is None or not 0 <= position < len(items):
            continue
        step = steps.get(items[position])
        # Etape deja terminee (niveau "morceau" entre deux titres) : ignoree.
        if step and (step.get("end") or now) >= now:
            current.append(step)
    if not current:  # entre deux morceaux, ou grille pas encore a jour
        return _fetch_transistor(ref)

    top, deepest = current[0], current[-1]
    if top.get("embedType") == "song":  # FIP & co : que de la musique
        return {
            "kind": "song",
            "show": _song_label(top),
            "detail": top.get("titreAlbum"),
            "image_url": _visual(top),
            "start": top.get("start"),
            "end": top.get("end"),
        }

    show = (top.get("titleConcept") or top.get("title") or "").strip()
    if deepest is not top:
        detail = _song_label(deepest) if deepest.get("embedType") == "song" else deepest.get("title")
    else:
        detail = top.get("title") if top.get("titleConcept") else None
    return {
        "kind": "show",
        "show": show,
        "detail": detail,
        "image_url": _visual(deepest) or _visual(top),
        "start": top.get("start"),
        "end": top.get("end"),
    }
