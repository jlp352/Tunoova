import secrets

import bcrypt
from flask import render_template, request, redirect, url_for, session, current_app, flash

from app.admin import bp
from app.admin.decorators import admin_required, end_admin_session, is_admin, start_admin_session
from app.extensions import db
from app.models import Users, MusicTrack, Podcast, Radio, PodcastUsers, EpisodesUsers, RadioUsers, MusicUsers, Play, Log, LoginHistory
from app.email_utils import send_email
from app.tokens import make_activation_token, make_reset_token
from app import backup_service, login_throttle, music_catalog, podcast_service, radio_service, remote_access, scheduler
from app.logging_util import log_login_event
from app.admin import config_settings
from app.admin.password_policy import is_strong_password
from app.url_util import safe_local_url
from app.version import APP_VERSION, APP_RELEASE_DATE, APP_AUTHOR, APP_REPO_URL
from app.i18n import available_locales, translate


@bp.route("/locale/<lang>")
def set_locale(lang):
    if lang in available_locales():
        session["admin_locale"] = lang
    return redirect(safe_local_url(request.referrer, url_for("admin.dashboard")))


@bp.route("/login", methods=["GET", "POST"])
def login():
    if is_admin():
        return redirect(url_for("admin.dashboard"))

    error = None
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        admin_email = current_app.config["ADMIN_EMAIL"].strip().lower()
        admin_hash = current_app.config["ADMIN_PASSWORD_HASH"]

        # Blocage par IP (tous emails confondus) et par compte admin : 5 echecs
        # = 15 min, cf. app/login_throttle.py. Le mot de passe n'est meme pas
        # verifie pendant un blocage.
        locked_until = login_throttle.ip_locked_until(login_throttle.ADMIN_SCOPE, request.remote_addr)
        if not locked_until and email == admin_email:
            locked_until = login_throttle.email_locked_until(login_throttle.ADMIN_SCOPE, admin_email)

        if locked_until:
            error = ("locked", login_throttle.remaining_minutes(locked_until))
            log_login_event("admin_locked", email)
        elif (
            admin_hash
            and email == admin_email
            and bcrypt.checkpw(password.encode("utf-8"), admin_hash.encode("utf-8"))
        ):
            log_login_event("admin_success", email)
            return start_admin_session(redirect(url_for("admin.dashboard")))
        else:
            error = ("invalid", None)
            log_login_event("admin_invalid", email)

    return render_template("admin/login.html", error=error)


@bp.route("/logout")
def logout():
    return end_admin_session(redirect(url_for("admin.login")))


@bp.route("/")
@admin_required
def dashboard():
    backups = backup_service.list_backups()

    # Prochain job planifie (les jobs desactives n'ont pas de next_run).
    jobs = scheduler.list_jobs()
    planned = [job for job in jobs if job["enabled"] and job["next_run"]]
    next_job = min(planned, key=lambda job: job["next_run"]) if planned else None

    remote_mode = remote_access.current_mode()

    available = MusicTrack.query.filter(MusicTrack.IsAvailable.is_(True))
    artist_count = available.with_entities(MusicTrack.ArtistId).distinct().count()
    song_count = available.count()

    return render_template(
        "admin/dashboard.html",
        active_users_count=Users.query.filter_by(IsActive=True).count(),
        total_users_count=Users.query.count(),
        db_size=backup_service.format_size(backup_service.db_size()),
        table_count=len(db.metadata.sorted_tables),
        backup_count=len(backups),
        last_backup_at=backups[0]["created_at"] if backups else None,
        job_count=len(jobs),
        enabled_job_count=sum(1 for job in jobs if job["enabled"]),
        next_job=next_job,
        remote_mode=remote_mode,
        remote_url=remote_access.public_base_url(),
        backup_retention_days=current_app.config["BACKUP_RETENTION_DAYS"],
        mp3_count=available.filter(MusicTrack.Source == "local").count(),
        artist_count=artist_count,
        song_count=song_count,
        podcast_count=Podcast.query.count(),
        radio_count=Radio.query.count(),
        log_count=Log.query.count(),
        login_history_count=LoginHistory.query.count(),
        smtp_host=config_settings.get_env("SMTP_HOST"),
        smtp_port=config_settings.get_env("SMTP_PORT", "587"),
        smtp_user=config_settings.get_env("SMTP_USER"),
        smtp_from=config_settings.get_env("SMTP_FROM"),
        smtp_password_set=bool(config_settings.get_env("SMTP_PASSWORD")),
        mp3_library_path=config_settings.get_env("MP3_LIBRARY_PATH", "./mp3"),
        backup_path=current_app.config["BACKUP_PATH"],
        admin_email=current_app.config["ADMIN_EMAIL"],
        app_version=APP_VERSION,
        app_release_date=APP_RELEASE_DATE,
        app_author=APP_AUTHOR,
        app_repo_url=APP_REPO_URL,
    )


_USERS_SORT_COLUMNS = {
    "FullName": Users.FullName,
    "Email": Users.Email,
    "IsActive": Users.IsActive,
    "LockedUntil": Users.LockedUntil,
    "FirstLogin": Users.FirstLogin,
    "LastLogin": Users.LastLogin,
}


@bp.route("/users")
@admin_required
def users():
    sort_field = request.args.get("sort_field", "FullName")
    sort_dir = request.args.get("sort_dir", "asc")
    column = _USERS_SORT_COLUMNS.get(sort_field, Users.FullName)
    order = column.desc() if sort_dir == "desc" else column.asc()
    users_list = Users.query.order_by(order).all()
    return render_template("admin/users.html", users=users_list, sort_field=sort_field, sort_dir=sort_dir)


@bp.route("/users/create", methods=["POST"])
@admin_required
def create_user():
    email = request.form.get("email", "").strip().lower()
    fullname = request.form.get("fullname", "").strip()

    if not email or not fullname:
        flash(translate("admin_msg_email_fullname_required"), "error")
        return redirect(url_for("admin.users"))

    if Users.query.filter_by(Email=email).first():
        flash(translate("admin_msg_account_exists"), "error")
        return redirect(url_for("admin.users"))

    user = Users(Email=email, FullName=fullname, FirstLogin=True)
    user.set_password(secrets.token_urlsafe(32))  # mot de passe temporaire inconnu de tous, remplace via le lien d'activation
    db.session.add(user)
    db.session.commit()

    token = make_activation_token(user.UsersId)
    activation_url = remote_access.external_url("auth.activate", token=token)
    send_email(
        email,
        translate("admin_email_welcome_subject"),
        translate("admin_email_welcome_body", fullname=fullname, activation_url=activation_url),
    )
    flash(translate("admin_msg_account_created", email=email), "success")
    return redirect(url_for("admin.users"))


@bp.route("/users/<int:users_id>/reset-password", methods=["POST"])
@admin_required
def trigger_reset(users_id):
    user = Users.query.get_or_404(users_id)
    token = make_reset_token(user.UsersId, user.Password)
    reset_url = remote_access.external_url("auth.reset_password", token=token)
    send_email(
        user.Email,
        translate("admin_email_reset_subject", locale=user.Langue),
        translate("admin_email_reset_body", locale=user.Langue, fullname=user.FullName, reset_url=reset_url),
    )
    flash(translate("admin_msg_reset_sent", email=user.Email), "success")
    return redirect(url_for("admin.users"))


@bp.route("/users/<int:users_id>/toggle-active", methods=["POST"])
@admin_required
def toggle_active(users_id):
    user = Users.query.get_or_404(users_id)
    user.IsActive = not user.IsActive
    db.session.commit()
    status_key = "admin_msg_status_activated" if user.IsActive else "admin_msg_status_deactivated"
    flash(translate("admin_msg_account_toggled", email=user.Email, status=translate(status_key)), "success")
    return redirect(url_for("admin.users"))


@bp.route("/users/<int:users_id>/edit", methods=["GET", "POST"])
@admin_required
def edit_user(users_id):
    user = Users.query.get_or_404(users_id)

    if request.method == "POST":
        new_email = request.form.get("email", "").strip().lower()
        new_fullname = request.form.get("fullname", "").strip()
        first_login = request.form.get("first_login") == "on"
        new_password = request.form.get("new_password", "")

        if not new_email or not new_fullname:
            flash(translate("admin_msg_email_fullname_required"), "error")
            return redirect(url_for("admin.edit_user", users_id=users_id))

        existing = Users.query.filter(Users.Email == new_email, Users.UsersId != users_id).first()
        if existing:
            flash(translate("admin_msg_email_taken"), "error")
            return redirect(url_for("admin.edit_user", users_id=users_id))

        if new_password:
            if not is_strong_password(new_password):
                flash(translate("admin_msg_password_requirement"), "error")
                return redirect(url_for("admin.edit_user", users_id=users_id))
            user.set_password(new_password)
            # Mot de passe force manuellement par l'admin : on leve aussi tout
            # blocage anti-bruteforce existant (FailedLoginAttempts/LockedUntil),
            # sinon l'utilisateur resterait bloque malgre son nouveau mot de passe.
            user.register_successful_login()

        user.Email = new_email
        user.FullName = new_fullname
        user.FirstLogin = first_login
        db.session.commit()

        flash(translate("admin_msg_user_updated"), "success")
        return redirect(url_for("admin.users"))

    return render_template("admin/edit_user.html", user=user)


@bp.route("/users/<int:users_id>/delete", methods=["POST"])
@admin_required
def delete_user(users_id):
    user = Users.query.get_or_404(users_id)
    email = user.Email

    EpisodesUsers.query.filter_by(UsersId=users_id).delete(synchronize_session=False)
    PodcastUsers.query.filter_by(UsersId=users_id).delete(synchronize_session=False)
    radio_service.delete_user_radio_data(users_id)
    RadioUsers.query.filter_by(SharedBy=users_id).update({"SharedBy": None}, synchronize_session=False)
    MusicUsers.query.filter_by(UsersId=users_id).delete(synchronize_session=False)
    MusicUsers.query.filter_by(SharedBy=users_id).update({"SharedBy": None}, synchronize_session=False)
    music_catalog.delete_user_comments(users_id)
    podcast_service.delete_user_comments(users_id)
    Play.query.filter_by(UsersId=users_id).delete(synchronize_session=False)
    Log.query.filter_by(UsersId=users_id).delete(synchronize_session=False)

    db.session.delete(user)
    db.session.commit()

    flash(translate("admin_msg_user_deleted", email=email), "success")
    return redirect(url_for("admin.users"))
