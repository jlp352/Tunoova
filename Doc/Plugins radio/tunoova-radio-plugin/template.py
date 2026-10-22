"""Plug-in "titre en cours" <Radio ou groupe> (<radios couvertes>).

Source : <URL exacte de l'endpoint>, <origine : JSON du lecteur du site,
API publique...> (<documente ou non>, <avec ou sans cle>). <Ce que contient
la reponse et quel champ est utilise.>

Station ID = <format, ex. slug de la chaine "rmc">. Detection par <domaine du
flux, puis nom de la radio>.

<Limites connues : radios non couvertes, pas de morceau en cours...>

Contrat commun a tous les plug-ins : cf. app/now_playing.py.
"""

import re
import time
from urllib.parse import urlparse

import requests

NAME = "Ma Radio"

API_URL = "https://exemple.com/api/live/{ref}"
TIMEOUT = 6
USER_AGENT = "Mozilla/5.0 (compatible; Tunoova/1.0)"

# Radios prises en charge (nom affiche, Station ID) : liste affichee dans l'Admin
SUPPORTED_STATIONS = [("Ma Radio", "maradio")]

_HOSTS = ("exemple.com",)  # domaines des flux de la radio
_NAMES = {"maradio": "maradio"}  # nom normalise -> Station ID


def _name_key(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", name.lower())


def detect(stream_url: str, name: str) -> str | None:
    # Appele pour toutes les radios du catalogue : strict, rapide, sans reseau.
    host = (urlparse(stream_url).hostname or "").lower()
    if not any(host == h or host.endswith("." + h) for h in _HOSTS):
        return None
    return _NAMES.get(_name_key(name))


def fetch(ref: str, stream_url: str, api_key: str | None) -> dict | None:
    if ref not in {station_id for _, station_id in SUPPORTED_STATIONS}:
        return None
    resp = requests.get(API_URL.format(ref=ref), headers={"User-Agent": USER_AGENT}, timeout=TIMEOUT)
    resp.raise_for_status()
    data = resp.json()

    # Emission dont les horaires encadrent l'heure actuelle (adapter aux champs de la source)
    now = time.time()
    current = next((p for p in data.get("programmes") or []
                    if (p.get("start") or 0) <= now < (p.get("end") or 0)), None)
    if not current:
        return None  # rien a l'antenne : pas une erreur
    title = (current.get("title") or "").strip()
    if not title:
        return None
    return {
        "kind": "show",  # "song" pour un morceau ("Artiste - Titre" dans show)
        "show": title,
        "detail": (current.get("host") or "").strip() or None,
        "image_url": current.get("image"),
        "start": current.get("start"),  # epoch en secondes
        "end": current.get("end"),
    }
