import os

import bcrypt
from flask import request, redirect, url_for, flash, current_app, jsonify

from app.admin import bp
from app.admin.decorators import admin_required, start_admin_session
from app.admin import config_settings
from app.admin.password_policy import is_strong_password
from app.config import BASE_DIR
from app.i18n import translate


def _is_ajax() -> bool:
    return request.headers.get("X-Requested-With") == "XMLHttpRequest"


def _fail(message: str):
    if _is_ajax():
        return jsonify(ok=False, message=message)
    flash(message, "error")
    return redirect(url_for("admin.dashboard"))


def _succeed(message: str):
    if _is_ajax():
        return jsonify(ok=True, message=message)
    flash(message, "success")
    return redirect(url_for("admin.dashboard"))


def _resolve_path(path: str) -> str:
    resolved = path if os.path.isabs(path) else os.path.join(BASE_DIR, path)
    return os.path.normpath(resolved)


@bp.route("/settings/smtp", methods=["POST"])
@admin_required
def update_smtp_settings():
    for field in ("SMTP_HOST", "SMTP_PORT", "SMTP_USER", "SMTP_FROM"):
        config_settings.set_env(field, request.form.get(field, "").strip())

    password = request.form.get("SMTP_PASSWORD", "")
    if password:  # champ laisse vide = mot de passe SMTP inchange
        config_settings.set_env("SMTP_PASSWORD", password)

    return _succeed(translate("admin_msg_smtp_updated"))


@bp.route("/settings/mp3-path", methods=["POST"])
@admin_required
def update_mp3_settings():
    mp3_path = request.form.get("MP3_LIBRARY_PATH", "").strip()

    if not mp3_path:
        return _fail(translate("admin_msg_path_empty"))

    resolved = _resolve_path(mp3_path)
    if not os.path.isdir(resolved):
        return _fail(translate("admin_msg_folder_missing", path=resolved))

    config_settings.set_env("MP3_LIBRARY_PATH", mp3_path)
    return _succeed(translate("admin_msg_mp3_updated"))


@bp.route("/settings/backup", methods=["POST"])
@admin_required
def update_backup_settings():
    backup_path = request.form.get("BACKUP_PATH", "").strip()
    retention = request.form.get("BACKUP_RETENTION_DAYS", "").strip()

    if not backup_path:
        return _fail(translate("admin_msg_path_empty"))
    if not retention.isdigit() or int(retention) <= 0:
        return _fail(translate("admin_msg_retention_invalid"))

    resolved = _resolve_path(backup_path)
    try:
        os.makedirs(resolved, exist_ok=True)
    except OSError as exc:
        return _fail(translate("admin_msg_backup_folder_error", path=resolved, error=exc))

    config_settings.set_env("BACKUP_PATH", backup_path)
    config_settings.set_env("BACKUP_RETENTION_DAYS", retention)

    # BACKUP_PATH/BACKUP_RETENTION_DAYS sont lus via current_app.config par
    # backup_service (pas directement via os.environ) : mis a jour ici pour
    # un effet immediat sur ce process, sans attendre un redemarrage.
    current_app.config["BACKUP_PATH"] = backup_path
    current_app.config["BACKUP_RETENTION_DAYS"] = int(retention)

    return _succeed(translate("admin_msg_backup_updated"))


@bp.route("/settings/admin-account", methods=["POST"])
@admin_required
def update_admin_account():
    current_password = request.form.get("current_password", "")
    new_email = request.form.get("ADMIN_EMAIL", "").strip().lower()
    new_password = request.form.get("new_password", "")
    confirm_password = request.form.get("confirm_password", "")

    # Relu directement depuis le fichier .env (pas depuis current_app.config,
    # qui peut etre perime si .env a ete modifie hors de l'appli) : le hash du
    # mot de passe actuel saisi doit correspondre a ce qui est reellement en
    # .env au moment de la validation.
    admin_hash = config_settings.get_env_from_file("ADMIN_PASSWORD_HASH")
    if not admin_hash or not bcrypt.checkpw(current_password.encode("utf-8"), admin_hash.encode("utf-8")):
        return _fail(translate("admin_msg_current_password_incorrect"))

    if not new_email or "@" not in new_email:
        return _fail(translate("admin_msg_email_invalid"))

    password_changed = False
    if new_password or confirm_password:
        if not is_strong_password(new_password):
            return _fail(translate("admin_msg_password_requirement"))
        if new_password != confirm_password:
            return _fail(translate("admin_password_mismatch"))

        new_hash = bcrypt.hashpw(new_password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")
        config_settings.set_env("ADMIN_PASSWORD_HASH", new_hash)
        current_app.config["ADMIN_PASSWORD_HASH"] = new_hash
        password_changed = True

    # ADMIN_EMAIL est lu via current_app.config par admin.login (pas
    # directement via os.environ) : mis a jour ici pour un effet immediat.
    config_settings.set_env("ADMIN_EMAIL", new_email)
    current_app.config["ADMIN_EMAIL"] = new_email

    # Message explicite sur ce qui a reellement change : laisser les 2 champs
    # mot de passe vides = mot de passe INCHANGE (jamais mis a blanc), mais un
    # message generique aurait pu laisser croire le contraire.
    # Le changement de compte invalide toutes les sessions admin (cf.
    # decorators._credentials_fingerprint) : nouveau cookie pour celle-ci.
    if password_changed:
        return start_admin_session(_succeed(translate("admin_msg_account_updated_both")))
    return start_admin_session(_succeed(translate("admin_msg_account_updated_email_only")))


@bp.route("/settings/purge-logs", methods=["POST"])
@admin_required
def purge_logs():
    """Purge manuelle des tables Log / LoginHistory (fenetre du tableau de bord)."""
    from app import logging_util

    tables = [t for t in request.form.getlist("tables") if t in ("Log", "LoginHistory")]
    if not tables:
        return _fail(translate("admin_purge_logs_no_table"))
    older_than_days = None if request.form.get("scope") == "all" else logging_util.LOG_RETENTION_DAYS
    deleted = logging_util.purge_logs(older_than_days, tables)
    return _succeed(translate("admin_purge_logs_done", log=deleted["Log"], login=deleted["LoginHistory"]))
