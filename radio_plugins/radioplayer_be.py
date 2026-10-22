"""Plug-in "titre en cours" Radioplayer Belgique : RTBF (La Premiere,
Vivacite, Classic 21, Musiq3, Tipik, Jam, Tarmac et leurs webradios) et
plus de 200 autres radios belges (VRT, bel RTL, Radio Contact, Nostalgie /
NRJ / Cherie Belgique...).

Sources (API publiques du lecteur Radioplayer, sans cle) :
  - liste des radios : mapi-prod.radioplayer.co.uk/api/be/public/services
  - fiche d'une radio (flux) : .../public/service/<rpId>
  - en cours : core-search.radioplayer.cloud/056/qp/v4/events/?rpId=<rpId>
    ("now" = morceau ou sequence, "pis" = emission)

Station ID = rpId Radioplayer (ex. "2" = Classic 21). Detection par le nom
de la radio, confirmee par le domaine de son flux (meme domaine que le flux
publie par Radioplayer, ou domaine belge en .be) : une radio francaise
homonyme (Fun Radio, Nostalgie...) n'est donc pas prise par erreur.

Contrat commun a tous les plug-ins : cf. app/now_playing.py.
"""

import re
import time
from urllib.parse import urlparse

import requests

NAME = "Radioplayer Belgique"

SERVICES_URL = "https://mapi-prod.radioplayer.co.uk/api/be/public/services"
SERVICE_URL = "https://mapi-prod.radioplayer.co.uk/api/be/public/service/{ref}"
EVENTS_URL = "https://core-search.radioplayer.cloud/056/qp/v4/events/?rpId={ref}"
TIMEOUT = 6
USER_AGENT = "Mozilla/5.0 (compatible; Tunoova/1.0)"
SERVICES_TTL = 24 * 3600

# Radios prises en charge (nom affiche, Station ID) : liste affichee dans l'Admin.
# Les autres radios belges de Radioplayer sont reconnues aussi, par leur nom.
SUPPORTED_STATIONS = [
    ("La Première", "1103"), ("Vivacité Bruxelles", "12"), ("Vivacité Liège", "16"),
    ("Vivacité Namur", "14"), ("Vivacité Charleroi", "15"), ("Vivacité Hainaut", "13"),
    ("Vivacité Luxembourg", "17"), ("Vivacité Brabant Wallon", "1110"), ("Classic 21", "2"),
    ("Classic 21 Metal", "29"), ("Classic 21 Blues", "200"), ("Musiq3", "18"), ("Tipik", "11"),
    ("Jam.", "1132"), ("Tarmac", "210"), ("Viva+", "1131"),
    ("Autres radios belges de Radioplayer (VRT, bel RTL, Contact...)", "auto"),
]

# Mots ignores pour comparer les noms ("RTBF Classic 21" = "Classic 21")
_FILLER = {"rtbf", "hd", "hq", "hifi", "belgique", "belgium", "live", "direct"}

_services: tuple[float, list] | None = None
_details: dict[str, dict] = {}


def _get(url: str):
    resp = requests.get(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"}, timeout=TIMEOUT)
    resp.raise_for_status()
    return resp.json()


def _all_services() -> list:
    global _services
    if _services and time.time() < _services[0]:
        return _services[1]
    data = _get(SERVICES_URL)
    services = data.get("undefined") if isinstance(data, dict) else data
    if not isinstance(services, list):
        raise ValueError("services : liste attendue")
    _services = (time.time() + SERVICES_TTL, services)
    return services


def _key(name: str) -> str:
    words = re.findall(r"[a-z0-9]+", name.lower().replace("é", "e").replace("è", "e").replace("ï", "i"))
    return "".join(w for w in words if w not in _FILLER)


def _domain(url: str) -> str:
    host = (urlparse(url or "").hostname or "").lower()
    return ".".join(host.split(".")[-2:])


def detect(stream_url: str, name: str) -> str | None:
    key = _key(name)
    if not key:
        return None
    try:
        candidates = [s for s in _all_services() if _key(s.get("serviceName") or "") == key]
    except (requests.RequestException, ValueError):
        return None
    domain = _domain(stream_url)
    for service in candidates:
        ref = str(service.get("rpId"))
        try:
            detail = _details.get(ref) or _get(SERVICE_URL.format(ref=ref))
            _details[ref] = detail
        except requests.RequestException:
            continue
        stream_domains = {_domain(s.get("url")) for s in detail.get("streams") or []}
        if domain in stream_domains or domain.endswith(".be"):
            return ref
    return None


def fetch(ref: str, stream_url: str, api_key: str | None) -> dict | None:
    results = (_get(EVENTS_URL.format(ref=ref)) or {}).get("results") or {}
    now_item = results.get("now") or {}
    programme = next(iter(results.get("pis") or []), {})
    programme_name = (programme.get("programmeName") or programme.get("name") or "").strip()

    def epoch(item, key):
        try:
            return int(item.get(key)) if item.get(key) else None
        except (TypeError, ValueError):
            return None

    if now_item.get("song") and now_item.get("name"):
        artist = (now_item.get("artistName") or "").strip()
        title = now_item["name"].strip()
        return {
            "kind": "song",
            "show": f"{artist} - {title}" if artist else title,
            "detail": programme_name or None,
            "image_url": now_item.get("imageUrl") or None,
            "start": epoch(now_item, "startTime"),
            "end": epoch(now_item, "stopTime"),
        }
    item = programme or now_item
    show = programme_name or (now_item.get("name") or "").strip()
    if not show:
        return None
    return {
        "kind": "show",
        "show": show,
        "detail": None,
        "image_url": item.get("imageUrl") or None,
        "start": epoch(item, "startTime"),
        "end": epoch(item, "stopTime"),
    }
