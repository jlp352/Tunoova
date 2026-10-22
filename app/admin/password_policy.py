"""Regle de complexite des mots de passe definis/changes depuis l'Espace
Admin (compte admin lui-meme, et mots de passe utilisateurs forces sans
email). Cle de traduction du message d'erreur : admin_msg_password_requirement."""

import re

PASSWORD_MIN_LENGTH = 8


def is_strong_password(password: str) -> bool:
    if len(password) < PASSWORD_MIN_LENGTH:
        return False
    has_upper = any(c.isupper() for c in password)
    has_lower = any(c.islower() for c in password)
    has_digit = any(c.isdigit() for c in password)
    has_special = bool(re.search(r"[^A-Za-z0-9]", password))
    return has_upper and has_lower and has_digit and has_special
