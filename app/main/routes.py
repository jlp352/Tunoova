from flask import render_template, redirect, url_for, request, jsonify
from flask_login import login_required, current_user

from app.main import bp
from app.models import PodcastUsers, RadioUsers, MusicUsers, MusicTrack
from app import radio_service, i18n
from app.logging_util import log_screen_view
from app.extensions import db

# Ordre par defaut des sections de l'Accueil, modifiable par la fenetre
# "Ordre" (memorise dans Users.SortPrefs["home"], cf. app/sort_prefs.py)
HOME_SECTIONS = ("podcast", "radio", "music")


def _home_order() -> list[str]:
    saved = (current_user.SortPrefs or {}).get("home") or []
    order = [s for s in saved if s in HOME_SECTIONS]
    return order + [s for s in HOME_SECTIONS if s not in order]


@bp.route("/")
@login_required
def home():
    if current_user.FirstLogin:
        return redirect(url_for("auth.firstlogin"))

    log_screen_view("Home")

    top_podcasts = (
        PodcastUsers.query.filter_by(UsersId=current_user.UsersId)
        .order_by(PodcastUsers.CountListen.desc())
        .limit(5)
        .all()
    )
    top_radios = (
        radio_service.user_radios(current_user.UsersId)
        .order_by(RadioUsers.CountListen.desc())
        .limit(5)
        .all()
    )
    top_music = (
        MusicUsers.query.join(MusicTrack, MusicUsers.TrackId == MusicTrack.TrackId)
        .filter(MusicUsers.UsersId == current_user.UsersId, MusicUsers.CountListen > 0, MusicTrack.IsAvailable.is_(True))
        .order_by(MusicUsers.CountListen.desc())
        .limit(10)
        .all()
    )
    # Artistes les plus ecoutes (ordre d'ecoute), avec leur morceau le plus
    # ecoute en secours d'image
    top_artists: dict[int, dict] = {}
    for row in top_music:
        top_artists.setdefault(row.track.ArtistId, {"artist": row.track.artist, "track": row.track})

    return render_template(
        "main/home.html",
        top_podcasts=top_podcasts,
        top_radios=top_radios,
        top_artists=list(top_artists.values()),
        home_order=_home_order(),
    )


@bp.route("/home/order", methods=["POST"])
@login_required
def home_order():
    order = [s for s in request.form.getlist("section") if s in HOME_SECTIONS]
    if sorted(order) == sorted(HOME_SECTIONS):
        # nouveau dict : SQLAlchemy ne detecte pas une modification en place
        current_user.SortPrefs = {**(current_user.SortPrefs or {}), "home": order}
        db.session.commit()
    return redirect(url_for("main.home"))


@bp.route("/manifest.webmanifest")
def manifest():
    """Manifeste d'application web (installation depuis Chrome, cf. base.html).
    Sans login : Chrome le telecharge sans cookies."""
    name = i18n.translate("app_name")
    response = jsonify({
        "name": name,
        "short_name": name,
        "start_url": "/",
        "scope": "/",
        "display": "standalone",
        "background_color": "#FAFAFC",
        "theme_color": "#5C4B87",
        "icons": [
            {"src": url_for("static", filename="brand/favicon-192.png"), "sizes": "192x192", "type": "image/png"},
            {"src": url_for("static", filename="brand/app-icon-512.png"), "sizes": "512x512", "type": "image/png"},
        ],
    })
    response.mimetype = "application/manifest+json"
    return response
