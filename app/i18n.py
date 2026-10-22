import json
import os
import re

from flask import request, session
from flask_login import current_user

_TRANSLATIONS_DIR = os.path.join(os.path.dirname(__file__), "translations")
_catalogs: dict[str, dict[str, str]] = {}


def _load_catalogs():
    """Charge un catalogue par fichier <code>.json present dans app/translations/
    (au lieu d'une liste ("en", "fr") figee) : ajouter une langue = juste ajouter
    son fichier JSON, sans toucher au code."""
    if _catalogs:
        return
    for filename in sorted(os.listdir(_TRANSLATIONS_DIR)):
        if not filename.endswith(".json"):
            continue
        lang = filename[: -len(".json")]
        with open(os.path.join(_TRANSLATIONS_DIR, filename), encoding="utf-8") as f:
            _catalogs[lang] = json.load(f)


def available_locales() -> list[str]:
    _load_catalogs()
    return list(_catalogs.keys())


def current_locale() -> str:
    """Langue utilisateur si connecte, sinon preference explicite en session
    (utilise par l'Espace Admin, qui n'a pas de compte Users/Langue - cf.
    admin.set_locale), sinon detection navigateur, sinon anglais (CDC §4).

    Sur une route /admin/*, la preference explicite (session['admin_locale'])
    passe AVANT Users.Langue : le meme navigateur peut avoir une session
    Flask-Login active (compte utilisateur normal, ex. teste en parallele)
    en plus de la session admin - sans cette priorite, le selecteur de langue
    de l'Espace Admin semblait ne rien faire, court-circuite par la langue de
    ce compte utilisateur sans rapport avec l'admin."""
    _load_catalogs()
    session_locale = session.get("admin_locale")
    if request.blueprint == "admin" and session_locale in available_locales():
        return session_locale
    if current_user.is_authenticated and getattr(current_user, "Langue", None):
        return current_user.Langue
    if session_locale in available_locales():
        return session_locale
    best = request.accept_languages.best_match(available_locales())
    return best or "en"


def translate(key: str, locale: str | None = None, **kwargs) -> str:
    """`locale` force la langue du texte renvoye au lieu de celle de la requete
    en cours (ex: email envoye a un utilisateur dans SA langue de profil,
    depuis une action admin qui a sa propre langue de session)."""
    _load_catalogs()
    locale = locale or current_locale()
    catalog = _catalogs.get(locale, _catalogs["en"])
    text = catalog.get(key, _catalogs["en"].get(key, key))
    return text.format(**kwargs) if kwargs else text


def _slugify(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", value.lower()).strip("_")


def translate_choice(prefix: str, name: str) -> str:
    """Traduit une valeur de reference (Hobby.HobbyName, MusicGenre.MusicGenreName...)
    stockee en anglais en base : cle `<prefix>_<slug(name)>` dans les catalogues,
    repli sur le nom brut si l'entree n'a pas (encore) de traduction - une nouvelle
    ligne ajoutee en base sans traduction reste donc lisible plutot que de casser
    l'affichage."""
    _load_catalogs()
    locale = current_locale()
    key = f"{prefix}_{_slugify(name)}"
    catalog = _catalogs.get(locale, _catalogs["en"])
    return catalog.get(key, _catalogs["en"].get(key, name))


def init_app(app):
    _load_catalogs()
    app.jinja_env.globals["t"] = translate
    app.jinja_env.globals["t_choice"] = translate_choice
    app.jinja_env.globals["current_locale"] = current_locale
