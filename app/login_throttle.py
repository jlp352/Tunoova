"""Blocage anti-bruteforce calcule a partir de LoginHistory : echecs de
connexion comptes par adresse IP (appli et admin) et par email pour le
compte admin, qui n'a pas de ligne dans Users (identifiants dans la config).
Meme regle que Users.register_failed_login : N echecs consecutifs (depuis la
derniere connexion reussie) bloquent pendant LOCKOUT_DURATION. Les tentatives
refusees pendant un blocage ne sont pas comptees comme des echecs (pas de
prolongation du blocage)."""

import math
from datetime import datetime, timedelta

from app.models import LoginHistory, LOCKOUT_DURATION, MAX_FAILED_LOGIN_ATTEMPTS

# Au-dela, des echecs isoles ne comptent plus (borne aussi la requete).
LOOKBACK = timedelta(hours=24)

# Types d'evenement LoginHistory par perimetre : (echecs, succes, seuil).
APP_SCOPE = (("invalid_user", "invalid_password"), ("success",), MAX_FAILED_LOGIN_ATTEMPTS)
ADMIN_SCOPE = (("admin_invalid",), ("admin_success",), MAX_FAILED_LOGIN_ATTEMPTS)


def _locked_until(scope, column, value) -> datetime | None:
    failure_types, success_types, max_attempts = scope
    now = datetime.utcnow()
    since = now - LOOKBACK

    last_success = (
        LoginHistory.query.with_entities(LoginHistory.EventDate)
        .filter(column == value, LoginHistory.EventType.in_(success_types), LoginHistory.EventDate >= since)
        .order_by(LoginHistory.EventDate.desc())
        .first()
    )
    if last_success:
        since = last_success[0]

    failures = (
        LoginHistory.query.with_entities(LoginHistory.EventDate)
        .filter(column == value, LoginHistory.EventType.in_(failure_types), LoginHistory.EventDate > since)
        .order_by(LoginHistory.EventDate.asc())
        .all()
    )

    count = 0
    locked_until = None
    for (event_date,) in failures:
        if locked_until and event_date >= locked_until:
            count = 0
            locked_until = None
        count += 1
        if count >= max_attempts:
            locked_until = event_date + LOCKOUT_DURATION
    return locked_until if locked_until and locked_until > now else None


def ip_locked_until(scope, ip_address: str | None) -> datetime | None:
    if not ip_address:
        return None
    return _locked_until(scope, LoginHistory.IpAddress, ip_address)


def email_locked_until(scope, email: str) -> datetime | None:
    return _locked_until(scope, LoginHistory.Email, email)


def remaining_minutes(locked_until: datetime) -> int:
    return max(math.ceil((locked_until - datetime.utcnow()).total_seconds() / 60), 1)


# Demandes envoyees par email a l'admin depuis l'ecran de connexion (creation
# de compte, reset de mot de passe) : limites pour qu'un bot ne puisse pas
# inonder la boite de l'admin. Seules les demandes transmises sont comptees.
REQUEST_EVENT_TYPES = ("account_request", "reset_request")
REQUEST_MAX_PER_IP = 3              # par REQUEST_IP_WINDOW
REQUEST_IP_WINDOW = timedelta(hours=1)
REQUEST_MAX_PER_EMAIL = 1           # par REQUEST_EMAIL_WINDOW et par type
REQUEST_EMAIL_WINDOW = timedelta(hours=24)
REQUEST_MAX_TOTAL = 20              # toutes IP confondues (bots a IP tournantes)
REQUEST_TOTAL_WINDOW = timedelta(hours=24)


def _count_requests(event_types, since, *filters) -> int:
    return LoginHistory.query.filter(
        LoginHistory.EventType.in_(event_types), LoginHistory.EventDate >= since, *filters
    ).count()


def admin_request_allowed(event_type: str, ip_address: str | None, email: str) -> bool:
    now = datetime.utcnow()
    if ip_address and _count_requests(
        REQUEST_EVENT_TYPES, now - REQUEST_IP_WINDOW, LoginHistory.IpAddress == ip_address
    ) >= REQUEST_MAX_PER_IP:
        return False
    if _count_requests(
        (event_type,), now - REQUEST_EMAIL_WINDOW, LoginHistory.Email == email
    ) >= REQUEST_MAX_PER_EMAIL:
        return False
    return _count_requests(REQUEST_EVENT_TYPES, now - REQUEST_TOTAL_WINDOW) < REQUEST_MAX_TOTAL
