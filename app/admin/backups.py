"""Admin : onglet Backup - liste des backups de la base (et des photos de
profil), backup immediat, telechargement et restauration globale depuis une
archive envoyee. Logique dans
app/backup_service.py."""

import os
import threading
import time

from flask import render_template, request, redirect, url_for, flash, send_file, abort, current_app, jsonify

from app.admin import bp
from app.admin.decorators import admin_required
from app import backup_service
from app.backup_service import BackupError
from app.i18n import translate

RESTART_DELAY_SECONDS = 2   # laisse partir la reponse HTTP avant de quitter
RESTART_EXIT_CODE = 3       # != 0 : relance par systemd (Restart=on-failure)


@bp.route("/backups")
@admin_required
def backups():
    return render_template(
        "admin/backups.html",
        backups=backup_service.list_backups(),
        backup_path=backup_service._resolve_backup_dir(),
        retention_days=current_app.config["BACKUP_RETENTION_DAYS"],
        format_size=backup_service.format_size,
    )


@bp.route("/backups/create", methods=["POST"])
@admin_required
def backup_create():
    try:
        path = backup_service.create_backup()
    except Exception as exc:  # noqa: BLE001 - dossier inaccessible, disque plein...
        current_app.logger.exception("Backup manuel en echec")
        flash(translate("admin_backup_create_error", error=exc), "error")
    else:
        flash(translate("admin_backup_created", name=os.path.basename(path)), "success")
    return redirect(url_for("admin.backups"))


@bp.route("/backups/download/<name>")
@admin_required
def backup_download(name):
    path = backup_service.backup_file_path(name)
    if not path:
        abort(404)
    return send_file(path, as_attachment=True, download_name=name)


@bp.route("/backups/bundle/<name>")
@admin_required
def backup_bundle(name):
    """Backup complet (base + photos de profil) en un seul zip."""
    result = backup_service.build_bundle(name)
    if not result:
        abort(404)
    bundle, download_name = result
    return send_file(bundle, as_attachment=True, download_name=download_name, mimetype="application/zip")


@bp.route("/backups/restore", methods=["POST"])
@admin_required
def backup_restore():
    """Restauration globale depuis une archive envoyee (cf. build_bundle),
    puis redemarrage du service. Reponse JSON {ok, message, restarting}."""
    upload = request.files.get("file")
    if not upload or not upload.filename:
        return jsonify(ok=False, message=translate("admin_backup_no_file"))
    try:
        result = backup_service.restore_bundle(upload.stream)
    except BackupError as exc:
        return jsonify(ok=False, message=translate(exc.key, **exc.kwargs))
    except Exception as exc:  # noqa: BLE001
        current_app.logger.exception("Restauration de %s en echec", upload.filename)
        return jsonify(ok=False, message=translate("admin_backup_restore_error", error=exc))

    current_app.logger.warning(
        "Base et photos restaurees depuis %s (etat precedent : %s)", upload.filename, result["safety"]
    )
    restarting = _schedule_restart()
    message = translate("admin_backup_restored", name=upload.filename, safety=result["safety"], photos=result["photos"])
    if not restarting:
        message += " " + translate("admin_backup_restart_manual")
    return jsonify(ok=True, message=message, restarting=restarting)


def _schedule_restart() -> bool:
    """Redemarre l'appli apres la reponse HTTP : le process quitte avec un code
    d'erreur et systemd le relance (Restart=on-failure, cf. install.sh).
    Uniquement sous systemd (INVOCATION_ID) : en dev, rien ne le relancerait."""
    if not os.environ.get("INVOCATION_ID"):
        return False

    def _exit():
        time.sleep(RESTART_DELAY_SECONDS)
        os._exit(RESTART_EXIT_CODE)

    threading.Thread(target=_exit, daemon=True).start()
    return True
