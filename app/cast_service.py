"""Couche de diffusion (Cast) vers des enceintes reseau (CDC §7/§8/§10).

Architecture retenue (solution hybride, cf. la "note technique"
Doc/Streaming-dlna-soundtouch (1).md, retiree du depot - historique git
avant e9fe675) :
- Bose SoundTouch : decouverte mDNS (_soundtouch._tcp.local.) + Web API 8090
  (libsoundtouch) pour le confort (volume) ; le contenu lui-meme est pousse en
  DLNA/UPnP AVTransport (seule voie fiable pour une URL perso), via la
  description UPnP du meme appareil recuperee en SSDP (cf. scan_devices).
- Renderer DLNA generique : decouverte SSDP + AVTransport (async-upnp-client),
  meme mecanisme de diffusion que pour le SoundTouch, volume DLNA natif.

Deux etapes :
1. Decouverte reseau (job planifie cast_scan, app/scheduler.py, toutes les
   5 min par defaut), qui alimente la table CastDevice.
2. Controle de la lecture (play/pause/volume, enchainement de piste par
   polling GetPositionInfo - cf. §3 de la note technique), pilote depuis
   app/cast/routes.py. Les deux etapes partagent le meme event loop asyncio
   de fond (AiohttpRequester/DmrDevice sont asyncio, Flask/APScheduler non).
"""

import asyncio
import ipaddress
import logging
import socket
import threading
import time
from datetime import datetime, timedelta
from urllib.parse import urlsplit

logger = logging.getLogger(__name__)

SOUNDTOUCH_DEVICE_TYPE = "Soundtouch"
DLNA_DEVICE_TYPE = "Dlna"

# Un Bose SoundTouch repond aussi au SSDP generique (urn:...:MediaRenderer:1),
# avec sa propre description UPnP (necessaire pour piloter son AVTransport) :
# on la recupere donc quand meme, mais pour completer l'entree SoundTouch
# existante (decouverte par mDNS) plutot que de creer un deuxieme CastDevice
# pour le meme appareil physique - cf. scan_devices().
_SOUNDTOUCH_MANUFACTURER_HINTS = ("bose",)

# Nettoyage auto (job cast_scan) : un device non revu depuis ce delai est
# supprime de CastDevice plutot que d'encombrer la fenetre Cast en "hors ligne".
STALE_DEVICE_MAX_AGE = timedelta(hours=24)

# ssdp:all plutot qu'un search_target MediaRenderer precis : certains
# renderers (dont Bose, cf. "Renderer non conforme UPnP AV" dans la note
# technique) ne repondent pas de facon fiable a un search_target cible - on
# filtre plutot a posteriori sur le device_type lu dans la description UPnP.
DLNA_SEARCH_TARGET = "ssdp:all"
DLNA_SCAN_TIMEOUT_SECONDS = 4
SOUNDTOUCH_SCAN_TIMEOUT_SECONDS = 3

# Enchainement de piste (cf. §3 de la note technique) : pas de gapless fiable
# sur SoundTouch, transition manuelle par polling de GetPositionInfo.
CAST_POLL_SECONDS = 3
# Delai de grace apres un SetAVTransportURI : le statut peut rester
# brievement STOPPED/NO_MEDIA_PRESENT pendant le changement de source, ce
# n'est pas encore la fin reelle du morceau (meme piege que bose_service.py).
CAST_START_GRACE_SECONDS = 6
# Marge avant de considerer qu'une piste de duree connue est terminee
# (latence de demarrage du flux, imprecision de TrackDuration).
CAST_DURATION_BUFFER_SECONDS = 2


##################################################################
# Event loop asyncio de fond (partage avec le futur controle DLNA)
##################################################################
# Flask/APScheduler sont synchrones, async-upnp-client/aiohttp non - on isole
# toute la logique asyncio dans un unique event loop tournant dans son propre
# thread plutot que de basculer l'app entiere en async (trop lourd pour ce
# projet, cf. CDC §2), avec un pont synchrone (run_coro) pour l'appeler
# depuis les routes Flask et les jobs planifies.

_loop: asyncio.AbstractEventLoop | None = None
_loop_lock = threading.Lock()


def _ensure_loop() -> asyncio.AbstractEventLoop:
    global _loop
    with _loop_lock:
        if _loop is not None and _loop.is_running():
            return _loop

        ready = threading.Event()
        holder = {}

        def _run():
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            holder["loop"] = loop
            ready.set()
            loop.run_forever()

        threading.Thread(target=_run, daemon=True, name="cast-asyncio-loop").start()
        ready.wait()
        _loop = holder["loop"]
        return _loop


def run_coro(coro, timeout=None):
    """Execute une coroutine sur l'event loop de fond depuis du code
    synchrone (route Flask, job APScheduler) et attend son resultat."""
    loop = _ensure_loop()
    future = asyncio.run_coroutine_threadsafe(coro, loop)
    return future.result(timeout=timeout)


##################################################################
# Decouverte Bose SoundTouch (mDNS + Web API confort)
##################################################################

def _discover_soundtouch_devices(timeout=SOUNDTOUCH_SCAN_TIMEOUT_SECONDS):
    """Decouverte mDNS, cf. app/libsoundtouch_patch.py. Renvoie une liste de
    dicts prets a etre upsertes dans CastDevice."""
    from app.libsoundtouch_patch import discover_devices_patched

    found = []
    try:
        devices = discover_devices_patched(timeout=timeout)
    except Exception:
        logger.exception("Cast scan : decouverte SoundTouch (mDNS) en echec")
        return found

    for device in devices:
        try:
            config = device.config
        except Exception:
            continue
        found.append({
            "device_type": SOUNDTOUCH_DEVICE_TYPE,
            "udn": f"soundtouch-{config.mac_address}",
            "ip_address": config.device_ip,
            "port": 8090,
            # Construite directement plutot que laissee a la decouverte SSDP
            # generique (_discover_dlna_devices_async / is_soundtouch_twin
            # ci-dessous) : celle-ci ne detecte pas les Bose de facon fiable
            # (cf. note technique §4.2), ce qui laissait DescriptionUrl a NULL
            # en base et faisait echouer start_cast des la selection ("pas de
            # description UPnP"). Format fixe documente (port DLNA 8091 par
            # defaut de libsoundtouch, deviceID = config.device_id).
            "description_url": f"http://{config.device_ip}:8091/XD/BO5EBO5E-F00D-F00D-FEED-{config.device_id}.xml",
            "friendly_name": config.name,
        })
    return found


##################################################################
# Decouverte DLNA generique (SSDP + description UPnP)
##################################################################

async def _discover_dlna_devices_async(timeout=DLNA_SCAN_TIMEOUT_SECONDS):
    from async_upnp_client.client_factory import UpnpFactory
    from async_upnp_client.search import async_search

    locations = set()

    async def _on_response(headers):
        location = headers.get("location")
        if location:
            locations.add(location)

    try:
        await async_search(_on_response, timeout=timeout, search_target=DLNA_SEARCH_TARGET)
    except Exception:
        logger.exception("Cast scan : recherche SSDP en echec")
        return []

    # Requester tolerant (cf. _build_lenient_requester) : la description UPnP
    # elle-meme n'est pas censee etre affectee par le bug ESP-ADF, mais autant
    # eviter qu'un device un peu non conforme fasse echouer tout le scan pour
    # la meme raison (corps de reponse non UTF-8 valide).
    requester = _build_lenient_requester()
    factory = UpnpFactory(requester)

    found = []
    for description_url in locations:
        try:
            device = await factory.async_create_device(description_url)
        except Exception as exc:
            # Renderer non conforme / injoignable : on l'ignore plutot que de
            # faire echouer tout le scan (cf. "Renderer non conforme UPnP AV"
            # dans la note technique - courant avec certains devices bas de gamme).
            logger.debug("Cast scan : description UPnP illisible (%s) : %s", description_url, exc)
            continue

        if "MediaRenderer" not in (device.device_type or ""):
            continue

        parsed = urlsplit(description_url)
        found.append({
            "device_type": DLNA_DEVICE_TYPE,
            "udn": device.udn,
            "ip_address": parsed.hostname,
            "port": parsed.port,
            "description_url": description_url,
            "friendly_name": device.friendly_name,
            "manufacturer": device.manufacturer or "",
        })
    return found


##################################################################
# Scan combine + persistance (CastDevice)
##################################################################

def _upsert_device(info: dict, now: datetime):
    from app.extensions import db
    from app.models import CastDevice

    existing = CastDevice.query.filter_by(Udn=info["udn"]).first()
    if existing:
        existing.DeviceType = info["device_type"]
        existing.IpAddress = info["ip_address"]
        existing.Port = info["port"]
        existing.DescriptionUrl = info["description_url"] or existing.DescriptionUrl
        existing.FriendlyName = info["friendly_name"]
        existing.LastSeenAt = now
        if not existing.Label:
            existing.Label = info["friendly_name"]
    else:
        db.session.add(CastDevice(
            DeviceType=info["device_type"],
            Udn=info["udn"],
            IpAddress=info["ip_address"],
            Port=info["port"],
            DescriptionUrl=info["description_url"],
            FriendlyName=info["friendly_name"],
            Label=info["friendly_name"],
            LastSeenAt=now,
        ))


def scan_devices() -> int:
    """Scanne le reseau local (mDNS SoundTouch + SSDP DLNA generique) et
    upsert la table CastDevice (par Udn, LastSeenAt rafraichi a chaque
    passage - une enceinte non revue depuis plusieurs scans est consideree
    hors ligne cote UI sans colonne dediee, puis supprimee au-dela de
    STALE_DEVICE_MAX_AGE). Appele par le job planifie
    cast_scan (app/scheduler.py)."""
    from app.extensions import db
    from app.models import CastDevice

    soundtouch_found = _discover_soundtouch_devices()
    try:
        dlna_found = run_coro(_discover_dlna_devices_async(), timeout=DLNA_SCAN_TIMEOUT_SECONDS + 10)
    except Exception:
        logger.exception("Cast scan : decouverte DLNA en echec")
        dlna_found = []

    soundtouch_by_ip = {info["ip_address"]: info for info in soundtouch_found}

    now = datetime.utcnow()
    seen_udns = set()

    for info in soundtouch_found:
        _upsert_device(info, now)
        seen_udns.add(info["udn"])

    for info in dlna_found:
        manufacturer = info.get("manufacturer", "").lower()
        is_soundtouch_twin = (
            any(hint in manufacturer for hint in _SOUNDTOUCH_MANUFACTURER_HINTS)
            and info["ip_address"] in soundtouch_by_ip
        )
        if is_soundtouch_twin:
            # Meme appareil physique que l'entree SoundTouch (mDNS) deja
            # traitee ci-dessus : on ne fait que lui fournir sa DescriptionUrl
            # (necessaire pour SetAVTransportURI), sans creer un second
            # CastDevice pour la meme enceinte.
            twin_udn = soundtouch_by_ip[info["ip_address"]]["udn"]
            existing = CastDevice.query.filter_by(Udn=twin_udn).first()
            if existing and not existing.DescriptionUrl:
                existing.DescriptionUrl = info["description_url"]
            continue

        _upsert_device(info, now)
        seen_udns.add(info["udn"])

    _purge_stale_devices(now)
    db.session.commit()
    return len(seen_udns)


def _purge_stale_devices(now: datetime):
    """Supprime les CastDevice non revus depuis STALE_DEVICE_MAX_AGE. Une
    lecture (Play) encore rattachee a l'un d'eux repasse en lecture locale :
    le device est injoignable depuis 24 h, la diffusion est de toute facon
    terminee."""
    from app.extensions import db
    from app.models import CastDevice, Play

    stale = CastDevice.query.filter(CastDevice.LastSeenAt < now - STALE_DEVICE_MAX_AGE).all()
    for device in stale:
        Play.query.filter_by(CastDeviceId=device.CastDeviceId).update({"CastDeviceId": None})
        with _sessions_lock:
            _sessions.pop(device.CastDeviceId, None)
        logger.info("Cast scan : suppression de %s (non vu depuis %s)", device.Label or device.FriendlyName, device.LastSeenAt)
        db.session.delete(device)


##################################################################
# Controle de la lecture (AVTransport) : demarrage, pause/reprise, arret,
# volume, et enchainement automatique de piste par polling.
##################################################################
# Une "session" de cast (une par CastDeviceId actif) garde le DmrDevice deja
# connecte (evite de re-parser sa description UPnP a chaque commande/poll) et
# le contexte necessaire pour pousser la piste suivante sans intervention de
# l'utilisateur (podcast/musique - pas de "suivant" pour une radio en direct).

_sessions: dict[int, dict] = {}
_sessions_lock = threading.Lock()
_monitor_started = False
_app = None  # cf. init_app - necessaire pour ouvrir un app_context depuis le thread de monitoring


def init_app(app):
    global _app
    _app = app


def _build_lenient_requester(timeout: int = 5):
    """AiohttpRequester tolerant aux corps de reponse HTTP invalides en UTF-8.

    Bug ESP-ADF (cf. Doc/dlna-tolerance-reponses-corrompues.md du depot
    Tunoova-ESP32) : pour les
    actions AVTransport sans valeur de retour (SetAVTransportURI, Play,
    Pause, Stop...), la bibliotheque UPnP fermee d'Espressif produit
    quelques octets binaires invalides au milieu du XML. Ces octets ne sont
    valides ni comme XML ni comme UTF-8 : l'AiohttpRequester standard
    d'async_upnp_client echoue donc des la lecture de la reponse
    (`response.text()` leve UnicodeDecodeError, encapsulee en
    UpnpCommunicationError) - avant meme d'atteindre le parsing XML, donc
    trop tot pour que `_tolerant_call` (qui ne voit que UpnpXmlParseError)
    puisse faire quoi que ce soit. On decode nous-memes le corps deja lu
    (errors="replace") plutot que de laisser echouer le decodage strict
    d'aiohttp : le XML reste invalide ensuite (UpnpXmlParseError, tolere par
    `_tolerant_call`) mais la requete HTTP elle-meme n'echoue plus. Un
    renderer standard (dont SoundTouch) n'envoie que de l'UTF-8 valide, ce
    decodage tolerant est donc strictement equivalent pour eux."""
    import aiohttp
    from async_upnp_client.aiohttp import AiohttpRequester
    from async_upnp_client.const import HttpResponse
    from async_upnp_client.exceptions import (
        UpnpClientResponseError,
        UpnpCommunicationError,
        UpnpConnectionError,
        UpnpConnectionTimeoutError,
    )

    class _LenientDlnaRequester(AiohttpRequester):
        async def async_http_request(self, http_request):
            req_headers = {**self._http_headers, **(http_request.headers or {})}
            try:
                async with aiohttp.ClientSession() as session:
                    async with session.request(
                        http_request.method,
                        http_request.url,
                        headers=req_headers,
                        data=http_request.body,
                        timeout=self._timeout,
                    ) as response:
                        status = response.status
                        resp_headers = response.headers or {}
                        resp_body_text = (await response.read()).decode("utf-8", errors="replace")
            except asyncio.TimeoutError as err:
                raise UpnpConnectionTimeoutError(repr(err)) from err
            except aiohttp.ClientConnectionError as err:
                raise UpnpConnectionError(repr(err)) from err
            except aiohttp.ClientResponseError as err:
                raise UpnpClientResponseError(
                    request_info=err.request_info,
                    history=err.history,
                    status=err.status,
                    message=err.message,
                    headers=err.headers,
                ) from err
            except aiohttp.ClientError as err:
                raise UpnpCommunicationError(repr(err)) from err

            return HttpResponse(status, resp_headers, resp_body_text)

    return _LenientDlnaRequester(timeout=timeout)


async def _get_dmr_device(description_url: str):
    from async_upnp_client.client_factory import UpnpFactory
    from async_upnp_client.profiles.dlna import DmrDevice

    requester = _build_lenient_requester()
    # non_strict : le SoundTouch expose un service QPlay (Tencent/WeChat, hors
    # marche occidental) dont le SCPD est dans un namespace non standard
    # (urn:schemas-tencent-com:service-1-0 au lieu de urn:schemas-upnp-org:...),
    # ce qui fait echouer async_create_device en mode strict par defaut
    # (UpnpXmlContentError "Invalid document root") avant meme d'atteindre
    # AVTransport, le seul service qui nous interesse ici.
    factory = UpnpFactory(requester, non_strict=True)
    upnp_device = await factory.async_create_device(description_url)
    return DmrDevice(upnp_device, event_handler=None)


async def _tolerant_call(coro) -> None:
    """Execute une action UPnP qui ne renvoie normalement aucune valeur
    (AVTransport SetAVTransportURI/Play/Pause/Stop/Next/Previous/Seek,
    RenderingControl SetVolume/SetMute...) en tolerant une reponse SOAP
    HTTP 200 mais corrompue.

    Bug connu de la bibliotheque UPnP fermee d'ESP-ADF (cf.
    Doc/dlna-tolerance-reponses-corrompues.md du depot Tunoova-ESP32) :
    pour ces actions "vides" par spec UPnP, elle renvoie quelques octets binaires invalides entre les
    balises XML alors que l'action a bel et bien ete executee cote renderer.
    Un statut HTTP 200 suffit a considerer la commande reussie ; seule la
    reponse (sans interet ici, il n'y a de toute facon aucune valeur a en
    extraire) est corrompue - inutile de faire echouer toute la diffusion
    pour ca. Kodi/VLC/WMP tolerent deja ce genre de XML invalide, un
    renderer standard n'est donc jamais concerne par ce catch (sa reponse est
    toujours parsable) : ca ne change rien pour un renderer DLNA classique ou
    un SoundTouch."""
    from async_upnp_client.exceptions import UpnpXmlParseError

    try:
        await coro
    except UpnpXmlParseError:
        logger.debug("Cast : reponse SOAP corrompue mais HTTP 200 - commande consideree reussie")


def cast_base_url(device, host_url: str, server_port: str | None, configured: str = "") -> str:
    """URL de base (http://IP-locale:port/) des flux /media/cast/... passes en
    SetAVTransportURI (play_service.cast_media_url).

    Toujours l'adresse LOCALE du serveur, en http, jamais le nom de domaine
    vu par le navigateur (`host_url`) : via un nom public, l'enceinte sortait
    sur Internet puis revenait par la box et le reverse proxy (Caddy) -
    HTTPS que le SoundTouch ne gere pas, redirection 308 http -> https que
    l'ESP32 (esp-adf http_stream) ne suit pas, et IP vue par l'appli qui
    serait celle de la box (et non plus du LAN) une fois ProxyFix en place.

    Par ordre de priorite :
    1. `configured` (CAST_BASE_URL du .env) si renseigne - cas ou l'adresse
       detectee n'est pas joignable par les enceintes (ex. WSL en NAT) ;
    2. `host_url` si le navigateur passe deja par une IP privee (acces en
       local, ou redirection de port vers une VM/WSL) ;
    3. sinon l'IP de l'interface qui sert a joindre l'enceinte, et le port
       d'ecoute reel de l'appli (`server_port`, SERVER_PORT du WSGI)."""
    if configured:
        return configured.rstrip("/") + "/"

    parts = urlsplit(host_url)
    try:
        if parts.hostname and ipaddress.ip_address(parts.hostname).is_private:
            return f"http://{parts.netloc}/"
    except ValueError:
        pass

    local_ip = _local_ip_towards(device.IpAddress)
    port = server_port or parts.port or 80
    return f"http://{local_ip}:{port}/"


def _local_ip_towards(target_ip: str) -> str:
    """IP de l'interface locale utilisee pour joindre `target_ip` (aucun
    paquet n'est envoye : connect() en UDP ne fait que choisir la route)."""
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.connect((target_ip, 9))
        return sock.getsockname()[0]


def start_cast(device, media_url: str, title: str, artist: str, users_id: int, content_type: str, item_id: int, base_url: str,
               start_offset: int = 0) -> bool:
    """Pousse `media_url` sur `device` via AVTransport (SetAVTransportURI +
    Play) et ouvre une session suivie par le thread de monitoring (pour
    l'enchainement automatique). Retourne False sans lever si la diffusion
    echoue (renderer injoignable/non conforme, cf. note technique) - a
    l'appelant de traduire ca en message utilisateur.

    `start_offset` : position (secondes) a laquelle le flux `media_url`
    commence deja (parametre start=, cf. play_service.cast_media_url) -
    l'enceinte, elle, se croit a 0:00. Garde dans la session pour que
    get_status renvoie la position reelle dans l'episode/le morceau."""
    if not device.DescriptionUrl:
        logger.error("Cast : pas de description UPnP pour %s (%s), diffusion impossible", device.Label, device.IpAddress)
        return False

    try:
        dmr = run_coro(_get_dmr_device(device.DescriptionUrl), timeout=10)
        run_coro(_tolerant_call(dmr.async_set_transport_uri(media_url, title or "Tunoova")), timeout=10)
        run_coro(_tolerant_call(dmr.async_play()), timeout=10)
    except Exception:
        logger.exception("Cast : demarrage de la diffusion en echec sur %s", device.IpAddress)
        return False

    with _sessions_lock:
        _sessions[device.CastDeviceId] = {
            "dmr": dmr,
            "users_id": users_id,
            "content_type": content_type,
            "item_id": item_id,
            "base_url": base_url,
            "start_offset": start_offset,
            "started_at": time.time(),
        }
    _ensure_monitor_thread()
    return True


def stop_cast(device) -> None:
    dmr = _get_or_reconnect_dmr(device, pop_session=True)
    if dmr is None:
        return

    try:
        run_coro(_tolerant_call(dmr.async_stop()), timeout=10)
    except Exception:
        logger.exception("Cast : arret de la diffusion en echec sur %s", device.IpAddress)


def _get_or_reconnect_dmr(device, pop_session: bool = False):
    """Recupere le DmrDevice de la session active, ou en reconnecte un a la
    volee si aucune session n'est trouvee en memoire - notamment apres un
    redemarrage du process Flask (ex. reloader de debug qui redemarre a
    chaque modification de fichier pendant le developpement) : l'enceinte,
    elle, continue de lire le flux deja pousse avant le redemarrage, seul le
    suivi cote Tunoova (session en memoire, _sessions) est perdu. Sans ce
    filet, Pause/Reprise/Volume echouaient silencieusement (aucune exception,
    juste un retour False) sur une enceinte DLNA generique pourtant bel et
    bien en train de diffuser - la commande n'avait simplement plus personne
    a qui la transmettre."""
    with _sessions_lock:
        session = _sessions.pop(device.CastDeviceId, None) if pop_session else _sessions.get(device.CastDeviceId)
    if session:
        return session["dmr"]

    if not device.DescriptionUrl:
        return None
    try:
        dmr = run_coro(_get_dmr_device(device.DescriptionUrl), timeout=10)
    except Exception:
        logger.exception("Cast : reconnexion au device en echec sur %s", device.IpAddress)
        return None

    if not pop_session:
        _restore_session_tracking(device, dmr)
    return dmr


def _restore_session_tracking(device, dmr):
    """Reconstitue le suivi de session (utilise par le thread de monitoring
    pour l'enchainement automatique) a partir de l'etat persiste en base
    (table Play) - le `base_url` d'origine (adresse locale du serveur au
    moment du /cast/select initial) n'est lui pas recuperable apres un
    redemarrage : l'enchainement automatique sur l'item suivant restera
    desactive pour cette session tant qu'un nouveau /cast/select ne l'aura
    pas repositionne, mais Pause/Reprise/Volume fonctionnent de nouveau
    immediatement, qui est le probleme concret rencontre."""
    from app.models import Play
    from app import play_service

    play = Play.query.filter_by(CastDeviceId=device.CastDeviceId).first()
    if not play or not play.ItemId or not play.PlayType:
        return
    content_type = play_service.CONTENT_TYPE_BY_PLAY_TYPE.get(play.PlayType)
    if not content_type:
        return

    with _sessions_lock:
        _sessions[device.CastDeviceId] = {
            "dmr": dmr,
            "users_id": play.UsersId,
            "content_type": content_type,
            "item_id": play.ItemId,
            "base_url": None,
            "started_at": time.time(),
        }
    _ensure_monitor_thread()


def _dispatch_session_coro(device, make_coro) -> bool:
    dmr = _get_or_reconnect_dmr(device)
    if dmr is None:
        return False
    try:
        run_coro(_tolerant_call(make_coro(dmr)), timeout=10)
        return True
    except Exception:
        logger.exception("Cast : commande en echec sur %s", device.IpAddress)
        return False


def _dispatch_soundtouch_webapi(device, action: str) -> bool:
    try:
        from libsoundtouch import soundtouch_device

        st_device = soundtouch_device(device.IpAddress)
        if action == "pause":
            st_device.pause()
        else:
            st_device.play()
        return True
    except Exception:
        logger.exception("Cast : commande %s (Web API) en echec sur %s", action, device.IpAddress)
        return False


def pause_cast(device) -> bool:
    # Bose : le Web API (libsoundtouch) est le canal documente pour
    # play/pause/skip (cf. note technique §2 : "Le Web
    # API sert au controle (play/pause/skip...)") - le SetAVTransportURI/Play
    # DLNA ne sert lui qu'a POUSSER l'URL au demarrage. Un `Pause` UPnP brut
    # (AVTransport) est justement le genre de commande citee au §4.2 comme
    # peu fiable sur un renderer Bose non conforme (accepte surtout l'app
    # Bose elle-meme comme controleur) - constate en pratique : la commande
    # partait sans erreur cote Tunoova, mais la lecture continuait sur
    # l'enceinte (pas d'exception a capturer, juste une commande ignoree).
    if device.DeviceType == SOUNDTOUCH_DEVICE_TYPE:
        return _dispatch_soundtouch_webapi(device, "pause")
    return _dispatch_session_coro(device, lambda dmr: dmr.async_pause())


def resume_cast(device) -> bool:
    if device.DeviceType == SOUNDTOUCH_DEVICE_TYPE:
        return _dispatch_soundtouch_webapi(device, "play")
    return _dispatch_session_coro(device, lambda dmr: dmr.async_play())


def set_volume(device, volume_percent: int) -> bool:
    """Bose : Web API (libsoundtouch), plus fiable que le RenderingControl
    UPnP du SoundTouch (cf. note technique §5). DLNA generique : AVTransport
    natif, il n'y a pas d'equivalent Web API."""
    volume_percent = max(0, min(100, volume_percent))

    if device.DeviceType == SOUNDTOUCH_DEVICE_TYPE:
        try:
            from libsoundtouch import soundtouch_device

            soundtouch_device(device.IpAddress).set_volume(volume_percent)
            return True
        except Exception:
            logger.exception("Cast : reglage du volume (Web API) en echec sur %s", device.IpAddress)
            return False

    return _dispatch_session_coro(device, lambda dmr: dmr.async_set_volume_level(volume_percent / 100))


def get_volume(device) -> int | None:
    """Volume courant (0-100) pour initialiser le curseur de la fenetre Cast -
    meme repartition Web API / DLNA que set_volume. None si illisible."""
    if device.DeviceType == SOUNDTOUCH_DEVICE_TYPE:
        try:
            from libsoundtouch import soundtouch_device

            return int(soundtouch_device(device.IpAddress).volume().actual)
        except Exception:
            logger.exception("Cast : lecture du volume (Web API) en echec sur %s", device.IpAddress)
            return None

    with _sessions_lock:
        session = _sessions.get(device.CastDeviceId)
    if not session:
        return None
    try:
        run_coro(session["dmr"].async_update(), timeout=10)
        level = session["dmr"].volume_level
        return round(level * 100) if level is not None else None
    except Exception:
        logger.exception("Cast : lecture du volume en echec (device %s)", device.CastDeviceId)
        return None


def get_status(device_id: int) -> dict | None:
    """Statut temps reel (position/duree/lecture) de la session active sur ce
    device, pour la barre de progression du panneau Play (GET /cast/status) -
    None si aucune session en cours ou si l'appel au device echoue."""
    with _sessions_lock:
        session = _sessions.get(device_id)
    if not session:
        return None
    try:
        run_coro(session["dmr"].async_update(), timeout=10)
        dmr = session["dmr"]
        # Flux demarre a `start_offset` (reprise / deplacement dans la barre) :
        # l'enceinte compte depuis 0 et annonce la duree du flux restant.
        offset = session.get("start_offset", 0)
        duration = dmr.media_duration or 0
        return {
            "playing": dmr.transport_state == "PLAYING",
            "position": (dmr.media_position or 0) + offset,
            "duration": duration + offset if duration else 0,
        }
    except Exception:
        logger.exception("Cast : lecture du statut en echec (device %s)", device_id)
        return None


def _ensure_monitor_thread():
    global _monitor_started
    with _sessions_lock:
        if _monitor_started:
            return
        _monitor_started = True
    threading.Thread(target=_monitor_loop, daemon=True, name="cast-monitor").start()


def _monitor_loop():
    while True:
        time.sleep(CAST_POLL_SECONDS)
        with _sessions_lock:
            device_ids = list(_sessions.keys())
        for device_id in device_ids:
            try:
                _check_session(device_id)
            except Exception:
                logger.exception("Cast : suivi de la diffusion en echec (device %s)", device_id)


def _check_session(device_id: int):
    with _sessions_lock:
        session = _sessions.get(device_id)
    if not session:
        return
    if time.time() - session["started_at"] < CAST_START_GRACE_SECONDS:
        return

    try:
        run_coro(session["dmr"].async_update(), timeout=10)
        state = session["dmr"].transport_state
        position = session["dmr"].media_position
        duration = session["dmr"].media_duration
    except Exception:
        logger.exception("Cast : mise a jour du statut en echec (device %s)", device_id)
        return

    finished = state in ("STOPPED", "NO_MEDIA_PRESENT")
    if not finished and duration and position is not None:
        # Source de verite recommandee par la note technique (§3) : les
        # evenements GENA du SoundTouch sont peu fiables, TrackDuration/
        # position de GetPositionInfo le sont davantage que le statut seul.
        # Valeurs brutes de l'enceinte (toutes deux relatives au flux) : le
        # decalage start_offset n'a pas a etre applique ici.
        finished = position >= max(duration - CAST_DURATION_BUFFER_SECONDS, 0)

    if finished:
        _advance_or_stop(device_id, session)


def _advance_or_stop(device_id: int, session: dict):
    """Fin de piste detectee sur une enceinte en cast : enchaine sur l'item
    suivant (podcast/musique, meme logique de navigation que la barre de
    lecture locale - cf. play_service.get_context) ou arrete simplement
    (radio en direct, ou fin de liste)."""
    if _app is None:
        with _sessions_lock:
            _sessions.pop(device_id, None)
        return

    # Contexte de REQUETE (pas seulement d'application) : get_context construit
    # des URL via url_for (ex. flux MP3 local), impossible hors requete sans
    # SERVER_NAME - l'exception etait avalee par _monitor_loop et la piste
    # suivante ne partait jamais. base_url = adresse locale du serveur au
    # moment du Cast, cf. cast_media_url.
    with _app.test_request_context(base_url=session.get("base_url") or "http://localhost/"):
        from app.extensions import db
        from app.models import CastDevice
        from app import play_service

        device = db.session.get(CastDevice, device_id)
        content_type = session.get("content_type")
        item_id = session.get("item_id")
        users_id = session.get("users_id")
        base_url = session.get("base_url")

        ctx = play_service.get_context(content_type, item_id, users_id) if content_type and item_id and users_id else None
        next_id = ctx.get("next_id") if ctx else None
        next_ctx = play_service.get_context(content_type, next_id, users_id) if next_id else None

        if device and next_ctx and base_url:
            media_url = play_service.cast_media_url(base_url, content_type, next_id, users_id)
            subtitle = next_ctx["subtitle"]
            ok = start_cast(
                device, media_url, title=next_ctx["title"], artist=subtitle,
                users_id=users_id, content_type=content_type, item_id=next_id, base_url=base_url,
            )
            if ok:
                # CastDeviceId n'est pas touche par set_current_play : il
                # reste positionne sur ce device d'un item au suivant.
                play_service.set_current_play(users_id, content_type, next_id, media_url)
                return

        with _sessions_lock:
            _sessions.pop(device_id, None)

        play = play_service.get_current_play(users_id) if users_id else None
        if play and play.CastDeviceId == device_id:
            play.CastDeviceId = None
            play.PlayStatus = "Stop"
            db.session.commit()
