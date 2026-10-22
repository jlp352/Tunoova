from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired
from flask import current_app

ACTIVATION_MAX_AGE = 24 * 3600  # 24h (CDC §9)
RESET_MAX_AGE = 3600  # 1h (CDC §9)


def _serializer(salt: str) -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(current_app.config["SECRET_KEY"], salt=salt)


def make_activation_token(users_id: int) -> str:
    return _serializer("account-activation").dumps({"uid": users_id})


def read_activation_token(token: str) -> int | None:
    try:
        data = _serializer("account-activation").loads(token, max_age=ACTIVATION_MAX_AGE)
    except (BadSignature, SignatureExpired):
        return None
    return data.get("uid")


def make_reset_token(users_id: int, current_password_hash: str) -> str:
    """Le hash du mot de passe courant est inclus dans la signature : le lien
    devient invalide des que le mot de passe change, ce qui le rend a usage
    unique sans avoir besoin d'une colonne dediee en base."""
    return _serializer("password-reset").dumps({"uid": users_id, "pwd": current_password_hash[-16:]})


def read_reset_token(token: str, current_password_hash: str) -> int | None:
    try:
        data = _serializer("password-reset").loads(token, max_age=RESET_MAX_AGE)
    except (BadSignature, SignatureExpired):
        return None
    if data.get("pwd") != current_password_hash[-16:]:
        return None
    return data.get("uid")


# Fonctionnalite Cast (CDC §2) : un device Cast (Bose SoundTouch, DLNA) va
# chercher lui-meme l'URL /media/cast/... et ne sait pas s'authentifier comme
# un navigateur (pas de cookie de session) - jeton signe a duree limitee dans
# l'URL a la place. Duree large (6h) : le temps qu'une session d'ecoute reste
# valable sans qu'un lien capture puisse rejouer indefiniment le contenu.
CAST_MEDIA_MAX_AGE = 6 * 3600


def make_cast_media_token(content_type: str, item_id: int, users_id: int) -> str:
    return _serializer("cast-media").dumps({"t": content_type, "id": item_id, "uid": users_id})


def read_cast_media_token(token: str, content_type: str, item_id: int) -> int | None:
    """Renvoie l'UsersId proprietaire si le jeton est valide et correspond
    bien au type/item demande, sinon None."""
    try:
        data = _serializer("cast-media").loads(token, max_age=CAST_MEDIA_MAX_AGE)
    except (BadSignature, SignatureExpired):
        return None
    if data.get("t") != content_type or data.get("id") != item_id:
        return None
    return data.get("uid")
