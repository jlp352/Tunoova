"""Routes de la fenetre Cast (CDC §7/§8) : liste des enceintes decouvertes
(app/cast_service.py::scan_devices, job planifie cast_scan), selection d'un
device pour diffuser l'item actuellement en cours (table Play), arret, et
relais play/pause/volume vers le device en train de diffuser."""

from datetime import datetime, timedelta

from flask import abort, current_app, flash, jsonify, redirect, request, url_for
from flask_login import current_user, login_required

from app.cast import bp
from app.extensions import db
from app.i18n import translate
from app.logging_util import log_screen_view
from app.models import CastDevice
from app import cast_service, play_service
from app.url_util import safe_local_url

# Marge de tolerance sur CastDevice.LastSeenAt pour l'affichage "en ligne" :
# le job cast_scan tourne toutes les 5 min par defaut (JOB_CAST_SCAN_CRON,
# cf. app/scheduler.py) - un multiple large evite de marquer une enceinte
# hors ligne pour un simple scan manque ou ralenti.
ONLINE_TOLERANCE = timedelta(minutes=15)


def _redirect_back():
    return redirect(safe_local_url(request.referrer, url_for("main.home")))


def _wants_json() -> bool:
    """Meme convention que play/routes.py::_wants_json() : la fenetre Cast
    (#cast-modal, app/static/js/app.js) agit par fetch() pour ne jamais
    naviguer (couperait la lecture en cours, cf. CDC §2/§7)."""
    return request.headers.get("X-Requested-With") == "XMLHttpRequest"


def _active_device_id() -> int | None:
    play = play_service.get_current_play(current_user.UsersId)
    return play.CastDeviceId if play else None


@bp.route("/devices")
@login_required
def devices():
    # Fenetre Cast = <dialog> sans route d'ecran propre : cet appel est fait a
    # chaque ouverture (tunoovaOpenCastModal), c'est donc ici qu'on journalise.
    log_screen_view("Cast")
    cutoff = datetime.utcnow() - ONLINE_TOLERANCE
    active_id = _active_device_id()
    rows = CastDevice.query.order_by(CastDevice.Label.asc(), CastDevice.FriendlyName.asc()).all()
    return jsonify([
        {
            "id": d.CastDeviceId,
            "label": d.Label or d.FriendlyName,
            "device_type": d.DeviceType,
            "online": d.LastSeenAt >= cutoff,
            "active": d.CastDeviceId == active_id,
        }
        for d in rows
    ])


@bp.route("/select", methods=["POST"])
@login_required
def select():
    device = db.session.get(CastDevice, request.form.get("device_id", type=int))
    if not device:
        abort(404)

    play = play_service.get_current_play(current_user.UsersId)
    content_type = play_service.CONTENT_TYPE_BY_PLAY_TYPE.get(play.PlayType) if play else None
    if not play or not play.ItemId or not content_type:
        message = translate("cast_nothing_playing")
        if _wants_json():
            return jsonify(ok=False, message=message)
        flash(message, "error")
        return _redirect_back()

    ctx = play_service.get_context(content_type, play.ItemId, current_user.UsersId)
    if not ctx:
        abort(404)

    # Passage d'une enceinte a une autre : l'ancienne devra etre arretee une
    # fois la nouvelle lancee (sinon les deux jouent en meme temps).
    previous_device_id = play.CastDeviceId if play.CastDeviceId != device.CastDeviceId else None

    # Reprise au meme endroit (podcast/musique ; une radio est en direct) :
    # position envoyee par le navigateur (<audio>.currentTime au passage local
    # -> Cast, ou curseur de la barre deplace pendant le Cast), sinon celle de
    # l'enceinte qu'on quitte, sinon celle enregistree. Le flux demarre
    # directement a cette position (start=), sans Seek sur l'enceinte
    # (refuse par le SoundTouch).
    start_offset = 0
    if content_type != "radio":
        start_position = request.form.get("position", type=float)
        if not start_position and previous_device_id:
            start_position = (cast_service.get_status(previous_device_id) or {}).get("position")
        start_position = start_position or play.PositionSeconds or 0.0
        start_offset = int(start_position) if start_position > 1 else 0

    base_url = cast_service.cast_base_url(device, request.host_url, request.environ.get("SERVER_PORT"),
                                          current_app.config.get("CAST_BASE_URL", ""))
    media_url = play_service.cast_media_url(base_url, content_type, play.ItemId, current_user.UsersId, start=start_offset)
    subtitle = translate(ctx["subtitle"]) if content_type == "radio" else ctx["subtitle"]

    ok = cast_service.start_cast(
        device,
        media_url,
        title=ctx["title"],
        artist=subtitle,
        users_id=current_user.UsersId,
        content_type=content_type,
        item_id=play.ItemId,
        base_url=base_url,
        start_offset=start_offset,
    )
    if not ok:
        message = translate("cast_start_failed")
        if _wants_json():
            return jsonify(ok=False, message=message)
        flash(message, "error")
        return _redirect_back()

    if previous_device_id:
        previous_device = db.session.get(CastDevice, previous_device_id)
        if previous_device:
            cast_service.stop_cast(previous_device)

    play.CastDeviceId = device.CastDeviceId
    db.session.commit()

    label = device.Label or device.FriendlyName
    message = translate("cast_started", label=label)
    if _wants_json():
        return jsonify(ok=True, message=message, device_label=label, device_id=device.CastDeviceId)
    flash(message, "success")
    return _redirect_back()


@bp.route("/stop", methods=["POST"])
@login_required
def stop():
    play = play_service.get_current_play(current_user.UsersId)
    if play and play.CastDeviceId:
        # Position reelle de l'enceinte AVANT l'arret (la session est fermee
        # par stop_cast) : le navigateur reprend la lecture locale a cet endroit.
        position = None
        if play_service.CONTENT_TYPE_BY_PLAY_TYPE.get(play.PlayType) != "radio":
            live = cast_service.get_status(play.CastDeviceId) or {}
            position = live.get("position")
            if position:
                play.PositionSeconds = position
        device = db.session.get(CastDevice, play.CastDeviceId)
        if device:
            cast_service.stop_cast(device)
        play.CastDeviceId = None
        db.session.commit()
        message = translate("cast_stopped")
        if _wants_json():
            return jsonify(ok=True, message=message, position=position)
        flash(message, "success")
        return _redirect_back()

    if _wants_json():
        return jsonify(ok=True)
    return _redirect_back()


@bp.route("/status")
@login_required
def status():
    """Statut courant de la diffusion (position/duree/lecture), pour la barre
    de progression du panneau Play pendant un Cast - <audio> local ne peut pas
    l'alimenter puisqu'il ne joue pas (cf. tunoovaPollCastStatus)."""
    play = play_service.get_current_play(current_user.UsersId)
    if not play or not play.CastDeviceId:
        return jsonify(active=False)

    device = db.session.get(CastDevice, play.CastDeviceId)
    label = (device.Label or device.FriendlyName) if device else None
    live = cast_service.get_status(play.CastDeviceId) or {}

    content_type = play_service.CONTENT_TYPE_BY_PLAY_TYPE.get(play.PlayType)
    play_url = url_for("play.show", content_type=content_type, item_id=play.ItemId) if content_type and play.ItemId else None

    return jsonify(
        active=True,
        device_label=label,
        # Item REELLEMENT en cours : change tout seul quand le serveur enchaine
        # sur l'enceinte (cast_service._advance_or_stop) - le navigateur s'en
        # sert pour remettre la barre de lecture a jour.
        play_url=play_url,
        playing=live.get("playing", play.PlayStatus == "Play"),
        position=live.get("position", play.PositionSeconds or 0),
        duration=live.get("duration", 0),
    )


@bp.route("/playpause", methods=["POST"])
@login_required
def playpause():
    play = play_service.get_current_play(current_user.UsersId)
    if not play or not play.CastDeviceId:
        abort(400)
    device = db.session.get(CastDevice, play.CastDeviceId)
    if not device:
        abort(404)

    if play.PlayStatus == "Play":
        ok = cast_service.pause_cast(device)
        new_status = "Pause"
    else:
        ok = cast_service.resume_cast(device)
        new_status = "Play"

    if ok:
        play.PlayStatus = new_status
        db.session.commit()

    return jsonify(ok=ok, status=play.PlayStatus)


@bp.route("/volume", methods=["GET", "POST"])
@login_required
def volume():
    play = play_service.get_current_play(current_user.UsersId)
    if not play or not play.CastDeviceId:
        abort(400)
    device = db.session.get(CastDevice, play.CastDeviceId)
    if not device:
        abort(404)

    if request.method == "GET":
        return jsonify(volume=cast_service.get_volume(device))

    value = request.form.get("value", type=int)
    if value is None:
        abort(400)

    ok = cast_service.set_volume(device, value)
    return jsonify(ok=ok)
