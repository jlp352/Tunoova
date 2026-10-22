"""Plug-in "titre en cours" du groupe RTL (M6) : RTL, RTL2, Fun Radio.

Source : https://www.<site>/ws/live/live, le JSON qu'utilise le lecteur des
sites du groupe (non documente, sans cle) : emission en cours, animateurs,
visuel et horaires (en millisecondes). Pas de morceau en cours : ni ce JSON
ni les flux (pas d'ICY) ne le donnent.

Contrat commun a tous les plug-ins : cf. app/now_playing.py.
"""

import re
from urllib.parse import urlparse

import requests

NAME = "Groupe RTL"

LIVE_URL = "https://www.{site}/ws/live/live"
TIMEOUT = 6
USER_AGENT = "Mozilla/5.0 (compatible; Tunoova/1.0)"

# ref (identifiant stocke sur la radio) -> site du lecteur
SITES = {"rtl": "rtl.fr", "rtl2": "rtl2.fr", "funradio": "funradio.fr"}
# Radios prises en charge (nom affiche, Station ID) : liste affichee dans l'Admin
SUPPORTED_STATIONS = [("RTL", "rtl"), ("RTL2", "rtl2"), ("Fun Radio", "funradio")]

_HOSTS = ("rtl.fr", "rtl2.fr", "funradio.fr", "m6radio.quortex.io")
# Mot du chemin du flux -> station (icecast.rtl.fr/rtl-1-44-128,
# .../grouprtl/national/..., hls.rtl2.fr/.../national/rtl2/..., fun-1-44-128)
_PATH_TOKENS = {"rtl": "rtl", "grouprtl": "rtl", "rtl2": "rtl2", "grouprtl2": "rtl2",
                "fun": "funradio", "funradio": "funradio", "groupfun": "funradio"}
_NAMES = {"rtl": "rtl", "rtlfrance": "rtl", "rtl2": "rtl2", "funradio": "funradio", "funradiofrance": "funradio"}


def detect(stream_url: str, name: str) -> str | None:
    parsed = urlparse(stream_url)
    host = (parsed.hostname or "").lower()
    if not any(host == h or host.endswith("." + h) for h in _HOSTS):
        return None
    path = parsed.path.lower()
    if "webradio" in path:  # webradios thematiques (Le Grand Studio RTL...) : pas de programme
        return None
    tokens = re.split(r"[/_.]", path)
    for token in tokens:
        # "rtl-1-44-128", "fun-radio" : on ne garde que le nom avant les chiffres
        key = re.sub(r"-\d.*$", "", token).replace("-", "")
        if key in _PATH_TOKENS:
            return _PATH_TOKENS[key]
    for site_key, site in SITES.items():
        if host == site or host.endswith("." + site):
            return site_key
    return _NAMES.get(re.sub(r"[^a-z0-9]", "", name.lower()))


def fetch(ref: str, stream_url: str, api_key: str | None) -> dict | None:
    site = SITES.get(ref)
    if site is None:
        return None
    resp = requests.get(LIVE_URL.format(site=site), headers={"User-Agent": USER_AGENT}, timeout=TIMEOUT)
    resp.raise_for_status()
    data = resp.json()

    title = (data.get("title") or "").strip()
    if not title:
        return None
    schedule = data.get("schedule") or {}

    def seconds(value):
        return int(value) // 1000 if value else None

    return {
        "kind": "show",
        "show": title,
        "detail": (data.get("hosts") or "").strip() or None,
        "image_url": data.get("cover"),
        "start": seconds(schedule.get("start")),
        "end": seconds(schedule.get("end")),
    }
