from datetime import datetime

from flask import render_template, request, redirect, url_for, flash, jsonify, abort
from flask_login import login_required, current_user

from app.radio import bp
from app.extensions import db
from app.models import RadioUsers, Country
from app.logging_util import log_screen_view
from app import radio_service, play_service, now_playing
from app.sort_prefs import requested_sort

SORTS = ("newest", "oldest", "az", "za")


def _user_country_api_name() -> str | None:
    if not current_user.Country:
        return None
    ref = Country.query.filter_by(CountryName=current_user.Country).first()
    return (ref.ApiName if ref and ref.ApiName else current_user.Country)


def _get_subscriptions(users_id: int, q: str, sort: str):
    query = radio_service.user_radios(users_id)

    if q and len(q) >= 3:
        query = query.filter(RadioUsers.RadioName.ilike(f"%{q}%"))

    if sort == "za":
        return query.order_by(RadioUsers.RadioName.desc()).all()

    if sort == "newest":
        return query.order_by(RadioUsers.LastListen.desc().nullslast(), RadioUsers.RadioName.asc()).all()

    if sort == "oldest":
        return query.order_by(RadioUsers.LastListen.asc().nullsfirst(), RadioUsers.RadioName.asc()).all()

    return query.order_by(RadioUsers.RadioName.asc()).all()


def _search_api(q: str) -> list[dict]:
    if not q or len(q) < 3:
        return []
    return radio_service.search_radio_browser(q, country=_user_country_api_name())


@bp.route("/")
@login_required
def index():
    log_screen_view("Radio")
    q = request.args.get("q", "").strip()
    sort = requested_sort("radio", SORTS, "az")
    subscriptions = _get_subscriptions(current_user.UsersId, q, sort)
    return render_template(
        "radio/index.html",
        subscriptions=subscriptions,
        add_results=_search_api(q),
        q=q,
        sort=sort,
    )


@bp.route("/list")
@login_required
def list_fragment():
    """Fragment htmx utilise par la recherche live et le tri (SearchRadio / SortBy, CDC §8)."""
    q = request.args.get("q", "").strip()
    sort = requested_sort("radio", SORTS, "az")
    subscriptions = _get_subscriptions(current_user.UsersId, q, sort)
    return render_template(
        "radio/_list.html",
        subscriptions=subscriptions,
        add_results=_search_api(q),
        q=q,
    )


@bp.route("/add/advanced", methods=["GET", "POST"])
@login_required
def add_advanced():
    log_screen_view("AddAdvancedRadio")
    error = None
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        stream_url = request.form.get("stream_url", "").strip()
        image_url = request.form.get("image_url", "").strip() or None

        if not name or not stream_url:
            error = "Nom et URL du flux sont requis."
        else:
            subscription, created = radio_service.add_radio_for_user(current_user.UsersId, name, stream_url, image_url)
            flash(
                f"« {name} » ajoutée." if created else f"« {name} » est déjà dans vos radios.",
                "success" if created else "info",
            )
            return redirect(url_for("radio.index"))

    return render_template("radio/add_advanced.html", error=error, values=request.form)


@bp.route("/<int:radio_users_id>/edit", methods=["GET", "POST"])
@login_required
def edit(radio_users_id):
    subscription = RadioUsers.query.filter_by(
        RadioUsersId=radio_users_id, UsersId=current_user.UsersId
    ).first_or_404()
    log_screen_view("EditRadio")

    error = None
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        stream_url = request.form.get("stream_url", "").strip()
        image_url = request.form.get("image_url", "").strip() or None

        if not name or not stream_url:
            error = "Nom et URL du flux sont requis."
        else:
            radio_service.update_radio_subscription(subscription, name, stream_url, image_url)
            flash(f"« {name} » mise à jour.", "success")
            return redirect(url_for("radio.index"))

    return render_template("radio/edit.html", subscription=subscription, error=error)


@bp.route("/<int:radio_users_id>/play", methods=["POST"])
@login_required
def play(radio_users_id):
    radio = RadioUsers.query.filter_by(
        RadioUsersId=radio_users_id, UsersId=current_user.UsersId
    ).first_or_404()
    return _start_radio(radio)


@bp.route("/<int:radio_users_id>/now-playing")
@login_required
def now_playing_info(radio_users_id):
    """Titre en cours (plug-in de la radio, cf. app/now_playing.py), interroge
    periodiquement par le mini-lecteur."""
    radio = RadioUsers.query.filter_by(
        RadioUsersId=radio_users_id, UsersId=current_user.UsersId
    ).first_or_404()
    result = now_playing.now_playing(radio.radio)
    return jsonify(now_playing=result, subtitle=now_playing.live_subtitle(radio.LinkURL))


@bp.route("/add/play", methods=["POST"])
@login_required
def add_and_play():
    """Clic sur un resultat de recherche : la radio est ajoutee aux favorites
    (si elle n'y est pas deja) et la lecture demarre."""
    name = request.args.get("name", "").strip()
    stream_url = request.args.get("stream_url", "").strip()
    image_url = request.args.get("image_url", "").strip() or None
    if not name or not stream_url:
        abort(400)
    radio, _created = radio_service.add_radio_for_user(current_user.UsersId, name, stream_url, image_url)
    return _start_radio(radio)


def _start_radio(radio):
    # Chaque clic compte une ecoute (meme si la radio joue deja) : compteur
    # de l'utilisateur et compteur global de la radio (ecran Play).
    radio.CountListen = (radio.CountListen or 0) + 1
    radio.LastListen = datetime.utcnow()
    if radio.RadioStatus in radio_service.NOTIFY_STATUSES:
        # "Ecouter" depuis la notification de partage : la radio entre dans
        # la liste (si elle n'y etait pas) et la notification disparait.
        radio.RadioStatus = "Read"
    if radio.radio is not None:
        radio.radio.RadioCount = (radio.radio.RadioCount or 0) + 1
    db.session.commit()

    play_service.set_current_play(current_user.UsersId, "radio", radio.RadioUsersId, radio.LinkURL)

    return jsonify(
        {
            "stream_url": radio.LinkURL,
            "title": radio.RadioName,
            "subtitle": now_playing.live_subtitle(radio.LinkURL),
            "thumbnail": radio.LinkImage,
            "play_url": url_for("play.show", content_type="radio", item_id=radio.RadioUsersId),
        }
    )


@bp.route("/<int:radio_users_id>/delete", methods=["POST"])
@login_required
def delete(radio_users_id):
    subscription = RadioUsers.query.filter_by(
        RadioUsersId=radio_users_id, UsersId=current_user.UsersId
    ).first_or_404()
    name = subscription.RadioName
    radio_service.delete_radio_subscription(subscription)
    if request.headers.get("HX-Request") == "true":
        return "", 200
    flash(f"« {name} » supprimée.", "success")
    return redirect(url_for("radio.index"))
