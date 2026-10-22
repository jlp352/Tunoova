"""Backup de la base SQLite, declenche par le job planifie `db_backup`
(voir app/scheduler.py). Copie via sqlite3.Connection.backup() plutot qu'une
simple copie de fichier : reste coherent meme si l'appli ecrit en base
pendant la sauvegarde. Liste, telechargement et restauration des backups
depuis l'onglet Backup de l'Espace Admin (app/admin/backups.py)."""

import glob
import os
import re
import shutil
import sqlite3
import tempfile
import time
import zipfile
from datetime import datetime

from flask import current_app

from app.config import BASE_DIR
from app.extensions import db
from app.profile_service import UPLOAD_DIR as PROFILE_PHOTOS_DIR


def db_path() -> str:
    uri = current_app.config["SQLALCHEMY_DATABASE_URI"]
    return uri.removeprefix("sqlite:///")


def db_size() -> int:
    """Taille en octets du fichier SQLite courant (0 si absent)."""
    path = db_path()
    return os.path.getsize(path) if os.path.exists(path) else 0


def _resolve_backup_dir() -> str:
    path = current_app.config["BACKUP_PATH"]
    if not os.path.isabs(path):
        path = os.path.join(BASE_DIR, path)
    return path


def _backup_dir() -> str:
    path = _resolve_backup_dir()
    os.makedirs(path, exist_ok=True)
    return path


def create_backup(prefix: str = "") -> str:
    """Cree une copie horodatee de la base et purge les backups perimes.
    `prefix` distingue les backups de securite ("pre_restore_"), comme
    "pre_update_" pour ceux de install.sh. Retourne le chemin du fichier cree."""
    backup_dir = _backup_dir()
    timestamp = prefix + datetime.now().strftime("%Y%m%d_%H%M%S")
    dest_path = os.path.join(backup_dir, f"tunoova_{timestamp}.db")

    src = sqlite3.connect(db_path())
    dest = sqlite3.connect(dest_path)
    try:
        src.backup(dest)
    finally:
        dest.close()
        src.close()

    _backup_profile_photos(backup_dir, timestamp)
    _purge_old_backups(backup_dir)
    return dest_path


def _backup_profile_photos(backup_dir: str, timestamp: str) -> None:
    """Archive db/profile_photos avec la meme sauvegarde : ces fichiers ne sont
    pas dans le depot git (cf. .gitignore "db/*"), donc pas d'autre filet que
    ce backup en cas de perte du disque."""
    if not os.path.isdir(PROFILE_PHOTOS_DIR) or not os.listdir(PROFILE_PHOTOS_DIR):
        return
    dest_path = os.path.join(backup_dir, f"tunoova_photos_{timestamp}.zip")
    with zipfile.ZipFile(dest_path, "w", zipfile.ZIP_DEFLATED) as archive:
        for filename in os.listdir(PROFILE_PHOTOS_DIR):
            archive.write(os.path.join(PROFILE_PHOTOS_DIR, filename), arcname=filename)


def _purge_old_backups(backup_dir: str) -> None:
    retention_days = current_app.config["BACKUP_RETENTION_DAYS"]
    cutoff = time.time() - retention_days * 86400
    for path in glob.glob(os.path.join(backup_dir, "tunoova_*.db")) + glob.glob(
        os.path.join(backup_dir, "tunoova_photos_*.zip")
    ):
        if os.path.getmtime(path) < cutoff:
            os.remove(path)


# tunoova_<cle>.db et son archive photos tunoova_photos_<cle>.zip, ou <cle> est
# l'horodatage, precede de pre_update_ (install.sh) ou pre_restore_ (ci-dessous).
_DB_RE = re.compile(r"^tunoova_((pre_update_|pre_restore_)?\d{8}_\d{6})\.db$")
_PHOTOS_RE = re.compile(r"^tunoova_photos_((pre_update_|pre_restore_)?\d{8}_\d{6})\.zip$")


class BackupError(Exception):
    def __init__(self, key: str, **kwargs):
        super().__init__(key)
        self.key = key
        self.kwargs = kwargs


def list_backups() -> list[dict]:
    """Backups de la base, du plus recent au plus ancien, avec l'archive
    photos du meme backup s'il y en a une."""
    backup_dir = _resolve_backup_dir()
    if not os.path.isdir(backup_dir):
        return []
    backups = []
    for name in os.listdir(backup_dir):
        match = _DB_RE.match(name)
        path = os.path.join(backup_dir, name)
        if not match or not os.path.isfile(path):
            continue
        photos_name = f"tunoova_photos_{match.group(1)}.zip"
        photos_path = os.path.join(backup_dir, photos_name)
        has_photos = os.path.isfile(photos_path)
        backups.append({
            "name": name,
            "kind": (match.group(2) or "auto_").rstrip("_"),
            "created_at": datetime.fromtimestamp(os.path.getmtime(path)),
            "size": os.path.getsize(path),
            "photos_name": photos_name if has_photos else None,
            "photos_size": os.path.getsize(photos_path) if has_photos else None,
        })
    return sorted(backups, key=lambda b: b["created_at"], reverse=True)


def backup_file_path(name: str) -> str | None:
    """Chemin d'un fichier de backup (base ou archive photos) d'apres son nom,
    ou None s'il n'existe pas. Le nom doit suivre le format des backups : pas
    de chemin arbitraire (../, autre dossier) depuis une URL."""
    if not (_DB_RE.match(name) or _PHOTOS_RE.match(name)):
        return None
    path = os.path.join(_resolve_backup_dir(), name)
    return path if os.path.isfile(path) else None


# Limite de taille decompressee d'une archive envoyee (protection zip bomb).
BUNDLE_MAX_UNCOMPRESSED = 2 * 1024 * 1024 * 1024
_BUNDLE_PHOTOS_DIR = "profile_photos/"


def build_bundle(name: str):
    """Archive zip d'un backup complet, pour le telechargement : la base
    (tunoova_<cle>.db) et les photos de profil du meme backup dans un dossier
    profile_photos/. Retourne (fichier temporaire positionne au debut, nom du
    zip), ou None si le backup n'existe pas."""
    path = backup_file_path(name)
    match = _DB_RE.match(name)
    if not path or not match:
        return None
    key = match.group(1)
    bundle = tempfile.SpooledTemporaryFile(max_size=50 * 1024 * 1024)
    with zipfile.ZipFile(bundle, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.write(path, arcname=name)
        # Dossier toujours present (meme vide) : restore_bundle l'exige.
        archive.writestr(_BUNDLE_PHOTOS_DIR, "")
        photos_path = backup_file_path(f"tunoova_photos_{key}.zip")
        if photos_path:
            with zipfile.ZipFile(photos_path) as photos:
                for member in photos.infolist():
                    if not member.is_dir():
                        archive.writestr(f"{_BUNDLE_PHOTOS_DIR}{os.path.basename(member.filename)}", photos.read(member))
    bundle.seek(0)
    return bundle, f"tunoova_backup_{key}.zip"


def _open_readonly(path: str) -> sqlite3.Connection:
    # immutable : un backup ne change pas pendant la lecture, et SQLite ne cree
    # alors pas de fichiers -wal/-shm a cote (base copiee d'une base en WAL).
    return sqlite3.connect(f"file:{path}?mode=ro&immutable=1", uri=True)


def _check_database(path: str) -> None:
    try:
        conn = _open_readonly(path)
        try:
            if conn.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                raise BackupError("admin_backup_invalid")
            tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        finally:
            conn.close()
    except sqlite3.DatabaseError as exc:
        raise BackupError("admin_backup_invalid") from exc
    if "Users" not in tables:
        raise BackupError("admin_backup_invalid")


def _replace_photos(archive: zipfile.ZipFile, members: list) -> None:
    """Remplace le contenu de db/profile_photos par les photos de l'archive
    (entrees profile_photos/<fichier>, ecrites sous leur seul nom de fichier :
    pas d'ecriture hors du dossier)."""
    os.makedirs(PROFILE_PHOTOS_DIR, exist_ok=True)
    for filename in os.listdir(PROFILE_PHOTOS_DIR):
        path = os.path.join(PROFILE_PHOTOS_DIR, filename)
        if os.path.isfile(path) or os.path.islink(path):
            os.remove(path)
    for member in members:
        dest_path = os.path.join(PROFILE_PHOTOS_DIR, os.path.basename(member.filename))
        with archive.open(member) as src, open(dest_path, "wb") as dest:
            shutil.copyfileobj(src, dest)


def restore_bundle(upload) -> dict:
    """Restauration globale depuis une archive telechargee via l'onglet Backup
    (cf. build_bundle) : remplace la base et les photos de profil. Verifie
    d'abord que l'archive est un zip contenant une base Tunoova valide ; rien
    n'est modifie sinon. Un backup "pre_restore_" de l'etat actuel est cree
    avant, pour pouvoir revenir en arriere. L'appli doit etre redemarree
    ensuite (caches en memoire : scheduler, catalogues...).
    Retourne {"safety": nom du backup de securite, "photos": nb de photos}."""
    if not zipfile.is_zipfile(upload):
        raise BackupError("admin_backup_not_zip")
    upload.seek(0)
    with zipfile.ZipFile(upload) as archive:
        if not any(m.filename.startswith(_BUNDLE_PHOTOS_DIR) for m in archive.infolist()):
            raise BackupError("admin_backup_no_photos")
        members = [m for m in archive.infolist() if not m.is_dir()]
        if sum(m.file_size for m in members) > BUNDLE_MAX_UNCOMPRESSED:
            raise BackupError("admin_backup_too_big")
        db_members = [m for m in members if "/" not in m.filename and m.filename.endswith(".db")]
        if len(db_members) != 1:
            raise BackupError("admin_backup_no_db")
        photo_members = [
            m for m in members
            if m.filename.startswith(_BUNDLE_PHOTOS_DIR)
            and os.path.basename(m.filename) == m.filename[len(_BUNDLE_PHOTOS_DIR):]
            and m.filename[len(_BUNDLE_PHOTOS_DIR):] not in ("", ".", "..")
        ]

        # Base extraite dans le dossier des backups (meme disque que la base
        # courante, plutot que /tmp souvent en RAM sur un Raspberry Pi).
        fd, db_tmp = tempfile.mkstemp(prefix="restore_", suffix=".tmp", dir=_backup_dir())
        try:
            with os.fdopen(fd, "wb") as dest, archive.open(db_members[0]) as src:
                shutil.copyfileobj(src, dest)
            _check_database(db_tmp)
            safety_path = create_backup(prefix="pre_restore_")
            _restore_database(db_tmp)
        finally:
            os.remove(db_tmp)
        _replace_photos(archive, photo_members)
    return {"safety": os.path.basename(safety_path), "photos": len(photo_members)}


def _restore_database(path: str) -> None:
    # Copie via l'API backup de SQLite (verrouille la base cible pendant la
    # copie) apres avoir ferme les connexions SQLAlchemy du pool.
    db.session.remove()
    db.engine.dispose()
    src = _open_readonly(path)
    dest = sqlite3.connect(db_path())
    try:
        src.backup(dest)
    finally:
        dest.close()
        src.close()

    # Un backup plus ancien que le code peut manquer des tables/colonnes :
    # meme mise a niveau qu'au demarrage (run.py).
    from db.seed import ensure_schema_upgrades

    db.create_all()
    ensure_schema_upgrades()


def format_size(num_bytes: int) -> str:
    size = float(num_bytes)
    for unit in ("o", "Ko", "Mo", "Go"):
        if size < 1024 or unit == "Go":
            if unit == "o":
                return f"{size:.0f} {unit}"
            text = f"{size:.1f}".removesuffix(".0")
            return f"{text} {unit}"
        size /= 1024
    return f"{size:.1f} Go"
