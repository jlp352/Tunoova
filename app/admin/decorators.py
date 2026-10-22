"""Session de l'Espace Admin, distincte de la session Flask des utilisateurs
de l'appli : cookie signe dedie, limite aux URL /admin et a duree courte
verifiee cote serveur (cf. Doc/Audit Tunoova.md, etape 5). Auparavant un
simple session["is_admin"] dans le cookie commun : un autre utilisateur du
meme navigateur heritait de l'acces admin, et une copie du cookie restait
valable 31 jours meme apres deconnexion. Flag Secure : pose selon la requete
par remote_access.SecureCookieMiddleware."""

import hashlib
from functools import wraps

from flask import current_app, redirect, request, url_for
from itsdangerous import BadSignature, URLSafeTimedSerializer

ADMIN_COOKIE = "tunoova_admin"
ADMIN_COOKIE_PATH = "/admin"
ADMIN_SESSION_MAX_AGE = 8 * 3600  # 8 h depuis la connexion, sans prolongation


def _serializer() -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(current_app.config["SECRET_KEY"], salt="admin-session")


def _credentials_fingerprint() -> str:
    """Empreinte du compte admin courant (email + hash du mot de passe) :
    changer l'un ou l'autre invalide toutes les sessions admin ouvertes."""
    data = f'{current_app.config["ADMIN_EMAIL"].strip().lower()}|{current_app.config["ADMIN_PASSWORD_HASH"]}'
    return hashlib.sha256(data.encode("utf-8")).hexdigest()[:32]


def is_admin() -> bool:
    token = request.cookies.get(ADMIN_COOKIE)
    if not token:
        return False
    try:
        data = _serializer().loads(token, max_age=ADMIN_SESSION_MAX_AGE)
    except BadSignature:  # inclut SignatureExpired
        return False
    return isinstance(data, dict) and data.get("fp") == _credentials_fingerprint()


def start_admin_session(response):
    """Pose un cookie admin neuf (connexion, ou changement du compte admin qui
    vient d'invalider le precedent)."""
    response.set_cookie(
        ADMIN_COOKIE,
        _serializer().dumps({"fp": _credentials_fingerprint()}),
        max_age=ADMIN_SESSION_MAX_AGE,
        path=ADMIN_COOKIE_PATH,
        httponly=True,
        samesite="Lax",
    )
    return response


def end_admin_session(response):
    """Supprime le cookie admin : deconnexion admin, mais aussi toute
    connexion/deconnexion d'un utilisateur de l'appli (changement de
    personne sur le navigateur)."""
    response.delete_cookie(
        ADMIN_COOKIE,
        path=ADMIN_COOKIE_PATH,
        httponly=True,
        samesite="Lax",
    )
    return response


def admin_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not is_admin():
            return redirect(url_for("admin.login"))
        return f(*args, **kwargs)

    return decorated
