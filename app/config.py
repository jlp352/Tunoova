import os

BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))


class Config:
    SECRET_KEY = os.environ["SESSION_SECRET_KEY"]

    _db_path = os.environ.get("DATABASE_PATH", "db/tunoova.db")
    if not os.path.isabs(_db_path):
        _db_path = os.path.join(BASE_DIR, _db_path)
    SQLALCHEMY_DATABASE_URI = f"sqlite:///{_db_path}"
    SQLALCHEMY_TRACK_MODIFICATIONS = False

    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"
    REMEMBER_COOKIE_DURATION = 60 * 60 * 24 * 30  # 30 jours, glissant (cf. CDC §2)
    REMEMBER_COOKIE_HTTPONLY = True
    REMEMBER_COOKIE_SAMESITE = "Lax"
    REMEMBER_COOKIE_REFRESH_EACH_REQUEST = True
    # Jeton CSRF valable toute la session (defaut Flask-WTF : 1h). L'appli
    # navigue en htmx sans recharger base.html : le jeton de <meta csrf-token>
    # n'est jamais renouvele, et au bout d'1h tous les POST fetch (/play/state,
    # /library/<id>/play...) partaient en 400 "The CSRF token has expired".
    WTF_CSRF_TIME_LIMIT = None

    DEBUG = os.environ.get("FLASK_DEBUG", "0") == "1"
    # Flag "Secure" pose requete par requete (app/remote_access.py,
    # SecureCookieMiddleware) : en HTTPS / via proxy (acces distant) oui, en
    # HTTP direct sur le reseau local non - sinon le navigateur ne renvoie
    # plus le cookie et la connexion par http://<ip-locale> est impossible.
    SESSION_COOKIE_SECURE = False
    REMEMBER_COOKIE_SECURE = False

    ADMIN_EMAIL = os.environ.get("ADMIN_EMAIL", "")
    ADMIN_PASSWORD_HASH = os.environ.get("ADMIN_PASSWORD_HASH", "")

    MP3_LIBRARY_PATH = os.environ.get("MP3_LIBRARY_PATH", "./mp3")
    # Taille max d'une requete : borne un lot d'upload de l'explorateur MP3
    # (Admin > MP3, qui envoie les fichiers par lots plus petits que cette limite).
    MP3_UPLOAD_MAX_MB = int(os.environ.get("MP3_UPLOAD_MAX_MB", "200"))
    MAX_CONTENT_LENGTH = MP3_UPLOAD_MAX_MB * 1024 * 1024

    BACKUP_PATH = os.environ.get("BACKUP_PATH", "db/backups")
    BACKUP_RETENTION_DAYS = int(os.environ.get("BACKUP_RETENTION_DAYS", "7"))

    # URL de base des flux Cast (ex. http://192.168.1.10:5000) si l'IP locale
    # detectee n'est pas joignable par les enceintes - cf. cast_service.cast_base_url.
    CAST_BASE_URL = os.environ.get("CAST_BASE_URL", "")

    # Nombre de reverse proxies (Caddy, Tailscale serve/funnel, nginx...)
    # places devant l'appli. 0 (defaut) = acces direct (LAN, VPS sans proxy) :
    # les en-tetes X-Forwarded-* sont ignores, sinon un client pourrait
    # falsifier son IP et contourner le blocage par IP (app/login_throttle.py).
    TRUSTED_PROXY_COUNT = int(os.environ.get("TRUSTED_PROXY_COUNT", "0"))
    # Mode d'acces a distance (off / external / private / public) et nom DNS
    # du reverse proxy externe : cf. app/remote_access.py.
    REMOTE_ACCESS_MODE = os.environ.get("REMOTE_ACCESS_MODE", "")
    PUBLIC_HOSTNAME = os.environ.get("PUBLIC_HOSTNAME", "")

    LANGUAGES = ["en", "fr"]
