from flask import Blueprint

bp = Blueprint("play", __name__, url_prefix="/play")

from app.play import routes  # noqa: E402,F401
