"""Plug-in "titre en cours" du groupe NRJ : NRJ, Nostalgie, Rire & Chansons
et leurs webradios (plus de 300).

Source : https://www.<site>/onair.json, le JSON qu'utilise le lecteur de
chaque site (non documente, sans cle) : liste des radios du site avec leurs
flux et le morceau en cours (playlist[0], fin en end_timestamp).

Station ID = "<site>:<id>" (ex. "nostalgie.fr:197"). La detection compare
l'URL du flux de la radio a celles du JSON (streaming.nrjaudio.fm/<code>),
puis, a defaut, son nom.

Cherie FM n'est pas prise en charge : cheriefm.fr est protege par un defi
Cloudflare (JavaScript obligatoire), y compris pour onair.json.

Contrat commun a tous les plug-ins : cf. app/now_playing.py.
"""

import re
import time
from urllib.parse import urlparse

import requests

NAME = "Groupe NRJ"

ONAIR_URL = "https://www.{site}/onair.json"
SITES = ("nrj.fr", "nostalgie.fr", "rireetchansons.fr")
TIMEOUT = 8
USER_AGENT = "Mozilla/5.0 (compatible; Tunoova/1.0)"
FEED_TTL = 25  # onair.json sert toutes les radios d'un site : un seul appel pour plusieurs auditeurs
PLACEHOLDER_SONG_ID = 99999999999999  # "NRJ en direct : Hit Music Only" : pas un morceau

# Radios prises en charge (nom affiche, Station ID) : liste affichee dans l'Admin.
# Les webradios des trois sites sont reconnues aussi, par l'URL de leur flux.
SUPPORTED_STATIONS = [
    ("NRJ", "nrj.fr:158"),
    ("Nostalgie", "nostalgie.fr:197"),
    ("Rire & Chansons", "rireetchansons.fr:200"),
    ("Webradios NRJ, Nostalgie, Rire & Chansons", "auto"),
]

_STREAM_FIELDS = ("url_64k_aac", "url_128k_mp3", "url_hd_aac")
_feeds: dict[str, tuple[float, list]] = {}  # site -> (expiration, radios)


def _feed(site: str) -> list:
    cached = _feeds.get(site)
    if cached and time.time() < cached[0]:
        return cached[1]
    resp = requests.get(ONAIR_URL.format(site=site),
                        headers={"User-Agent": USER_AGENT, "Accept": "application/json"}, timeout=TIMEOUT)
    resp.raise_for_status()
    radios = resp.json()
    if not isinstance(radios, list):
        raise ValueError("onair.json : liste attendue")
    _feeds[site] = (time.time() + FEED_TTL, radios)
    return radios


def _stream_code(url: str) -> str:
    """streaming.nrjaudio.fm/ouwgwqsk6j4d?origine=... -> "ouwgwqsk6j4d"."""
    return urlparse(url or "").path.rstrip("/").rsplit("/", 1)[-1].lower()


def _name_key(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", name.lower().replace("&", "et"))


def detect(stream_url: str, name: str) -> str | None:
    host = (urlparse(stream_url).hostname or "").lower()
    from_nrjaudio = host == "nrjaudio.fm" or host.endswith(".nrjaudio.fm")
    key = _name_key(name)
    # Ni flux NRJ Audio, ni nom de radio du groupe : inutile d'interroger les sites.
    if not from_nrjaudio and not key.startswith(("nrj", "nostalgie", "rireetchansons")):
        return None
    code = _stream_code(stream_url) if from_nrjaudio else None
    by_name = None
    for site in SITES:
        try:
            radios = _feed(site)
        except (requests.RequestException, ValueError):
            continue
        for radio in radios:
            if code and any(_stream_code(radio.get(field)) == code for field in _STREAM_FIELDS):
                return f"{site}:{radio['id']}"
            if by_name is None and _name_key(radio.get("name") or "") == key:
                by_name = f"{site}:{radio['id']}"
    return by_name


def fetch(ref: str, stream_url: str, api_key: str | None) -> dict | None:
    site, _, radio_id = ref.partition(":")
    if site not in SITES or not radio_id:
        return None
    radio = next((r for r in _feed(site) if str(r.get("id")) == radio_id), None)
    if radio is None:
        return None
    current = (radio.get("playlist") or [{}])[0]
    song = current.get("song") or {}
    if song.get("id") == PLACEHOLDER_SONG_ID:
        return None
    artist = (song.get("artist") or "").strip()
    title = (song.get("title") or "").strip()
    if not title:
        return None
    return {
        "kind": "song",
        "show": f"{artist} - {title}" if artist else title,
        "detail": None,
        "image_url": song.get("img_url"),
        "start": None,
        "end": current.get("end_timestamp"),
    }
