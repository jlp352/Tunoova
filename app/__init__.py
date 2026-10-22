from flask import Flask, request, session
from flask_login import user_loaded_from_cookie

from app.config import Config
from app.extensions import db, login_manager, csrf
from app import i18n


def create_app(config_class=Config):
    app = Flask(__name__)
    app.config.from_object(config_class)

    db.init_app(app)
    login_manager.init_app(app)
    csrf.init_app(app)
    i18n.init_app(app)

    from flask import flash, redirect
    from flask_wtf.csrf import CSRFError
    from app.url_util import safe_local_url

    @app.errorhandler(CSRFError)
    def csrf_error(error):
        """Formulaire envoye depuis une page devenue perimee (deconnexion ou
        reconnexion dans un autre onglet : la session, et donc le jeton CSRF,
        ont change). Plutot qu'une page brute "Bad Request", on recharge la
        page d'origine avec un message ; elle repasse par le controle de
        connexion (admin ou appli). Les POST htmx/fetch gardent le 400."""
        if request.headers.get("Sec-Fetch-Mode") != "navigate" or request.headers.get("HX-Request"):
            return error.description, 400
        flash(i18n.translate("msg_csrf_expired"), "error")
        return redirect(safe_local_url(request.referrer, "/"))

    from app.cover_service import cover_for
    from app.icons import icon
    from app.time_util import short_time_ago
    from app.play_service import short_count

    app.jinja_env.globals["cover_for"] = cover_for
    app.jinja_env.globals["icon"] = icon
    app.jinja_env.globals["time_ago"] = short_time_ago
    app.jinja_env.globals["short_count"] = short_count

    from app.models import Users
    from flask_login import current_user

    @login_manager.user_loader
    def load_user(user_id):
        return db.session.get(Users, int(user_id))

    @user_loaded_from_cookie.connect_via(app)
    def _on_user_loaded_from_cookie(sender, user):
        """Reconnexion silencieuse via le cookie "remember me" (pas de
        passage par /signin) - trace dans LoginHistory pour la detection
        d'activite suspecte. Une seule entree par session Flask (pas a
        chaque requete) via ce flag en session."""
        if session.get("_login_history_cookie_logged"):
            return
        session["_login_history_cookie_logged"] = True

        from app.logging_util import log_login_event

        log_login_event("cookie_resume", user.Email, user)

    @app.context_processor
    def inject_notifications_count():
        if not current_user.is_authenticated or not current_user.Notifications:
            return {"notifications_count": 0}
        from app.notifications.routes import count_new

        return {"notifications_count": count_new(current_user.UsersId)}

    @app.context_processor
    def inject_current_play():
        """Rend le mini-lecteur a partir de l'etat reellement enregistre (table
        Play, CDC §2) sur CHAQUE affichage de page - independant de hx-preserve
        (CDC §7), qui ne fonctionne que pour une navigation normale en avant.
        htmx ne respecte pas hx-preserve lors d'une restauration d'historique
        (bouton Retour, verifie empiriquement) : sans ce rendu serveur, revenir
        en arriere pendant une lecture affichait le mini-lecteur reinitialise
        ('Cet ecran arrive bientot') au lieu de ce qui joue vraiment."""
        if not current_user.is_authenticated:
            return {"current_play": None}

        from app import play_service

        return {"current_play": play_service.current_play_summary(current_user.UsersId)}

    @app.context_processor
    def inject_pwa_installable():
        """Manifeste d'application (icone "Installer" de Chrome) seulement en
        acces distant (Tailscale ou reverse proxy) : Chrome n'installe qu'en
        HTTPS, que l'acces direct LAN (http://IP:port) ne fournit pas."""
        from app import remote_access

        return {"pwa_installable": remote_access.current_mode() != "off"}

    @app.context_processor
    def inject_other_users_for_share():
        """Liste pour le formulaire de partage du panneau #play-overlay
        (app/templates/base.html) - toujours rendue (independante de l'item
        affiche) puisque ce panneau n'est jamais re-rendu par le serveur
        apres le premier chargement de page (cf. commentaire dans
        base.html)."""
        if not current_user.is_authenticated:
            return {"other_users": []}
        return {
            "other_users": Users.query.filter(
                Users.UsersId != current_user.UsersId, Users.IsActive.is_(True)
            ).order_by(Users.FullName).all()
        }

    @app.after_request
    def _private_cache_headers(response):
        """Interdit la mise en cache partagee (proxy, CDN) des reponses
        dynamiques, qui portent des donnees propres a l'utilisateur (cf.
        Doc/Audit Tunoova.md, PYSEC-2026-2151). Les fichiers statiques et
        les reponses ayant deja choisi leur cache (pochettes en max_age) sont
        laisses tels quels. HTML/JSON et telechargements (backups) : no-store
        en plus, rien a conserver sur disque ; l'audio reste simplement
        private pour ne pas gener la lecture et le prechargement."""
        if request.endpoint == "static" or response.cache_control.public or response.cache_control.max_age:
            return response
        response.cache_control.private = True
        if (
            response.mimetype in ("text/html", "application/json")
            or response.headers.get("Content-Disposition", "").startswith("attachment")
        ):
            response.cache_control.no_store = True
        return response

    @app.after_request
    def _security_headers(response):
        """En-tetes de durcissement (scan OWASP ZAP, cf. Doc/Audit Tunoova.md) :
        pas d'affichage dans un cadre d'un autre site (clickjacking), pas de
        devinette du type MIME. CSP volontairement minimale : les templates
        ont des <script> inline et des onclick, une CSP script-src stricte
        demanderait de les sortir tous dans app.js."""
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        response.headers.setdefault(
            "Content-Security-Policy",
            "frame-ancestors 'none'; object-src 'none'; base-uri 'self'; form-action 'self'",
        )
        return response

    from app.auth import bp as auth_bp
    from app.main import bp as main_bp
    from app.admin import bp as admin_bp
    from app.podcast import bp as podcast_bp
    from app.radio import bp as radio_bp
    from app.library import bp as library_bp
    from app.media import bp as media_bp
    from app.play import bp as play_bp
    from app.settings import bp as settings_bp
    from app.userprofile import bp as userprofile_bp
    from app.notifications import bp as notifications_bp
    from app.cast import bp as cast_bp

    app.register_blueprint(auth_bp)
    app.register_blueprint(main_bp)
    app.register_blueprint(admin_bp)
    app.register_blueprint(podcast_bp)
    app.register_blueprint(radio_bp)
    app.register_blueprint(library_bp)
    app.register_blueprint(media_bp)
    app.register_blueprint(play_bp)
    app.register_blueprint(settings_bp)
    app.register_blueprint(userprofile_bp)
    app.register_blueprint(notifications_bp)
    app.register_blueprint(cast_bp)

    from app import scheduler as job_scheduler
    from app import cast_service

    job_scheduler.init_app(app)
    cast_service.init_app(app)

    # Derriere un reverse proxy, request.remote_addr est l'IP du proxy : on
    # reprend la vraie IP du client (et le schema/hote d'origine) dans les
    # en-tetes X-Forwarded-* ajoutes par les proxies de confiance
    # (TRUSTED_PROXY_COUNT, regle par Admin > Reseau).
    from app.remote_access import SecureCookieMiddleware, TrustedProxyMiddleware

    app.wsgi_app = TrustedProxyMiddleware(SecureCookieMiddleware(app.wsgi_app), app.config)

    return app
