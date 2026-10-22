"""Admin : plug-ins radio "titre en cours" (cf. app/now_playing.py) - liste,
upload d'un script (nouveau ou nouvelle version), cle d'API, test, retour a
la version precedente, suppression, et test du plug-in de chaque radio."""

import os
import tempfile

from flask import render_template, request, redirect, url_for, flash

from app.admin import bp
from app.admin.decorators import admin_required
from app.extensions import db
from app.i18n import translate
from app.models import Radio, RadioPlugIn
from app import now_playing

MAX_SCRIPT_SIZE = 256 * 1024


def _masked(key: str | None) -> str:
    if not key:
        return ""
    return "••••" + key[-4:] if len(key) > 4 else "••••"


@bp.route("/radio-plugins")
@admin_required
def radio_plugins():
    now_playing.sync_plugins()
    plugins = RadioPlugIn.query.order_by(RadioPlugIn.PlugInName).all()
    counts = dict(
        db.session.query(Radio.RadioPlugInId, db.func.count())
        .filter(Radio.RadioPlugInId.isnot(None))
        .group_by(Radio.RadioPlugInId)
        .all()
    )
    return render_template(
        "admin/radio_plugins.html",
        plugins=[
            {
                "p": p,
                "masked_key": _masked(p.ApiKey),
                "radio_count": counts.get(p.RadioPlugInId, 0),
                "can_rollback": now_playing.previous_version_path(p) is not None,
                "stations": now_playing.supported_stations(p),
            }
            for p in plugins
        ],
        radios=Radio.query.order_by(Radio.RadioName).all(),
    )


@bp.route("/radio-plugins/upload", methods=["POST"])
@admin_required
def radio_plugin_upload():
    file = request.files.get("script")
    script_name = os.path.basename((file.filename if file else "") or "").lower()
    if not file or not now_playing.SCRIPT_RE.match(script_name):
        flash(translate("admin_plugin_bad_name"), "error")
        return redirect(url_for("admin.radio_plugins"))
    content = file.read(MAX_SCRIPT_SIZE + 1)
    if len(content) > MAX_SCRIPT_SIZE:
        flash(translate("admin_plugin_too_big"), "error")
        return redirect(url_for("admin.radio_plugins"))

    fd, tmp_path = tempfile.mkstemp(suffix=".py")
    try:
        with os.fdopen(fd, "wb") as tmp:
            tmp.write(content)
        plugin = now_playing.install_script(tmp_path, script_name, request.form.get("api_key", "").strip() or None)
    except now_playing.PlugInError as exc:
        flash(translate("admin_plugin_invalid", error=str(exc)), "error")
        return redirect(url_for("admin.radio_plugins"))
    finally:
        os.remove(tmp_path)

    flash(translate("admin_plugin_installed", name=plugin.PlugInName, version=plugin.Version), "success")
    return redirect(url_for("admin.radio_plugins"))


@bp.route("/radio-plugins/<int:plugin_id>/update", methods=["POST"])
@admin_required
def radio_plugin_update(plugin_id):
    plugin = db.get_or_404(RadioPlugIn, plugin_id)
    was_enabled = plugin.Enabled
    plugin.Enabled = request.form.get("enabled") == "on"
    api_key = request.form.get("api_key", "").strip()
    if request.form.get("clear_key") == "on":
        plugin.ApiKey = None
    elif api_key:  # champ vide = cle inchangee (elle n'est jamais renvoyee au navigateur)
        plugin.ApiKey = api_key
    db.session.commit()
    now_playing.clear_cache()
    if plugin.Enabled and not was_enabled:
        now_playing.redetect_unlinked()
    flash(translate("admin_plugin_saved", name=plugin.PlugInName), "success")
    return redirect(url_for("admin.radio_plugins"))


@bp.route("/radio-plugins/<int:plugin_id>/test", methods=["POST"])
@admin_required
def radio_plugin_test(plugin_id):
    plugin = db.get_or_404(RadioPlugIn, plugin_id)
    radio = Radio.query.filter_by(RadioPlugInId=plugin.RadioPlugInId).first()
    if radio is None:
        flash(translate("admin_plugin_test_no_radio", name=plugin.PlugInName), "error")
        return redirect(url_for("admin.radio_plugins"))
    now_playing.check_plugin(plugin)
    if plugin.Status == "ok":
        flash(translate("admin_plugin_test_ok", radio=radio.RadioName,
                        result=plugin.StatusDetail or translate("admin_plugin_test_empty")), "success")
    else:
        flash(translate("admin_plugin_test_error", radio=radio.RadioName, error=plugin.StatusDetail), "error")
    return redirect(url_for("admin.radio_plugins"))


@bp.route("/radio-plugins/<int:plugin_id>/rollback", methods=["POST"])
@admin_required
def radio_plugin_rollback(plugin_id):
    plugin = db.get_or_404(RadioPlugIn, plugin_id)
    if now_playing.rollback(plugin):
        flash(translate("admin_plugin_rolled_back", name=plugin.PlugInName, version=plugin.Version), "success")
    else:
        flash(translate("admin_plugin_no_previous"), "error")
    return redirect(url_for("admin.radio_plugins"))


@bp.route("/radio-plugins/<int:plugin_id>/delete", methods=["POST"])
@admin_required
def radio_plugin_delete(plugin_id):
    plugin = db.get_or_404(RadioPlugIn, plugin_id)
    name = plugin.PlugInName
    try:
        now_playing.remove(plugin)
    except OSError as exc:
        db.session.rollback()
        flash(translate("admin_plugin_delete_error", name=name, error=str(exc)), "error")
    else:
        flash(translate("admin_plugin_deleted", name=name), "success")
    return redirect(url_for("admin.radio_plugins"))


@bp.route("/radio-plugins/radio/<int:radio_id>/test", methods=["POST"])
@admin_required
def radio_plugin_test_radio(radio_id):
    """Appel du plug-in de la radio avec son Station ID. Depuis la page
    (fetch), renvoie la colonne "Dernier test" de la ligne mise a jour ;
    sinon retour a la page, sur la ligne de la radio."""
    radio = db.get_or_404(Radio, radio_id)
    if radio.plugin is not None:
        now_playing.test_radio(radio)
    if request.headers.get("X-Requested-With") == "XMLHttpRequest":
        return render_template("admin/_radio_test_cell.html", r=radio)
    return redirect(url_for("admin.radio_plugins") + f"#radio-{radio.RadioId}")
