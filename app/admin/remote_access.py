"""Admin : onglet Reseau - choix du mode (local, VPN prive Tailscale, reverse
proxy externe ou Tailscale Funnel) et connexion de Tailscale. Logique dans
app/remote_access.py et app/tailscale_service.py."""

import getpass
import os
import socket

import segno
from flask import render_template, request, redirect, url_for, flash, current_app, jsonify
from markupsafe import Markup

from app.admin import bp
from app.admin.decorators import admin_required
from app import remote_access, tailscale_service
from app.config import BASE_DIR
from app.i18n import translate
from app.tailscale_service import TailscaleError

# Etats Tailscale pendant lesquels la page se rafraichit d'elle-meme
# (connexion ou autorisation en cours sur le site de Tailscale).
_WAITING_STATES = ("NeedsLogin", "NeedsMachineAuth", "Starting", "Connecting")

# Mode Tailscale choisi alors que le serveur n'etait pas encore connecte a
# Tailscale : applique des que la connexion aboutit (cf. _apply_pending_mode).
_pending_mode: str | None = None


def _qr_svg(url: str | None) -> Markup | None:
    return Markup(segno.make(url, error="m").svg_inline(scale=4, border=2)) if url else None


def _hostname_suggestions(current: str) -> list[str]:
    """Propositions de la liste du nom DNS : valeur actuelle, nom par lequel
    la page est consultee et nom complet de la machine (s'ils sont valides)."""
    candidates = [current, request.host, socket.getfqdn()]
    suggestions = []
    for value in candidates:
        name = remote_access.normalize_hostname(value or "")
        if name and name not in suggestions and not name.split(":")[0].replace(".", "").isdigit():
            suggestions.append(name)
    return suggestions


def _wait_key(ts: dict) -> str:
    """Resume de l'etat Tailscale : la page se recharge quand il change."""
    task = ts["task"] or {}
    return "|".join(str(v) for v in (ts["state"], ts["exposure"], ts["auth_url"], task.get("kind"),
                                     task.get("running"), task.get("url")))


def _is_waiting(ts: dict) -> bool:
    return ts["state"] in _WAITING_STATES or bool(ts["task"] and ts["task"]["running"])


def _apply_tailscale_mode(mode: str):
    """Active le mode Tailscale, l'enregistre et le signale par un flash.
    Leve TailscaleError."""
    result = tailscale_service.enable(mode, remote_access.app_port())
    remote_access.save_mode(mode)
    label = translate(f"admin_remote_mode_{mode}")
    key = "admin_remote_msg_pending" if result == "pending" else "admin_remote_msg_saved"
    flash(translate(key, label=label), "success")


def _apply_pending_mode(ts: dict) -> dict:
    """Applique le mode en attente une fois Tailscale connecte, et retourne
    l'etat a jour."""
    global _pending_mode
    if not _pending_mode or ts["state"] != "Running":
        return ts
    mode, _pending_mode = _pending_mode, None
    try:
        _apply_tailscale_mode(mode)
    except TailscaleError as exc:
        flash(translate("admin_remote_msg_error", error=str(exc)), "error")
    return tailscale_service.status()


@bp.route("/network")
@admin_required
def remote_access_page():
    ts = _apply_pending_mode(tailscale_service.status())
    mode = remote_access.current_mode()
    hostname = current_app.config.get("PUBLIC_HOSTNAME") or ""
    public_url = remote_access.public_base_url(ts)
    task = ts["task"]
    return render_template(
        "admin/remote_access.html",
        mode=mode,
        hostname=hostname,
        hostname_suggestions=_hostname_suggestions(hostname),
        ts=ts,
        ts_ready=ts["state"] == "Running",
        ts_denied=tailscale_service.is_access_denied(ts["error"] or (task or {}).get("error")),
        app_user=getpass.getuser(),
        install_script=os.path.join(BASE_DIR, "install.sh"),
        console_url=tailscale_service.CONSOLE_URL,
        public_url=public_url,
        public_qr=_qr_svg(public_url),
        auth_qr=_qr_svg(ts["auth_url"]),
        task_qr=_qr_svg(task["url"] if task else None),
        pending_mode=_pending_mode,
        waiting=_is_waiting(ts),
        wait_key=_wait_key(ts),
    )


@bp.route("/network/status")
@admin_required
def remote_access_status():
    """Interroge par la page toutes les 3 s pendant une connexion ou une
    autorisation en attente : elle se recharge quand `key` change."""
    ts = _apply_pending_mode(tailscale_service.status())
    return jsonify(key=_wait_key(ts), waiting=_is_waiting(ts))


@bp.route("/network/mode", methods=["POST"])
@admin_required
def remote_access_mode():
    global _pending_mode
    mode = request.form.get("mode", "")
    if mode not in remote_access.MODES:
        flash(translate("admin_remote_msg_mode_invalid"), "error")
        return redirect(url_for("admin.remote_access_page"))

    hostname = None
    if mode == "external":
        hostname = remote_access.normalize_hostname(request.form.get("hostname", ""))
        if not hostname:
            flash(translate("admin_remote_msg_hostname_invalid"), "error")
            return redirect(url_for("admin.remote_access_page"))

    if mode in remote_access.TAILSCALE_MODES:
        ts = tailscale_service.status()
        try:
            if ts["state"] == "NotInstalled":
                flash(translate("admin_remote_msg_not_installed"), "error")
            elif ts["state"] == "Running":
                _pending_mode = None
                _apply_tailscale_mode(mode)
            else:
                # Pas encore connecte : lance la connexion, le mode sera
                # applique quand elle aboutit (la page interroge l'etat).
                _pending_mode = mode
                ts = tailscale_service.connect()
                # Enregistre tout de suite : le choix s'affiche pendant la
                # connexion et survit a un redemarrage de l'appli (le mode
                # n'est actif dans Tailscale qu'une fois applique).
                remote_access.save_mode(mode)
                if ts["state"] == "Running":
                    # Simple reconnexion (deconnecte par le mode Local) : le
                    # compte etait memorise, pas de connexion sur le site.
                    _pending_mode = None
                    _apply_tailscale_mode(mode)
                elif ts["task"] and ts["task"]["error"]:
                    flash(translate("admin_remote_msg_error", error=ts["task"]["error"]), "error")
                else:
                    flash(translate("admin_remote_msg_login_first", label=translate(f"admin_remote_mode_{mode}")), "success")
        except TailscaleError as exc:
            flash(translate("admin_remote_msg_error", error=str(exc)), "error")
        return redirect(url_for("admin.remote_access_page"))

    _pending_mode = None
    try:
        ts = tailscale_service.status()
        if ts["state"] == "Running" or (ts["task"] and ts["task"]["running"]):
            tailscale_service.disable(connected=ts["state"] == "Running")
    except TailscaleError as exc:
        flash(translate("admin_remote_msg_error", error=str(exc)), "error")
        # Exposition Tailscale non retiree : on garde le mode actuel plutot
        # que d'afficher un mode qui ne correspond pas a la realite.
        if remote_access.current_mode() in remote_access.TAILSCALE_MODES:
            return redirect(url_for("admin.remote_access_page"))

    remote_access.save_mode(mode, hostname)
    flash(translate("admin_remote_msg_saved", label=translate(f"admin_remote_mode_{mode}")), "success")
    return redirect(url_for("admin.remote_access_page"))
