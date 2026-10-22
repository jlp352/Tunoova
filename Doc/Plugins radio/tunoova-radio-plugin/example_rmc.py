"""Plug-in "titre en cours" RMC (RMC, RMC Gold).

Source : https://rmc.bfmtv.com/api/v1/epg/<chaine>/, le JSON qu'utilise le
lecteur du site (non documente, sans cle). "data" decrit l'emission que le
site considere en cours, mais avec du retard au changement d'emission : on
retient d'abord, dans "programmes" (grille du jour), celle dont les horaires
encadrent l'heure actuelle. Pas de morceau en cours (RMC Gold comprise).

Contrat commun a tous les plug-ins : cf. app/now_playing.py.
"""

import re
import time
from urllib.parse import urlparse

import requests

NAME = "RMC"

EPG_URL = "https://rmc.bfmtv.com/api/v1/epg/{ref}/"
TIMEOUT = 6
USER_AGENT = "Mozilla/5.0 (compatible; Tunoova/1.0)"

# Radios prises en charge (nom affiche, Station ID) : liste affichee dans l'Admin
SUPPORTED_STATIONS = [("RMC", "rmc"), ("RMC Gold", "rmc_gold")]

_HOSTS = ("bfmtv.com", "nextradiotv.com")
_NAMES = {"rmc": "rmc", "rmcfr": "rmc", "rmcinfo": "rmc", "rmcinfotalksport": "rmc", "bfmavecrmc": "rmc",
          "rmcgold": "rmc_gold"}


def detect(stream_url: str, name: str) -> str | None:
    parsed = urlparse(stream_url)
    host = (parsed.hostname or "").lower()
    if not any(host == h or host.endswith("." + h) for h in _HOSTS):
        return None
    # hls-rmcgold.nextradiotv.com, hls-rmc.nextradiotv.com, audio.bfmtv.com/rmcradio_128.mp3
    text = host + parsed.path.lower()
    if "rmcgold" in text:
        return "rmc_gold"
    if re.search(r"(^|[^a-z])rmc(radio)?([^a-z]|$)", text):
        return "rmc"
    return _NAMES.get(re.sub(r"[^a-z0-9]", "", name.lower()))


def _current_program(data: dict) -> dict | None:
    now_ms = time.time() * 1000
    programmes = data.get("programmes") or []
    for program in programmes:
        if (program.get("debut") or 0) <= now_ms < (program.get("fin") or 0):
            return program
    return next((p for p in programmes if p.get("is_current_program")), None)


def fetch(ref: str, stream_url: str, api_key: str | None) -> dict | None:
    if ref not in ("rmc", "rmc_gold"):
        return None
    resp = requests.get(EPG_URL.format(ref=ref), headers={"User-Agent": USER_AGENT}, timeout=TIMEOUT)
    resp.raise_for_status()
    data = resp.json().get("data") or {}

    program = _current_program(data)
    if program:
        title = (program.get("titre") or program.get("nom") or "").strip()
        return {
            "kind": "show",
            "show": title,
            "detail": (program.get("presentateur") or "").strip() or None,
            "image_url": program.get("image"),
            "start": int(program["debut"]) // 1000 if program.get("debut") else None,
            "end": int(program["fin"]) // 1000 if program.get("fin") else None,
        }

    # Grille absente : emission annoncee par le lecteur du site
    title = (data.get("titre") or "").strip()
    if not title:
        return None
    start = data.get("timestamp_debut")
    return {
        "kind": "show",
        "show": title,
        "detail": None,
        "image_url": data.get("image"),
        "start": start,
        "end": start + 60 * data["duree"] if start and data.get("duree") else None,
    }
