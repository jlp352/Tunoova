"""Endpoints de diffusion (CDC §2) : /media/mp3/<id> pour la musique locale,
/media/ytmusic/<id> pour YouTube Music - proteges par la session utilisateur
(navigateur). /media/cast/<type>/<id> est la variante utilisee pour la
diffusion Cast (CDC §7/§8/§10) : un device (Bose SoundTouch, DLNA) ne
s'authentifie pas comme un navigateur, donc pas de @login_required ici -
protection par jeton signe (app/tokens.py) + restriction au reseau local
(CDC §12 : serveur et devices Cast toujours sur le meme LAN)."""

import io
import ipaddress
import logging
import os
import subprocess
from urllib.parse import urlsplit

import requests
from flask import Blueprint, Response, abort, request, send_file, stream_with_context
from flask_login import current_user, login_required

from app.models import EpisodesUsers, MusicTrack, RadioUsers
from app import mp3_library_service, music_catalog, profile_service, youtube_music_service
from app.tokens import read_cast_media_token

bp = Blueprint("media", __name__, url_prefix="/media")
logger = logging.getLogger(__name__)

# CDC §12 : le serveur et les devices Cast sont toujours sur le meme reseau
# local (Homelab) - /media/cast/... n'a donc pas besoin d'etre expose au-dela.
_LAN_NETWORKS = [ipaddress.ip_network(n) for n in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "127.0.0.0/8")]


def _is_lan_address(addr: str | None) -> bool:
    if not addr:
        return False
    try:
        ip = ipaddress.ip_address(addr)
    except ValueError:
        return False
    return any(ip in network for network in _LAN_NETWORKS)


@bp.route("/profile_photo")
@login_required
def profile_photo():
    if not current_user.Photo:
        abort(404)
    absolute_path = os.path.join(profile_service.UPLOAD_DIR, os.path.basename(current_user.Photo))
    if not os.path.isfile(absolute_path):
        abort(404)
    return send_file(absolute_path)


@bp.route("/mp3/<int:track_id>")
@login_required
def mp3_stream(track_id):
    track = MusicTrack.query.filter_by(TrackId=track_id, Source=music_catalog.SOURCE_LOCAL).first_or_404()
    absolute_path = mp3_library_service.absolute_path_for(track.SourceRef)
    if not absolute_path:
        abort(404)
    return send_file(absolute_path, mimetype="audio/mpeg")


@bp.route("/album_cover/<int:album_id>")
@login_required
def album_cover(album_id):
    """Pochette locale d'un album MP3 (cf. mp3_library_service.local_cover)."""
    cover = mp3_library_service.local_cover(album_id)
    if cover is None:
        abort(404)
    if isinstance(cover, str):
        return send_file(cover, max_age=86400)
    data, mimetype = cover
    return send_file(io.BytesIO(data), mimetype=mimetype, max_age=86400)


@bp.route("/ytmusic/<int:track_id>")
@login_required
def ytmusic_stream(track_id):
    track = MusicTrack.query.filter_by(TrackId=track_id, Source=music_catalog.SOURCE_YOUTUBE).first_or_404()
    url = youtube_music_service.get_stream_url(track.SourceRef, quality=current_user.StreamQuality)
    if not url:
        abort(502)
    return _relay_ranged(url, refresh=lambda: youtube_music_service.get_stream_url(
        track.SourceRef, quality=current_user.StreamQuality, refresh=True))


@bp.route("/ytmusic/<int:track_id>/warm", methods=["POST"])
@login_required
def ytmusic_warm(track_id):
    """Resout a l'avance l'URL googlevideo du morceau suivant (yt-dlp, plusieurs
    secondes) pour qu'elle soit deja en cache a l'enchainement (cf. app.js,
    tunoovaWarmStream)."""
    track = MusicTrack.query.filter_by(TrackId=track_id, Source=music_catalog.SOURCE_YOUTUBE).first_or_404()
    url = youtube_music_service.get_stream_url(track.SourceRef, quality=current_user.StreamQuality)
    return ("", 204 if url else 502)


# En-tetes du flux googlevideo repris tels quels vers le navigateur : de quoi
# lire et surtout se deplacer dans le morceau (HTTP Range -> 206).
_RANGED_HEADERS = ("Content-Type", "Content-Length", "Content-Range", "Accept-Ranges")


def _relay_ranged(source_url: str, refresh=None):
    """Relaie le flux YouTube Music par le serveur au lieu de rediriger le
    navigateur vers googlevideo.com (CDC §2) :

    - l'URL signee est liee a l'IP qui l'a extraite (parametre `ip=`) : un
      navigateur qui ne sort pas par la meme IP publique que le serveur
      (4G, reseau d'entreprise...) recoit un 403 ;
    - derriere un proxy filtrant (ex. Zscaler), googlevideo.com peut etre
      bloque ou remplace par une page HTML, que Chrome rejette (CORB).

    Contrairement a `_relay`, transmet l'en-tete Range et les en-tetes de
    reponse associes, pour garder la barre de progression fonctionnelle.

    `refresh` : rappele une fois sur un 403 pour obtenir une URL fraiche -
    googlevideo revoque parfois l'URL mise en cache avant son expiration."""
    # identity : Content-Length doit rester celui des octets relayes.
    headers = {"User-Agent": _RELAY_USER_AGENT, "Accept-Encoding": "identity"}
    if request.headers.get("Range"):
        headers["Range"] = request.headers["Range"]
    try:
        upstream = requests.get(source_url, stream=True, timeout=10, headers=headers)
        if upstream.status_code == 403 and refresh:
            upstream.close()
            logger.warning("Relais YouTube Music : 403 sur l'URL en cache, nouvelle extraction")
            fresh_url = refresh()
            if not fresh_url:
                abort(502)
            upstream = requests.get(fresh_url, stream=True, timeout=10, headers=headers)
    except requests.RequestException as exc:
        logger.warning("Relais YouTube Music en echec : %s", exc)
        abort(502)
    if upstream.status_code >= 400:
        upstream.close()
        if upstream.status_code != 416:
            logger.warning("Relais YouTube Music : googlevideo a repondu %s", upstream.status_code)
        abort(416 if upstream.status_code == 416 else 502)

    def generate():
        try:
            for chunk in upstream.iter_content(chunk_size=65536):
                if chunk:
                    yield chunk
        finally:
            upstream.close()

    response = Response(stream_with_context(generate()), status=upstream.status_code)
    for name in _RANGED_HEADERS:
        if name in upstream.headers:
            response.headers[name] = upstream.headers[name]
    return response


# Certains hebergeurs de podcasts (Acast/CloudFront constate en pratique)
# renvoient un 403 au User-Agent par defaut de `requests` ("python-requests/x.y",
# signature de scraping courante), mais acceptent un navigateur classique -
# curl passe deja car son propre User-Agent par defaut n'est pas filtre.
_RELAY_USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"


def _relay(source_url: str, default_mimetype: str):
    """Relaie un flux distant (HTTP ou HTTPS) en HTTP simple : les enceintes
    Cast (Bose SoundTouch, protocole UPnP herite) ne savent pas lire de
    HTTPS - le serveur Tunoova fait le pont (CDC §2)."""
    try:
        upstream = requests.get(source_url, stream=True, timeout=10, headers={"User-Agent": _RELAY_USER_AGENT})
    except requests.RequestException:
        abort(502)
    if upstream.status_code >= 400:
        upstream.close()
        abort(502)

    def generate():
        try:
            for chunk in upstream.iter_content(chunk_size=8192):
                if chunk:
                    yield chunk
        finally:
            upstream.close()

    content_type = upstream.headers.get("Content-Type", default_mimetype)
    return Response(stream_with_context(generate()), content_type=content_type)


_MP4_AUDIO_EXTENSIONS = (".m4a", ".m4b", ".mp4", ".aac")


def _looks_like_mp4_audio(url: str) -> bool:
    """Heuristique sur l'extension (avant la query string) : evite une requete
    HTTP supplementaire rien que pour lire un Content-Type. Vise les episodes
    AAC-in-MP4 (ex. Radio France, cf. _relay_via_ffmpeg) - un .mp3 direct n'a
    pas ce probleme et continue par le relais simple ci-dessus."""
    path = urlsplit(url).path.lower()
    return path.endswith(_MP4_AUDIO_EXTENSIONS)


def _relay_via_ffmpeg(source_url: str, start: int = 0, output_format: str = "adts"):
    """Remultiplexe a la volee (ffmpeg, sans reencoder : -c:a copy) vers de
    l'AAC brut (ADTS) un flux AAC-in-MP4 illisible tel quel par le renderer
    DLNA embarque d'un Bose SoundTouch (contrairement au MP3 local et a la
    radio AAC classique, qui eux passent deja sans probleme) :

    - YouTube Music ne sert son audio qu'en MP4 fragmente (DASH, cf. `file` :
      "ISO Media... Dynamic Adaptive Streaming over HTTP").
    - Certains podcasts (ex. Radio France) servent un .m4a classique mais non
      optimise pour le streaming (atome `moov`, l'index du fichier, place a
      la toute fin plutot qu'au debut) : impossible a lire sans pouvoir
      sauter directement a la fin (HTTP Range), ce que notre propre relais
      `_relay` ne permet pas (pas de Content-Length/Accept-Ranges transmis).

    On pointe donc ffmpeg directement sur l'URL source (qui elle gere le
    Range, verifie en pratique) plutot que de repasser par `_relay`.

    `start` (secondes) : -ss place AVANT -i, ffmpeg saute directement dans
    la source (Range HTTP ou fichier local) - c'est ainsi que se fait la
    reprise en Cast, les enceintes refusant le Seek AVTransport.
    `output_format` : "adts" (AAC) ou "mp3" (source deja en MP3, copiee
    telle quelle - -write_xing 0 : pas d'en-tete Xing a duree fausse)."""
    command = ["ffmpeg", "-loglevel", "error"]
    if source_url.startswith(("http://", "https://")):
        command += ["-user_agent", _RELAY_USER_AGENT]
    if start > 0:
        command += ["-ss", str(start)]
    command += ["-i", source_url, "-vn", "-c:a", "copy"]
    if output_format == "mp3":
        command += ["-write_xing", "0"]
    command += ["-f", output_format, "-"]
    process = subprocess.Popen(command, stdout=subprocess.PIPE)

    def generate():
        try:
            while True:
                chunk = process.stdout.read(8192)
                if not chunk:
                    break
                yield chunk
        finally:
            process.stdout.close()
            process.terminate()

    mimetype = "audio/mpeg" if output_format == "mp3" else "audio/aac"
    return Response(stream_with_context(generate()), content_type=mimetype)


@bp.route("/cast/<content_type>/<int:item_id>")
def cast_stream(content_type, item_id):
    """Endpoint pousse par app/cast_service.py (SetAVTransportURI) : resout
    systematiquement vers un flux HTTP proxifie par l'appli, y compris pour
    Radio/Podcast (URL directe dans le navigateur, mais pas en Cast - CDC §2).
    Pas de @login_required : verification par jeton signe + LAN uniquement."""
    if content_type not in ("music", "radio", "podcast"):
        abort(404)

    token = request.args.get("token", "")
    users_id = read_cast_media_token(token, content_type, item_id)
    if users_id is None:
        abort(403)
    if not _is_lan_address(request.remote_addr):
        abort(403)

    # Reprise / deplacement dans la barre pendant le Cast : le flux demarre
    # directement a cette position (cf. play_service.cast_media_url).
    start = max(request.args.get("start", 0, type=int), 0)

    if content_type == "music":
        track = MusicTrack.query.get_or_404(item_id)
        if track.Source == music_catalog.SOURCE_YOUTUBE:
            url = youtube_music_service.get_stream_url(track.SourceRef, quality="high")
            if not url:
                abort(502)
            return _relay_via_ffmpeg(url, start)
        absolute_path = mp3_library_service.absolute_path_for(track.SourceRef)
        if not absolute_path:
            abort(404)
        if start:
            return _relay_via_ffmpeg(absolute_path, start, output_format="mp3")
        return send_file(absolute_path, mimetype="audio/mpeg")

    if content_type == "radio":
        radio = RadioUsers.query.filter_by(RadioUsersId=item_id, UsersId=users_id).first_or_404()
        return _relay(radio.LinkURL, "audio/mpeg")

    episode = EpisodesUsers.query.filter_by(EpisodesUsersId=item_id, UsersId=users_id).first_or_404()
    if _looks_like_mp4_audio(episode.EpisodeURL):
        return _relay_via_ffmpeg(episode.EpisodeURL, start)
    if start:
        return _relay_via_ffmpeg(episode.EpisodeURL, start, output_format="mp3")
    return _relay(episode.EpisodeURL, "audio/mpeg")
