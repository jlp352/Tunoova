"""Admin : explorateur de fichiers MP3 (onglet MP3) - navigation dans
MP3_LIBRARY_PATH, dossiers, upload, renommage, deplacement (glisser-deposer),
suppression et edition des tags. Logique dans app/mp3_files_service.py ;
les actions repondent en JSON {ok, message} (fetch de admin/mp3_browser.html)."""

from flask import render_template, request, jsonify

from app.admin import bp
from app.admin.decorators import admin_required
from app.i18n import translate
from app import mp3_files_service as files, scheduler
from app.mp3_files_service import Mp3FileError


def _ok(message_key: str | None = None, **data):
    return jsonify(ok=True, message=translate(message_key, **data) if message_key else "", **data)


def _error(exc: Mp3FileError):
    return jsonify(ok=False, message=translate(exc.key, **exc.kwargs))


def _form(name: str) -> str:
    return request.form.get(name, "")


@bp.route("/mp3")
@admin_required
def mp3_browser():
    path = request.args.get("path", "")
    try:
        listing = files.list_dir(path)
        error = None
    except Mp3FileError as exc:
        listing = files.list_dir("")
        error = translate(exc.key, **exc.kwargs)
    return render_template(
        "admin/mp3_browser.html",
        listing=listing,
        error=error,
        root_path=files.root(),
        tag_fields=files.TAG_FIELDS,
        scan_running=any(j["id"] == "mp3_scan" and j["running"] for j in scheduler.list_jobs()),
    )


@bp.route("/mp3/folder", methods=["POST"])
@admin_required
def mp3_create_folder():
    try:
        files.create_folder(_form("path"), _form("name"))
    except Mp3FileError as exc:
        return _error(exc)
    return _ok()


@bp.route("/mp3/rename", methods=["POST"])
@admin_required
def mp3_rename():
    try:
        files.rename(_form("path"), _form("name"))
    except Mp3FileError as exc:
        return _error(exc)
    files.sync_catalog()
    return _ok()


@bp.route("/mp3/move", methods=["POST"])
@admin_required
def mp3_move():
    try:
        files.move(_form("path"), _form("dest"))
    except Mp3FileError as exc:
        return _error(exc)
    files.sync_catalog()
    return _ok()


@bp.route("/mp3/delete-info")
@admin_required
def mp3_delete_info():
    """Nombre de MP3 touches par la suppression d'un dossier (confirmation)."""
    try:
        return jsonify(ok=True, count=files.count_mp3(request.args.get("path", "")))
    except Mp3FileError as exc:
        return _error(exc)


@bp.route("/mp3/delete", methods=["POST"])
@admin_required
def mp3_delete():
    try:
        files.delete(_form("path"))
    except Mp3FileError as exc:
        return _error(exc)
    files.sync_catalog()
    return _ok()


@bp.route("/mp3/upload", methods=["POST"])
@admin_required
def mp3_upload():
    """Un lot de fichiers (la page envoie par lots, cf. mp3_browser.html) ;
    `names` donne le chemin relatif de chaque fichier d'un dossier envoye.
    Le catalogue est mis a jour une fois tous les lots envoyes (/mp3/sync)."""
    dest = _form("path")
    uploads = request.files.getlist("files")
    names = request.form.getlist("names")
    saved = skipped = 0
    try:
        for i, storage in enumerate(uploads):
            relative_name = names[i] if i < len(names) and names[i] else None
            if files.save_upload(dest, storage, relative_name):
                saved += 1
            else:
                skipped += 1
    except Mp3FileError as exc:
        return _error(exc)
    return jsonify(ok=True, saved=saved, skipped=skipped)


@bp.route("/mp3/sync", methods=["POST"])
@admin_required
def mp3_sync():
    stats = files.sync_catalog()
    return _ok("admin_mp3_sync_done", added=stats["added"], updated=stats["updated"])


@bp.route("/mp3/scan", methods=["POST"])
@admin_required
def mp3_scan():
    """Relance le job 'mp3_scan' (avec recherche des pochettes) ; la page
    suit son avancement via /admin/jobs/status."""
    try:
        scheduler.run_now("mp3_scan")
    except RuntimeError as exc:
        return jsonify(ok=False, message=str(exc))
    return jsonify(ok=True)


@bp.route("/mp3/tags")
@admin_required
def mp3_tags():
    try:
        return jsonify(ok=True, tags=files.read_tags(request.args.get("path", "")))
    except Mp3FileError as exc:
        return _error(exc)


@bp.route("/mp3/tags", methods=["POST"])
@admin_required
def mp3_tags_save():
    path = _form("path")
    values = {field: _form(field) for field in files.TAG_FIELDS}
    try:
        if _form("folder") == "1":
            count = files.write_folder_tags(path, values)
        else:
            files.write_tags(path, values)
            count = 1
    except Mp3FileError as exc:
        return _error(exc)
    files.sync_catalog()
    return _ok("admin_mp3_tags_saved", count=count)
