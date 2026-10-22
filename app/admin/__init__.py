from flask import Blueprint

bp = Blueprint("admin", __name__, url_prefix="/admin")

from app.admin import routes, db_browser, jobs, app_settings, radio_plugins, mp3_browser, update, backups, remote_access  # noqa: E402,F401
