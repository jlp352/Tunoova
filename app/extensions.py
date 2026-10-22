import sqlite3

from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import event
from sqlalchemy.engine import Engine
from flask_login import LoginManager
from flask_wtf import CSRFProtect

db = SQLAlchemy()
login_manager = LoginManager()
csrf = CSRFProtect()

login_manager.login_view = "auth.signin"
# Pas de flash "Veuillez vous connecter..." lors d'une redirection vers le
# login : ce message n'a de sens que pour un deep-link vers une page precise,
# mais s'affiche aussi (de facon illogique) au tout premier chargement du
# site puisque "/" est elle-meme protegee par @login_required.
login_manager.login_message = None


@event.listens_for(Engine, "connect")
def _sqlite_pragmas(dbapi_connection, _record):
    """SQLite partage entre les requetes et les jobs planifies (scan MP3,
    recherche des pochettes...) : en mode WAL, les lectures ne sont plus
    bloquees par une ecriture en cours (et inversement), et une ecriture
    attend jusqu'a 30 s qu'une autre se termine au lieu d'echouer apres 5 s
    en "database is locked"."""
    if not isinstance(dbapi_connection, sqlite3.Connection):
        return
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA busy_timeout=30000")
    cursor.close()
