"""Logique partagee entre First Login et UserProfile (CDC §8 : "memes regles
que First Login") : upload de photo de profil et listes referentielles."""

import os
import uuid

from werkzeug.utils import secure_filename

from app.config import BASE_DIR
from app.models import AgeRange, Hobby, MusicGenre, Country

# Hors de app/static : les photos de profil ne doivent pas etre accessibles
# directement par le serveur web ni versionnees dans git (cf. .gitignore "db/*").
UPLOAD_DIR = os.path.join(BASE_DIR, "db", "profile_photos")
ALLOWED_PHOTO_EXT = {"png", "jpg", "jpeg", "gif", "webp"}

# Categories mises en avant (demande utilisateur) dans la listbox Genres musicaux ;
# les autres categories suivent, triees A-Z.
MUSIC_GENRE_FEATURED_CATEGORIES = ["Popular Music", "Rock & Metal", "Electronic Music"]


def _music_genre_category_key(category: str):
    if category in MUSIC_GENRE_FEATURED_CATEGORIES:
        return (0, MUSIC_GENRE_FEATURED_CATEGORIES.index(category))
    return (1, category)


def save_profile_photo(file_storage) -> str | None:
    if not file_storage or not file_storage.filename:
        return None
    ext = secure_filename(file_storage.filename).rsplit(".", 1)[-1].lower()
    if ext not in ALLOWED_PHOTO_EXT:
        return None
    os.makedirs(UPLOAD_DIR, exist_ok=True)
    filename = f"{uuid.uuid4().hex}.{ext}"
    file_storage.save(os.path.join(UPLOAD_DIR, filename))
    return filename


def get_reference_lists():
    age_ranges = AgeRange.query.filter_by(Used=True).all()
    hobbies = Hobby.query.filter_by(Used=True).order_by(Hobby.Category, Hobby.HobbyName).all()
    music_genres = MusicGenre.query.filter_by(Used=True).order_by(MusicGenre.MusicGenreName).all()
    music_genres.sort(key=lambda g: _music_genre_category_key(g.Category))
    countries = Country.query.filter_by(Used=True).order_by(Country.CountryName).all()
    return age_ranges, hobbies, music_genres, countries
