"""Acces a distance (onglet Admin > Reseau) : mode choisi par
l'admin, reverse proxy de confiance et URL publique de l'appli.

Modes (REMOTE_ACCESS_MODE du .env) et TRUSTED_PROXY_COUNT associe :
- off      : acces direct uniquement (LAN, redirection de port) -> 0 ;
- external : reverse proxy gere par l'utilisateur (Caddy, nginx, Cloudflare
             Tunnel...) devant le nom DNS PUBLIC_HOSTNAME -> 1 ;
- private  : tailscale serve (appareils du tailnet) -> 1 ;
- public   : tailscale funnel (tout Internet) -> 1.
Tailscale serve et funnel sont eux-memes un reverse proxy local (127.0.0.1) :
sans ProxyFix, tous les visiteurs auraient l'IP 127.0.0.1 (logs faux, et un
seul visiteur bloque par app/login_throttle.py bloquerait tout le monde).

Les en-tetes X-Forwarded-* ne sont pris en compte que si la requete vient
d'un proxy plausible : 127.0.0.1 pour Tailscale, 127.0.0.1 ou le reseau local
pour un proxy externe. L'acces direct reste ouvert (LAN, redirection de port)
et un client qui l'utilise ne peut donc pas falsifier son IP."""

import ipaddress
import os
import re

from flask import current_app, request, url_for

from app.config import BASE_DIR

MODES = ("off", "external", "private", "public")
TAILSCALE_MODES = ("private", "public")
PROXY_COUNT = {"off": 0, "external": 1, "private": 1, "public": 1}

PORT_FILE = os.path.join(BASE_DIR, ".tunoova_port")
DEFAULT_PORT = 5000

_HOSTNAME_RE = re.compile(
    r"^(?=.{1,253}(:|$))(?!-)[a-z0-9-]{1,63}(?<!-)(\.(?!-)[a-z0-9-]{1,63}(?<!-))+(:\d{1,5})?$"
)


def current_mode(config=None) -> str:
    """Mode enregistre ; a defaut (installation anterieure a l'onglet), deduit
    de TRUSTED_PROXY_COUNT regle a la main."""
    config = config if config is not None else current_app.config
    mode = config.get("REMOTE_ACCESS_MODE") or ""
    if mode in MODES:
        return mode
    return "external" if config.get("TRUSTED_PROXY_COUNT", 0) > 0 else "off"


def normalize_hostname(value: str) -> str | None:
    """Nom DNS saisi par l'admin (schema et / final toleres), ou None si
    invalide. Port facultatif (proxy sur un port autre que 443)."""
    value = (value or "").strip().lower()
    value = re.sub(r"^https?://", "", value).rstrip("/")
    return value if _HOSTNAME_RE.match(value) else None


def save_mode(mode: str, hostname: str | None = None):
    """Enregistre le mode et le TRUSTED_PROXY_COUNT associe dans .env, avec
    effet immediat (TrustedProxyMiddleware relit la config a chaque requete)."""
    from app.admin.config_settings import set_env

    if mode not in MODES:
        raise ValueError(mode)
    config = current_app.config
    if hostname is not None:
        set_env("PUBLIC_HOSTNAME", hostname)
        config["PUBLIC_HOSTNAME"] = hostname
    set_env("REMOTE_ACCESS_MODE", mode)
    config["REMOTE_ACCESS_MODE"] = mode
    set_env("TRUSTED_PROXY_COUNT", str(PROXY_COUNT[mode]))
    config["TRUSTED_PROXY_COUNT"] = PROXY_COUNT[mode]


def app_port() -> int:
    """Port d'ecoute de l'appli, cible de tailscale serve/funnel :
    .tunoova_port (install.sh), sinon celui de la requete en cours."""
    try:
        with open(PORT_FILE, encoding="utf-8") as f:
            value = f.readline().strip()
        if value.isdigit():
            return int(value)
    except OSError:
        pass
    orig = request.environ.get("werkzeug.proxy_fix.orig") or request.environ
    port = str(orig.get("SERVER_PORT") or "")
    return int(port) if port.isdigit() else DEFAULT_PORT


def public_base_url(tailscale_status: dict | None = None) -> str | None:
    """URL publique de l'appli (sans / final) selon le mode, ou None si elle
    n'est joignable que sur le reseau local."""
    mode = current_mode()
    if mode == "external":
        hostname = current_app.config.get("PUBLIC_HOSTNAME") or ""
        return f"https://{hostname}" if hostname else None
    if mode in TAILSCALE_MODES:
        from app import tailscale_service

        st = tailscale_status if tailscale_status is not None else tailscale_service.status()
        return st["url"] if st["exposure"] == mode else None
    return None


def external_url(endpoint: str, **values) -> str:
    """url_for(_external=True) base sur l'URL publique quand elle est connue :
    un lien envoye par email depuis l'admin consulte en LAN (http://IP:port)
    ne serait pas utilisable depuis l'exterieur. En mode prive, le lien n'est
    ouvrable que depuis un appareil du tailnet."""
    base = public_base_url()
    if base:
        return base + url_for(endpoint, **values)
    return url_for(endpoint, _external=True, **values)


def is_trusted_proxy(addr: str | None, mode: str) -> bool:
    try:
        ip = ipaddress.ip_address(addr or "")
    except ValueError:
        return False
    if ip.version == 6 and ip.ipv4_mapped:
        ip = ip.ipv4_mapped
    if ip.is_loopback:
        return mode != "off"
    return mode == "external" and ip.is_private


class TrustedProxyMiddleware:
    """ProxyFix applique requete par requete selon la config courante (le
    changement de mode dans l'admin s'applique sans redemarrage)."""

    def __init__(self, wsgi_app, config):
        self.wsgi_app = wsgi_app
        self.config = config
        self._fixers = {}

    def __call__(self, environ, start_response):
        count = self.config.get("TRUSTED_PROXY_COUNT", 0)
        if count > 0 and is_trusted_proxy(environ.get("REMOTE_ADDR"), current_mode(self.config)):
            fixer = self._fixers.get(count)
            if fixer is None:
                from werkzeug.middleware.proxy_fix import ProxyFix

                fixer = self._fixers[count] = ProxyFix(
                    self.wsgi_app, x_for=count, x_proto=count, x_host=count
                )
            return fixer(environ, start_response)
        return self.wsgi_app(environ, start_response)


class SecureCookieMiddleware:
    """Ajoute le flag Secure a tous les cookies poses (session, remember me,
    admin, ...) quand la requete est arrivee en HTTPS ou via un proxy (acces
    distant : Tailscale Funnel, reverse proxy - TLS termine en amont). Seul
    l'acces HTTP direct depuis le reseau local garde des cookies sans Secure.
    Place sous TrustedProxyMiddleware : wsgi.url_scheme y est deja corrige."""

    def __init__(self, wsgi_app):
        self.wsgi_app = wsgi_app

    def __call__(self, environ, start_response):
        secure = (
            environ.get("wsgi.url_scheme") == "https"
            or "HTTP_X_FORWARDED_FOR" in environ
            or "HTTP_X_FORWARDED_PROTO" in environ
        )
        if not secure:
            return self.wsgi_app(environ, start_response)

        def _start_response(status, headers, exc_info=None):
            headers = [
                (name, value + "; Secure")
                if name.lower() == "set-cookie" and "; secure" not in value.lower()
                else (name, value)
                for name, value in headers
            ]
            return start_response(status, headers, exc_info)

        return self.wsgi_app(environ, _start_response)
