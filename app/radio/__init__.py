from flask import Blueprint

bp = Blueprint("radio", __name__, url_prefix="/radio")

from app.radio import routes  # noqa: E402,F401
