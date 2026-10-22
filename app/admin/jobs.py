from flask import render_template, request, redirect, url_for, flash, jsonify

from app.admin import bp
from app.admin.decorators import admin_required
from app import scheduler
from app.i18n import translate


@bp.route("/jobs")
@admin_required
def jobs():
    return render_template("admin/jobs.html", jobs=scheduler.list_jobs())


def _fmt_dt(value):
    return value.strftime("%d/%m/%Y %H:%M:%S") if value else None


@bp.route("/jobs/status")
@admin_required
def jobs_status():
    """Statut courant des jobs, interroge par la page admin tant qu'un job
    est en cours, pour mettre a jour le tableau sans recharger la page."""
    return jsonify(
        jobs=[
            {
                "id": job["id"],
                "running": job["running"],
                "last_run": _fmt_dt(job["last_run"]),
                "next_run": _fmt_dt(job["next_run"]),
                "last_detail": job["last_detail"],
                "last_error": job["last_error"],
            }
            for job in scheduler.list_jobs()
        ]
    )


@bp.route("/jobs/<job_id>/update", methods=["POST"])
@admin_required
def update_job(job_id):
    if job_id not in scheduler.JOBS:
        flash(translate("admin_msg_job_unknown"), "error")
        return redirect(url_for("admin.jobs"))

    cron_expr = request.form.get("cron", "").strip()
    enabled = request.form.get("enabled") == "on"

    try:
        scheduler.set_cron(job_id, cron_expr)
    except ValueError:
        flash(translate("admin_msg_cron_invalid", cron=cron_expr), "error")
        return redirect(url_for("admin.jobs"))

    scheduler.set_enabled(job_id, enabled)
    label = translate(scheduler.JOBS[job_id]["label_key"])
    flash(translate("admin_msg_job_updated", label=label), "success")
    return redirect(url_for("admin.jobs"))


@bp.route("/jobs/<job_id>/run", methods=["POST"])
@admin_required
def run_job(job_id):
    if job_id not in scheduler.JOBS:
        flash(translate("admin_msg_job_unknown"), "error")
        return redirect(url_for("admin.jobs"))

    try:
        scheduler.run_now(job_id)
    except RuntimeError as exc:
        flash(str(exc), "error")
        return redirect(url_for("admin.jobs"))

    label = translate(scheduler.JOBS[job_id]["label_key"])
    flash(translate("admin_msg_job_triggered", label=label), "success")
    return redirect(url_for("admin.jobs"))
