"""Scheduler applicatif (APScheduler) pilotant les jobs de fond (CDC §2/§9) :
scan MP3, purge mensuelle des MP3 disparus, poll RSS podcasts, backup DB, scan des enceintes Cast,
purge des logs de plus d'un mois.
Statut (derniere execution, resultat, prochaine execution) garde en memoire,
pas de table dediee en base - conforme au CDC."""

import logging
import os
from datetime import datetime

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from dotenv import set_key

from app.config import BASE_DIR

logger = logging.getLogger(__name__)

_ENV_PATH = os.path.join(BASE_DIR, ".env")
_scheduler = BackgroundScheduler()
_status: dict[str, dict] = {}
# Jobs en cours d'execution (job_id -> heure de debut), pour que la page admin
# puisse afficher "en cours" et rafraichir le statut sans recharger.
_running: dict[str, datetime] = {}
_app = None


def _run_mp3_scan():
    from app import mp3_library_service

    stats = mp3_library_service.scan_library(force=True, resolve_images=True)
    return "admin_job_mp3_result", stats


def _run_music_purge():
    from app import music_catalog

    count = music_catalog.purge_missing_tracks()
    return "admin_job_music_purge_result", {"count": count}


def _run_rss_poll():
    from app import podcast_service
    from app.models import PodcastUsers

    total = 0
    for subscription in PodcastUsers.query.all():
        total += podcast_service.sync_episodes(subscription, mark_new=True)
    return "admin_job_rss_result", {"count": total}


def _run_db_backup():
    from app import backup_service

    path = backup_service.create_backup()
    return "admin_job_backup_result", {"filename": os.path.basename(path)}


def _run_cast_scan():
    from app import cast_service

    count = cast_service.scan_devices()
    return "admin_job_cast_scan_result", {"count": count}


def _run_log_purge():
    from app import logging_util

    deleted = logging_util.purge_logs()
    return "admin_job_log_purge_result", {"log": deleted["Log"], "login": deleted["LoginHistory"]}


JOBS = {
    "mp3_scan": {
        "label_key": "admin_job_mp3_scan",
        "cron_env": "JOB_MP3_SCAN_CRON",
        "enabled_env": "JOB_MP3_SCAN_ENABLED",
        "default_cron": "*/15 * * * *",
        "func": _run_mp3_scan,
    },
    "music_purge": {
        "label_key": "admin_job_music_purge",
        "cron_env": "JOB_MUSIC_PURGE_CRON",
        "enabled_env": "JOB_MUSIC_PURGE_ENABLED",
        "default_cron": "0 4 1 * *",  # le 1er du mois a 4h
        "func": _run_music_purge,
    },
    "rss_poll": {
        "label_key": "admin_job_rss_poll",
        "cron_env": "JOB_RSS_POLL_CRON",
        "enabled_env": "JOB_RSS_POLL_ENABLED",
        "default_cron": "*/30 * * * *",
        "func": _run_rss_poll,
    },
    "db_backup": {
        "label_key": "admin_job_db_backup",
        "cron_env": "JOB_DB_BACKUP_CRON",
        "enabled_env": "JOB_DB_BACKUP_ENABLED",
        "default_cron": "0 3 * * *",
        "func": _run_db_backup,
    },
    "cast_scan": {
        "label_key": "admin_job_cast_scan",
        "cron_env": "JOB_CAST_SCAN_CRON",
        "enabled_env": "JOB_CAST_SCAN_ENABLED",
        "default_cron": "*/5 * * * *",
        "func": _run_cast_scan,
    },
    "log_purge": {
        "label_key": "admin_job_log_purge",
        "cron_env": "JOB_LOG_PURGE_CRON",
        "enabled_env": "JOB_LOG_PURGE_ENABLED",
        "default_cron": "30 4 * * *",  # tous les jours a 4h30
        "func": _run_log_purge,
    },
}


def _wrap(job_id: str):
    func = JOBS[job_id]["func"]

    def wrapper():
        with _app.app_context():
            started_at = datetime.now()
            _running[job_id] = started_at
            try:
                detail_key, detail_kwargs = func()
                # Le job tourne dans le thread du scheduler, sans contexte de
                # requete (session/langue courante indisponibles) : on garde
                # la cle + les parametres bruts, traduits plus tard a
                # l'affichage dans list_jobs() (appele depuis une requete
                # admin, qui a bien un contexte de requete).
                _status[job_id] = {
                    "last_run": started_at,
                    "ok": True,
                    "detail_key": detail_key,
                    "detail_kwargs": detail_kwargs,
                    "error": None,
                }
            except Exception as exc:  # best-effort : un job en echec ne doit pas planter le scheduler
                logger.exception("Job planifie '%s' en echec", job_id)
                _status[job_id] = {
                    "last_run": started_at,
                    "ok": False,
                    "detail_key": None,
                    "detail_kwargs": None,
                    "error": str(exc),
                }
            finally:
                _running.pop(job_id, None)

    return wrapper


def init_app(app):
    global _app
    _app = app

    if app.debug and os.environ.get("WERKZEUG_RUN_MAIN") != "true":
        # Sous le reloader Flask, le process parent ne doit pas demarrer le scheduler
        # (double execution des jobs sinon) : seul le process enfant (WERKZEUG_RUN_MAIN) le fait.
        return

    for job_id, meta in JOBS.items():
        cron = os.environ.get(meta["cron_env"], meta["default_cron"])
        enabled = os.environ.get(meta["enabled_env"], "1") == "1"
        _scheduler.add_job(
            _wrap(job_id),
            trigger=CronTrigger.from_crontab(cron),
            id=job_id,
            replace_existing=True,
            paused=not enabled,
        )

    _scheduler.start()


def list_jobs() -> list[dict]:
    from app.i18n import translate

    jobs = []
    for job_id, meta in JOBS.items():
        aps_job = _scheduler.get_job(job_id)
        status = _status.get(job_id, {})
        detail_key = status.get("detail_key")
        jobs.append(
            {
                "id": job_id,
                "label": translate(meta["label_key"]),
                "cron": os.environ.get(meta["cron_env"], meta["default_cron"]),
                "enabled": os.environ.get(meta["enabled_env"], "1") == "1",
                "next_run": aps_job.next_run_time if aps_job else None,
                "last_run": status.get("last_run"),
                "last_ok": status.get("ok"),
                "last_detail": translate(detail_key, **status.get("detail_kwargs", {})) if detail_key else None,
                "last_error": status.get("error"),
                "running": job_id in _running,
            }
        )
    return jobs


def set_cron(job_id: str, cron_expr: str) -> None:
    meta = JOBS[job_id]
    trigger = CronTrigger.from_crontab(cron_expr)  # leve ValueError si invalide
    set_key(_ENV_PATH, meta["cron_env"], cron_expr, quote_mode="never")
    os.environ[meta["cron_env"]] = cron_expr
    # Le job peut ne pas etre enregistre dans ce process (ex. processus parent
    # du reloader Flask en debug, cf. init_app) : la valeur en .env est malgre
    # tout persistee, elle sera reprise au prochain (re)demarrage du scheduler.
    if _scheduler.get_job(job_id) is not None:
        _scheduler.reschedule_job(job_id, trigger=trigger)


def set_enabled(job_id: str, enabled: bool) -> None:
    meta = JOBS[job_id]
    set_key(_ENV_PATH, meta["enabled_env"], "1" if enabled else "0", quote_mode="never")
    os.environ[meta["enabled_env"]] = "1" if enabled else "0"
    if _scheduler.get_job(job_id) is not None:
        if enabled:
            _scheduler.resume_job(job_id)
        else:
            _scheduler.pause_job(job_id)


def is_running() -> bool:
    return _scheduler.running


def run_now(job_id: str) -> None:
    if not _scheduler.running:
        from app.i18n import translate

        raise RuntimeError(translate("admin_msg_scheduler_not_running"))
    # Marque le job "en cours" des maintenant : la page admin rechargee juste
    # apres le POST doit l'afficher comme tel, meme si le thread du scheduler
    # ne l'a pas encore demarre.
    _running.setdefault(job_id, datetime.now())
    _scheduler.add_job(
        _wrap(job_id),
        trigger="date",
        run_date=datetime.now(),
        id=f"{job_id}_manual_{datetime.now().timestamp()}",
        misfire_grace_time=None,  # toujours executer, sinon l'etat "en cours" resterait bloque
    )
