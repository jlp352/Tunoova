"""Pack de bienvenue : quelques radios et podcasts ajoutes a un nouvel
utilisateur a la fin de son First Login, pour que ses listes ne soient pas
vides. Le contenu est choisi selon la langue de l'utilisateur et se modifie
dans app/starter_content.json (une entree par code langue, "en" par defaut)."""

import json
import logging
import os

from app import podcast_service, radio_service
from app.extensions import db

logger = logging.getLogger(__name__)

STARTER_CONTENT_PATH = os.path.join(os.path.dirname(__file__), "starter_content.json")
DEFAULT_LANGUAGE = "en"


def _load_pack(langue: str | None) -> dict:
    with open(STARTER_CONTENT_PATH, encoding="utf-8") as f:
        packs = json.load(f)
    return packs.get(langue) or packs.get(DEFAULT_LANGUAGE) or {}


def seed_starter_content(user) -> None:
    """Ajoute le pack de la langue de l'utilisateur. Un element en echec
    (flux injoignable, JSON invalide...) est ignore : le First Login ne doit
    jamais etre bloque par le pack de bienvenue."""
    try:
        pack = _load_pack(user.Langue)
    except (OSError, ValueError):
        logger.exception("Pack de bienvenue : lecture de %s en echec", STARTER_CONTENT_PATH)
        return

    for radio in pack.get("radios", []):
        try:
            radio_service.add_radio_for_user(user.UsersId, radio["name"], radio["url"], radio.get("image"))
        except Exception:
            db.session.rollback()
            logger.exception("Pack de bienvenue : ajout de la radio %s en echec", radio.get("name"))

    for podcast in pack.get("podcasts", []):
        try:
            subscription, created = podcast_service.add_podcast_for_user(
                user.UsersId, podcast["name"], podcast["rss"], podcast.get("image")
            )
            if created:
                podcast_service.sync_episodes(subscription, mark_new=False)
        except Exception:
            db.session.rollback()
            logger.exception("Pack de bienvenue : ajout du podcast %s en echec", podcast.get("name"))
