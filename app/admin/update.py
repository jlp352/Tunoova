"""Bouton Update de l'Espace Admin : verification d'une nouvelle version sur
GitHub et lancement de la mise a jour (cf. app/update_service.py)."""

from datetime import datetime, timedelta

from flask import jsonify, request

from app.admin import bp
from app.admin.decorators import admin_required
from app import update_service
from app.i18n import translate

# Une verification (git fetch) par heure au plus a l'ouverture du tableau de
# bord ; le bouton "Verifier" force une nouvelle verification.
CHECK_MAX_AGE = timedelta(hours=1)


def _check_payload(check: dict) -> dict:
    return {
        "available": check["available"],
        "local_version": check["local_version"],
        "remote_version": check["remote_version"],
        "checked_at": check["checked_at"].strftime("%d/%m/%Y %H:%M"),
        "error": check["error"],
    }


@bp.route("/update/check")
@admin_required
def update_check():
    if not update_service.is_available():
        return jsonify(supported=False)
    check = update_service.last_check()
    if request.args.get("force") == "1" or not check or datetime.now() - check["checked_at"] > CHECK_MAX_AGE:
        check = update_service.check_for_update()
        update_service.notify_update_available(check)
    return jsonify(supported=True, **_check_payload(check))


@bp.route("/update/status")
@admin_required
def update_status():
    return jsonify(update_service.get_status())


@bp.route("/update/run", methods=["POST"])
@admin_required
def update_run():
    if not update_service.request_update():
        return jsonify(ok=False, message=translate("admin_update_refused"))
    return jsonify(ok=True, message=translate("admin_update_started"))
