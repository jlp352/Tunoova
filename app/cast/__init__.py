from flask import Blueprint

bp = Blueprint("cast", __name__, url_prefix="/cast")

from app.cast import routes  # noqa: E402,F401
