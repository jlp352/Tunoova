from flask import Blueprint

bp = Blueprint("userprofile", __name__, url_prefix="/userprofile")

from app.userprofile import routes  # noqa: E402,F401
