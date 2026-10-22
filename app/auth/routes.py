from datetime import datetime

from flask import render_template, request, redirect, url_for, flash, session
from flask_login import login_user, logout_user, login_required, current_user

from app.auth import bp
from app.extensions import db
from app.models import Users
from app.logging_util import log_screen_view, log_login_event
from app.email_utils import notify_admin
from app.tokens import read_activation_token, read_reset_token
from app import profile_service, login_throttle, starter_content
from app.admin.password_policy import is_strong_password
from app.admin.decorators import end_admin_session


def _reset_session():
    """Changement de personne sur le navigateur (connexion/deconnexion) : repart
    d'une session Flask vierge, sans reste de la precedente. Seule la langue
    choisie dans l'Espace Admin (simple preference d'affichage) est gardee.
    Le cookie admin, distinct, est supprime par l'appelant (end_admin_session)."""
    admin_locale = session.get("admin_locale")
    session.clear()
    if admin_locale:
        session["admin_locale"] = admin_locale


@bp.route("/signin", methods=["GET", "POST"])
def signin():
    if current_user.is_authenticated:
        return redirect(url_for("main.home"))

    error = None
    email = ""
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        user = Users.query.filter_by(Email=email).first()
        ip_locked_until = login_throttle.ip_locked_until(login_throttle.APP_SCOPE, request.remote_addr)

        if ip_locked_until:
            error = ("locked", login_throttle.remaining_minutes(ip_locked_until))
            log_login_event("ip_locked", email, user)
        elif user and user.is_locked():
            error = ("locked", login_throttle.remaining_minutes(user.LockedUntil))
            log_login_event("user_locked", email, user)
        elif user and user.check_password(password):
            user.register_successful_login()
            user.LastLogin = datetime.utcnow()
            db.session.commit()
            log_login_event("success", email, user)
            _reset_session()
            login_user(user, remember=True)
            target = "auth.firstlogin" if user.FirstLogin else "main.home"
            return end_admin_session(redirect(url_for(target)))
        else:
            if user:
                user.register_failed_login()
                db.session.commit()
            error = ("invalid", None)
            log_login_event("invalid_password" if user else "invalid_user", email, user)

    password_saved = request.args.get("password_saved") == "1"

    return render_template("auth/signin.html", error=error, email_value=email, password_saved=password_saved)


def _admin_request_allowed(event_type: str, email: str, user=None) -> bool:
    """Filtre les demandes qui envoient un email a l'admin : champ piege
    "website" (invisible, rempli seulement par les bots) et limites de
    login_throttle. Une demande refusee recoit la meme reponse qu'une demande
    transmise, pour ne pas renseigner le bot."""
    if request.form.get("website"):
        log_login_event("request_blocked", email, user)
        return False
    if not login_throttle.admin_request_allowed(event_type, request.remote_addr, email):
        log_login_event("request_blocked", email, user)
        return False
    log_login_event(event_type, email, user)
    return True


@bp.route("/signin/request-account", methods=["POST"])
def request_account():
    fullname = request.form.get("fullname", "").strip()[:100]
    email = request.form.get("email", "").strip().lower()[:320]
    if email and _admin_request_allowed("account_request", email):
        notify_admin(
            "Tunoova - Demande de creation de compte",
            f"{fullname or 'Un visiteur'} ({email}) demande la creation d'un compte Tunoova.",
        )
    if request.headers.get("HX-Request") == "true":
        return render_template("auth/_request_sent.html", message_key="signin_request_account_sent")
    flash("signin_request_account_sent", "info")
    return redirect(url_for("auth.signin"))


@bp.route("/signin/request-reset", methods=["POST"])
def request_reset():
    email = request.form.get("email", "").strip().lower()
    user = Users.query.filter_by(Email=email).first()
    if user and _admin_request_allowed("reset_request", email, user):
        notify_admin(
            "Tunoova - Demande de reinitialisation de mot de passe",
            f"L'utilisateur {user.FullName} ({user.Email}) demande une reinitialisation de mot de passe.",
        )
    # Message volontairement identique que le compte existe ou non (pas d'enumeration d'emails).
    if request.headers.get("HX-Request") == "true":
        return render_template("auth/_request_sent.html", message_key="signin_request_reset_sent")
    flash("signin_request_reset_sent", "info")
    return redirect(url_for("auth.signin"))


@bp.route("/activate/<token>", methods=["GET", "POST"])
def activate(token):
    users_id = read_activation_token(token)
    if users_id is None:
        return render_template("auth/token_invalid.html"), 400

    user = Users.query.get_or_404(users_id)
    error = None
    if request.method == "POST":
        password = request.form.get("password", "")
        confirm = request.form.get("confirm", "")
        if password != confirm:
            error = "mismatch"
        elif not is_strong_password(password):
            error = "weak"
        else:
            user.set_password(password)
            db.session.commit()
            return redirect(url_for("auth.signin", password_saved="1"))

    return render_template("auth/set_password.html", mode="activate", error=error)


@bp.route("/reset-password/<token>", methods=["GET", "POST"])
def reset_password(token):
    # Le token porte l'uid mais sa validite depend du hash de mot de passe courant
    # (cf. app/tokens.py) : on doit donc d'abord charger un candidat sans le valider
    # completement, ce qui necessite de decoder l'uid en confiance limitee.
    from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired
    from flask import current_app

    try:
        raw = URLSafeTimedSerializer(current_app.config["SECRET_KEY"], salt="password-reset").loads(
            token, max_age=3600
        )
    except (BadSignature, SignatureExpired):
        return render_template("auth/token_invalid.html"), 400

    user = Users.query.get_or_404(raw.get("uid"))
    if read_reset_token(token, user.Password) is None:
        return render_template("auth/token_invalid.html"), 400

    error = None
    if request.method == "POST":
        password = request.form.get("password", "")
        confirm = request.form.get("confirm", "")
        if password != confirm:
            error = "mismatch"
        elif not is_strong_password(password):
            error = "weak"
        else:
            user.set_password(password)
            db.session.commit()
            return redirect(url_for("auth.signin", password_saved="1"))

    return render_template("auth/set_password.html", mode="reset", error=error)


FIRSTLOGIN_VALID_LANGUAGES = ("fr", "en")


@bp.route("/firstlogin", methods=["GET", "POST"])
@login_required
def firstlogin():
    log_screen_view("FirstLogin")

    if request.method == "POST" and request.form.get("language_only"):
        # Changement de langue dans la liste : applique la langue tout de suite
        # (sans valider ni terminer le First Login) et reaffiche l'ecran avec
        # les valeurs deja saisies.
        langue = request.form.get("langue")
        if langue in FIRSTLOGIN_VALID_LANGUAGES:
            current_user.Langue = langue
        photo_path = profile_service.save_profile_photo(request.files.get("photo"))
        if photo_path:
            current_user.Photo = photo_path
        db.session.commit()
        age_ranges, hobbies, music_genres, countries = profile_service.get_reference_lists()
        return render_template(
            "auth/firstlogin.html",
            age_ranges=age_ranges,
            hobbies=hobbies,
            music_genres=music_genres,
            countries=countries,
            error=None,
            form_values=request.form,
        )

    if request.method == "POST":
        fullname = request.form.get("fullname", "").strip()
        age_range = request.form.get("age_range") or ""
        country = request.form.get("country") or ""

        error = None
        if not fullname:
            error = "userprofile_error_fullname_required"
        elif not age_range:
            error = "userprofile_error_age_range_required"
        elif not country:
            error = "userprofile_error_country_required"

        if error:
            age_ranges, hobbies, music_genres, countries = profile_service.get_reference_lists()
            return render_template(
                "auth/firstlogin.html",
                age_ranges=age_ranges,
                hobbies=hobbies,
                music_genres=music_genres,
                countries=countries,
                error=error,
                form_values=request.form,
            )

        current_user.FullName = fullname
        current_user.AgeRange = age_range
        current_user.Hobby = ", ".join(request.form.getlist("hobby"))
        current_user.MusicGenre = ", ".join(request.form.getlist("music_genre"))
        current_user.Country = country

        langue = request.form.get("langue")
        if langue in FIRSTLOGIN_VALID_LANGUAGES:
            current_user.Langue = langue

        photo_path = profile_service.save_profile_photo(request.files.get("photo"))
        if photo_path:
            current_user.Photo = photo_path

        current_user.FirstLogin = False
        db.session.commit()
        starter_content.seed_starter_content(current_user)
        return redirect(url_for("main.home"))

    age_ranges, hobbies, music_genres, countries = profile_service.get_reference_lists()

    return render_template(
        "auth/firstlogin.html",
        age_ranges=age_ranges,
        hobbies=hobbies,
        music_genres=music_genres,
        countries=countries,
        error=None,
        form_values=None,
    )


@bp.route("/logout")
@login_required
def logout():
    log_login_event("logout", current_user.Email, current_user)
    # Session videe AVANT logout_user : celui-ci y depose la consigne
    # d'effacement du cookie "remember me", qui doit survivre.
    _reset_session()
    logout_user()
    return end_admin_session(redirect(url_for("auth.signin")))


@bp.route("/legal/terms")
def terms():
    return render_template("auth/terms.html")


@bp.route("/legal/privacy")
def privacy():
    return render_template("auth/privacy.html")
