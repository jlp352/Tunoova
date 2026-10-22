from flask import render_template, request, redirect, url_for, flash
from flask_login import login_required, current_user

from app.settings import bp
from app.extensions import db
from app.logging_util import log_screen_view
from app.version import APP_VERSION, APP_RELEASE_DATE, APP_AUTHOR, APP_REPO_URL

VALID_QUALITIES = ("auto", "low", "high")
VALID_LANGUAGES = ("fr", "en")


@bp.route("/", methods=["GET", "POST"])
@login_required
def index():
    log_screen_view("Settings")

    if request.method == "POST":
        current_user.DarkMode = request.form.get("dark_mode") == "on"
        current_user.YoutubeMusicEnabled = request.form.get("youtube_music") == "on"

        quality = request.form.get("stream_quality", "auto")
        if quality in VALID_QUALITIES:
            current_user.StreamQuality = quality

        langue = request.form.get("langue")
        if langue in VALID_LANGUAGES:
            current_user.Langue = langue

        db.session.commit()
        if request.headers.get("HX-Request") == "true":
            # Sauvegarde automatique a chaque changement : rien a reafficher.
            return "", 204
        flash("settings_saved", "success")
        return redirect(url_for("settings.index"))

    return render_template(
        "settings/index.html",
        app_version=APP_VERSION,
        app_release_date=APP_RELEASE_DATE,
        app_author=APP_AUTHOR,
        app_repo_url=APP_REPO_URL,
    )
