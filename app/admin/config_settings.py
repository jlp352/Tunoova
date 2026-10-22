"""Lecture/ecriture des reglages applicatifs stockes en .env (SMTP, chemins,
retention backup) depuis l'Espace Admin. Meme mecanisme que app/scheduler.py :
persistance via dotenv.set_key + mise a jour de os.environ pour un effet
immediat sur le process en cours, sans redemarrage."""

import os

from dotenv import set_key, dotenv_values

from app.config import BASE_DIR

_ENV_PATH = os.path.join(BASE_DIR, ".env")


def get_env(key: str, default: str = "") -> str:
    return os.environ.get(key, default)


def set_env(key: str, value: str) -> None:
    set_key(_ENV_PATH, key, value, quote_mode="never")
    os.environ[key] = value


def get_env_from_file(key: str, default: str = "") -> str:
    """Relit la valeur directement depuis le fichier .env sur disque, plutot
    que depuis os.environ (qui peut etre perime si .env a ete modifie hors
    de l'appli). Utilise pour verifier le mot de passe admin actuel avant
    tout changement de compte (cf. update_admin_account)."""
    return dotenv_values(_ENV_PATH).get(key) or default
