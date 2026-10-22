from datetime import datetime, timedelta

from flask import request
from flask_login import current_user
from user_agents import parse as parse_user_agent

from app.extensions import db
from app.models import Log, LoginHistory


def _device_and_os():
    ua = parse_user_agent(request.headers.get("User-Agent", ""))
    device = ua.device.family if ua.device.family and ua.device.family != "Other" else ua.browser.family
    os_name = f"{ua.os.family} {ua.os.version_string}".strip()
    return device, os_name


def log_screen_view(frame: str) -> None:
    """Ajoute une entree dans Log a chaque affichage d'ecran (CDC §7)."""
    if not current_user.is_authenticated:
        return

    device, os_name = _device_and_os()

    entry = Log(
        UsersId=current_user.UsersId,
        Frame=frame,
        LogDate=datetime.utcnow(),
        Device=device,
        IPaddress=request.remote_addr,
        OS=os_name,
    )
    db.session.add(entry)
    db.session.commit()


def log_login_event(event_type: str, email: str, user=None) -> None:
    """Ajoute une entree dans LoginHistory : tentative de connexion (reussie,
    refusee, compte verrouille) ou reprise de session via cookie
    "remember me". Permet de reperer une activite suspecte (bruteforce,
    connexions depuis des IP/appareils inhabituels...)."""
    device, os_name = _device_and_os()

    entry = LoginHistory(
        UsersId=user.UsersId if user else None,
        Email=email,
        EventType=event_type,
        EventDate=datetime.utcnow(),
        IpAddress=request.remote_addr,
        Device=device,
        OS=os_name,
    )
    db.session.add(entry)
    db.session.commit()


LOG_RETENTION_DAYS = 30


def purge_logs(older_than_days: int | None = LOG_RETENTION_DAYS, tables=("Log", "LoginHistory")) -> dict:
    """Supprime les entrees de Log et/ou LoginHistory plus vieilles que
    `older_than_days` jours (toutes si None). Utilise par le job planifie
    log_purge (app/scheduler.py) et la purge manuelle de l'admin.
    Retourne le nombre de lignes supprimees par table."""
    cutoff = datetime.utcnow() - timedelta(days=older_than_days) if older_than_days is not None else None
    deleted = {"Log": 0, "LoginHistory": 0}
    if "Log" in tables:
        query = Log.query if cutoff is None else Log.query.filter(Log.LogDate < cutoff)
        deleted["Log"] = query.delete(synchronize_session=False)
    if "LoginHistory" in tables:
        query = LoginHistory.query if cutoff is None else LoginHistory.query.filter(LoginHistory.EventDate < cutoff)
        deleted["LoginHistory"] = query.delete(synchronize_session=False)
    db.session.commit()
    return deleted
