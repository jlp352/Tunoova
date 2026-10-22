"""Titre en cours lu dans le flux lui-meme (metadonnees ICY) : repli de
app/now_playing.py pour toute radio sans plug-in, ou dont le plug-in echoue.

Les serveurs Icecast/Shoutcast glissent, si on le demande (en-tete
Icy-MetaData), un bloc "StreamTitle='...';" tous les icy-metaint octets
d'audio. Beaucoup de flux n'en envoient pas (Radio France, RTL...) : ils sont
alors ecartes pendant UNSUPPORTED_TTL, sans rouvrir le flux a chaque
rafraichissement du lecteur.
"""

import re
import time

import requests

USER_AGENT = "Tunoova/1.0"
TIMEOUT = 4
READ_SECONDS = 5  # le titre peut n'arriver qu'apres un premier bloc vide (pub, preroll)
UNSUPPORTED_TTL = 3600

_TITLE_RE = re.compile(r"StreamTitle='(.*?)';(?=[A-Za-z_]+=|\s*$)", re.S)
_unsupported: dict[str, float] = {}  # stream_url -> expiration


def _decode(raw: bytes) -> str:
    raw = raw.rstrip(b"\0")
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return raw.decode("latin-1")


def clean_title(meta: str) -> str | None:
    if "adw_ad='true'" in meta:  # bloc publicitaire (NRJ Group...)
        return None
    match = _TITLE_RE.search(meta)
    title = match.group(1).strip(" -") if match else ""
    # Valeurs bouche-trou vues en pratique : "", "-", "9999999 - 9999999"...
    if not re.search(r"[^\W\d_]", title):
        return None
    # "nrjaudio_832 - nrjaudio_832" : identifiant technique repete, pas un titre
    parts = [part.strip() for part in title.split(" - ")]
    if len(parts) == 2 and parts[0].lower() == parts[1].lower():
        return None
    return title


def _read(stream_url: str) -> tuple[str | None, bool]:
    """(titre, flux compatible ICY)."""
    try:
        resp = requests.get(
            stream_url,
            headers={"Icy-MetaData": "1", "User-Agent": USER_AGENT},
            timeout=TIMEOUT,
            stream=True,
        )
    except requests.RequestException:
        return None, False
    try:
        metaint = int(resp.headers.get("icy-metaint") or 0)
        if resp.status_code != 200 or metaint <= 0:
            return None, False
        deadline = time.time() + READ_SECONDS
        while time.time() < deadline:
            if len(resp.raw.read(metaint)) < metaint:
                break
            length = resp.raw.read(1)
            if not length:
                break
            if length[0]:
                title = clean_title(_decode(resp.raw.read(length[0] * 16)))
                if title:
                    return title, True
        return None, True
    except (requests.RequestException, OSError, ValueError):
        return None, False
    finally:
        resp.close()


def _key(text: str) -> str:
    return re.sub(r"[^a-z0-9]", "", text.lower())


def now_playing(stream_url: str, station_name: str = "") -> dict | None:
    """Titre ICY au format standard des plug-ins (cf. app/now_playing.py).
    Un titre qui ne fait que reprendre le nom de la station ("Classic21",
    "EUROPE 2 - POP RADIO") n'apporte rien : ignore."""
    expires = _unsupported.get(stream_url)
    if expires and time.time() < expires:
        return None
    title, supported = _read(stream_url)
    if not supported:
        _unsupported[stream_url] = time.time() + UNSUPPORTED_TTL
        return None
    if not title:
        return None
    name, text = _key(station_name), _key(title)
    if name and (text in name or text.startswith(name)):
        return None
    return {"kind": "song", "show": title}
