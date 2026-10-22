from flask import Blueprint

bp = Blueprint("library", __name__, url_prefix="/library")

from app.library import routes  # noqa: E402,F401
