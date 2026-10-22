"""Mise a jour de l'appli depuis l'Espace Admin (bouton Update).

L'appli ne lance pas install.sh elle-meme : elle tourne sous un compte sans
droits root et serait arretee par la mise a jour. Elle depose seulement un
fichier de demande, surveille par le service systemd root "<service>-update"
installe par install.sh (setup_update_access / install_update_service), qui
execute "install.sh update" depuis un miroir du depot appartenant a root.

La verification d'une nouvelle version fait un git fetch du depot GitHub
(anonyme si public, deploy key en lecture seule si prive, cf. update.conf)
dans le clone de l'appli et compare son HEAD a celui de la branche distante.
Indisponible hors installation par install.sh (dev, Windows...)."""

import os
import re
import subprocess
from datetime import datetime

from app.config import BASE_DIR

CONF_DIR = "/etc/tunoova"
UPDATE_CONF = os.path.join(CONF_DIR, "update.conf")
# Copie de la deploy key appartenant au compte de l'appli (cf. install.sh).
DEPLOY_KEY = os.path.join(CONF_DIR, "deploy_key_app")
KNOWN_HOSTS = os.path.join(CONF_DIR, "known_hosts")
STATE_DIR = "/var/lib/tunoova"
REQUEST_FILE = os.path.join(STATE_DIR, "request", "update")
STATUS_FILE = os.path.join(STATE_DIR, "update.status")
UPDATE_LOG = "/var/log/tunoova-update.log"

LOG_TAIL_LINES = 60
_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")
_VERSION_RE = re.compile(r'APP_VERSION\s*=\s*"([^"]+)"')

# Resultat de la derniere verification (memoire du process, comme le statut
# des jobs dans app/scheduler.py).
_last_check: dict | None = None


def read_conf() -> dict | None:
    try:
        with open(UPDATE_CONF, encoding="utf-8") as f:
            lines = f.read().splitlines()
    except OSError:
        return None
    conf = {}
    for line in lines:
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            conf[key.strip()] = value.strip()
    return conf if conf.get("REPO_URL") and conf.get("REPO_BRANCH") else None


def is_available() -> bool:
    return read_conf() is not None and os.access(os.path.dirname(REQUEST_FILE), os.W_OK)


def _git(*args, timeout=60, env=None) -> str:
    result = subprocess.run(
        ["git", "-C", BASE_DIR, *args],
        capture_output=True, text=True, timeout=timeout, env=env, check=False,
    )
    if result.returncode != 0:
        raise RuntimeError((result.stderr or result.stdout).strip() or f"git {args[0]} failed")
    return result.stdout.strip()


def check_for_update() -> dict:
    """Compare le code installe a la branche GitHub. Retourne un dict
    {available, local_version, remote_version, checked_at, error}."""
    global _last_check
    from app.version import APP_VERSION

    result = {
        "available": False,
        "local_version": APP_VERSION,
        "remote_version": None,
        "checked_at": datetime.now(),
        "error": None,
    }
    conf = read_conf()
    if not conf:
        result["error"] = "unavailable"
        return result

    env = dict(os.environ, GIT_TERMINAL_PROMPT="0")
    if not conf["REPO_URL"].startswith("https://"):
        env["GIT_SSH_COMMAND"] = (
            f"ssh -i {DEPLOY_KEY} -o IdentitiesOnly=yes -o UserKnownHostsFile={KNOWN_HOSTS} "
            "-o StrictHostKeyChecking=yes -o BatchMode=yes"
        )
    try:
        _git("fetch", "--quiet", conf["REPO_URL"], conf["REPO_BRANCH"], env=env)
        remote_sha = _git("rev-parse", "FETCH_HEAD")
        local_sha = _git("rev-parse", "HEAD")
        match = _VERSION_RE.search(_git("show", "FETCH_HEAD:app/version.py"))
        result["remote_version"] = match.group(1) if match else None
        result["available"] = remote_sha != local_sha
    except (RuntimeError, OSError, subprocess.TimeoutExpired) as exc:
        result["error"] = str(exc)[:300]

    _last_check = result
    return result


def last_check() -> dict | None:
    return _last_check


def get_status() -> dict:
    """Etat de la derniere mise a jour lancee : running / success / failed
    (None si aucune), date, et fin du journal d'execution."""
    state, at = None, None
    try:
        with open(STATUS_FILE, encoding="utf-8") as f:
            parts = f.read().split()
        if parts:
            state = parts[0]
            at = parts[1] if len(parts) > 1 else None
    except OSError:
        pass
    # Demande deposee mais pas encore prise en charge par le service (le
    # fichier de statut garde alors le resultat de la mise a jour precedente).
    if state != "running" and os.path.exists(REQUEST_FILE):
        state = "requested"

    log = ""
    try:
        with open(UPDATE_LOG, encoding="utf-8", errors="replace") as f:
            log = "".join(f.readlines()[-LOG_TAIL_LINES:])
    except OSError:
        pass
    return {"state": state, "at": at, "log": _ANSI_RE.sub("", log)}


# Version annoncee aux utilisateurs (ecran Notifications) ; la notification
# reste affichee tant que cette version n'est pas installee.
ANNOUNCED_VERSION_KEY = "UPDATE_ANNOUNCED_VERSION"


def notify_update_available(check: dict) -> None:
    """Previent tous les utilisateurs qu'une nouvelle version est disponible
    et que l'admin va bientot l'installer. Rien si la version distante n'a
    pas change de numero (commits sans changement de APP_VERSION)."""
    from app.admin.config_settings import get_env_from_file, set_env

    version = check.get("remote_version")
    if not check.get("available") or check.get("error") or not version or version == check.get("local_version"):
        return
    if get_env_from_file(ANNOUNCED_VERSION_KEY) != version:
        set_env(ANNOUNCED_VERSION_KEY, version)


def announced_version() -> str | None:
    """Version annoncee et pas encore installee, sinon None. Relue depuis le
    .env (et non os.environ) : l'annonce a pu etre faite par un autre worker."""
    from app.admin.config_settings import get_env_from_file
    from app.version import APP_VERSION

    version = get_env_from_file(ANNOUNCED_VERSION_KEY)
    return version if version and version != APP_VERSION else None


def request_update() -> bool:
    """Depose la demande de mise a jour (le service root la prend en charge
    en quelques secondes). Refuse si une mise a jour est deja en cours."""
    if not is_available() or get_status()["state"] in ("running", "requested"):
        return False
    with open(REQUEST_FILE, "w", encoding="utf-8") as f:
        f.write(datetime.now().isoformat())
    return True
