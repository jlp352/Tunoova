"""Tri memorise par ecran (Musique, Podcast, Radio...) : Users.SortPrefs,
un seul JSON {"library": "album_za", "podcast": "za", ...} - un nouvel ecran
a son tri sans changement de schema."""
from flask import request
from flask_login import current_user

from app.extensions import db


def requested_sort(page, allowed, default):
    """Tri demande (?sort=), sinon le dernier tri choisi sur cet ecran.
    N'enregistre que si la requete vient de la fenetre de tri (?save_sort=1) :
    les liens retour imposent aussi ?sort= sans changer la preference."""
    prefs = current_user.SortPrefs or {}
    sort = request.args.get("sort")
    if sort not in allowed:
        saved = prefs.get(page)
        return saved if saved in allowed else default
    if request.args.get("save_sort") == "1" and prefs.get(page) != sort:
        # nouveau dict : SQLAlchemy ne detecte pas une modification en place
        current_user.SortPrefs = {**prefs, page: sort}
        db.session.commit()
    return sort


def saved_sort(page, default):
    return (current_user.SortPrefs or {}).get(page) or default
