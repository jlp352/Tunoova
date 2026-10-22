"""Pilotage de Tailscale pour l'onglet Admin > Reseau (cf.
Doc/Documentation technique.md).

L'appli n'utilise pas l'API web de Tailscale : elle appelle la commande
`tailscale` du serveur (sans shell), autorisee pour le compte de l'appli par
`tailscale set --operator=<compte>` (install.sh). Les commandes qui peuvent
attendre une action de l'utilisateur sur le site de Tailscale (connexion,
autorisation de Funnel) tournent en arriere-plan : la page affiche le lien
d'activation qu'elles impriment, puis interroge l'etat jusqu'a la fin.

Le mode actif (prive/public) n'est pas stocke ici : il est relu a chaque fois
dans la config serve de Tailscale, seule source de verite."""

import json
import re
import shutil
import subprocess
import threading
import time
from collections import deque

CONSOLE_URL = "https://login.tailscale.com/admin"
COMMAND_TIMEOUT = 15
# Attente de la fin d'une commande d'arriere-plan avant de rendre la main a
# la page (au-dela, elle attend en general une action sur le site Tailscale).
LOGIN_WAIT_SECONDS = 20
# Delai laisse aux serveurs Tailscale pour reconnaitre la machine apres une
# reconnexion, avant de conclure qu'elle a ete supprimee de la console.
RECOGNITION_WAIT_SECONDS = 8
ENABLE_WAIT_SECONDS = 10

# Lien d'activation imprime par la commande (connexion, Funnel, HTTPS). Ce
# texte n'est pas une interface officielle : expression volontairement large.
_LOGIN_URL_RE = re.compile(r"https://login\.tailscale\.com/[^\s\"'<>]+")
_ACCESS_DENIED_RE = re.compile(r"access denied|permission denied|--operator|use 'sudo tailscale", re.IGNORECASE)


class TailscaleError(Exception):
    pass


class _BackgroundCommand:
    """Commande `tailscale` lancee en arriere-plan, dont la sortie est gardee
    en memoire (lien d'activation, message d'erreur)."""

    def __init__(self, kind: str, args: list[str]):
        self.kind = kind
        self.lines = deque(maxlen=50)
        self.proc = subprocess.Popen(
            args, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
        )
        threading.Thread(target=self._read_output, daemon=True).start()

    def _read_output(self):
        for line in self.proc.stdout:
            self.lines.append(line.rstrip())

    @property
    def running(self) -> bool:
        return self.proc.poll() is None

    @property
    def failed(self) -> bool:
        return not self.running and self.proc.returncode != 0

    def output(self) -> str:
        return "\n".join(self.lines).strip()

    def login_url(self) -> str | None:
        urls = _LOGIN_URL_RE.findall(self.output())
        return urls[-1] if urls else None

    def wait(self, timeout: float):
        try:
            self.proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            pass

    def stop(self):
        if self.running:
            self.proc.terminate()


_lock = threading.Lock()
_task: _BackgroundCommand | None = None


def binary() -> str | None:
    return shutil.which("tailscale")


def _run(*args, timeout=COMMAND_TIMEOUT) -> str:
    exe = binary()
    if not exe:
        raise TailscaleError("tailscale not installed")
    try:
        result = subprocess.run(
            [exe, *args], stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=timeout, check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise TailscaleError(str(exc)) from exc
    if result.returncode != 0:
        raise TailscaleError((result.stderr or result.stdout).strip() or f"tailscale {args[0]} failed")
    return result.stdout


def _run_json(*args) -> dict:
    out = _run(*args).strip()
    try:
        return json.loads(out) if out else {}
    except ValueError as exc:
        raise TailscaleError(f"tailscale {args[0]}: invalid JSON output") from exc


def _exposure(serve_config: dict) -> str:
    """off / private / public d'apres `tailscale serve status --json`."""
    if not (serve_config.get("Web") or serve_config.get("TCP")):
        return "off"
    return "public" if any((serve_config.get("AllowFunnel") or {}).values()) else "private"


def is_access_denied(message: str | None) -> bool:
    return bool(message) and bool(_ACCESS_DENIED_RE.search(message))


def _daemon_failed() -> bool:
    """tailscaled plante au demarrage (ex. /dev/net/tun absent dans un
    conteneur) : le relancer ne suffit pas. Lecture seule, sans droits root."""
    exe = shutil.which("systemctl")
    if not exe:
        return False
    try:
        return subprocess.run([exe, "is-failed", "--quiet", "tailscaled"], timeout=5, check=False).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def status() -> dict:
    """Etat de Tailscale sur le serveur :
    - state : NotInstalled, NoDaemon (tailscaled arrete), DaemonFailed
      (tailscaled en echec pour systemd), Connecting (Running pour
      tailscaled mais machine pas encore reconnue par les serveurs Tailscale,
      cf. plus bas) ou BackendState (NeedsLogin, NeedsMachineAuth, Stopped,
      Starting, Running) ;
    - auth_url : lien de connexion a ouvrir (NeedsLogin) ;
    - hostname, url (https://<machine>.<tailnet>.ts.net) ;
    - exposure : off / private / public (config serve actuelle) ;
    - task : commande d'arriere-plan en cours ou terminee en erreur
      ({kind, running, url, error}) ;
    - error : message si l'etat n'a pas pu etre lu."""
    info = {
        "state": "NotInstalled", "auth_url": None, "hostname": None, "url": None,
        "exposure": "off", "task": None, "error": None,
    }
    if not binary():
        return info

    try:
        st = _run_json("status", "--json")
    except TailscaleError as exc:
        info["state"] = "DaemonFailed" if _daemon_failed() else "NoDaemon"
        info["error"] = str(exc)[:500]
        return info

    info["state"] = st.get("BackendState") or "NoDaemon"
    info["auth_url"] = st.get("AuthURL") or None
    me = st.get("Self") or {}
    info["hostname"] = me.get("HostName")
    dns_name = (me.get("DNSName") or "").rstrip(".")
    if dns_name:
        info["url"] = f"https://{dns_name}"
    # A la reconnexion, tailscaled se dit Running des qu'il a repris sa
    # derniere config locale, avant que les serveurs Tailscale aient confirme
    # la machine (Self.Online). Elle a pu etre supprimee de la console
    # entre-temps : elle n'est connectee qu'une fois reconnue.
    if info["state"] == "Running" and me.get("Online") is False:
        info["state"] = "Connecting"

    if info["state"] == "Running":
        try:
            info["exposure"] = _exposure(_run_json("serve", "status", "--json"))
        except TailscaleError as exc:
            info["error"] = str(exc)[:500]

    with _lock:
        task = _task
    if task and (task.running or task.failed):
        info["task"] = {
            "kind": task.kind,
            "running": task.running,
            "url": task.login_url(),
            "error": task.output()[-1500:] if task.failed else None,
        }
        if task.kind == "login" and not info["auth_url"]:
            info["auth_url"] = task.login_url()
    return info


def _start(kind: str, *args, restart: bool = False) -> _BackgroundCommand:
    global _task
    exe = binary()
    if not exe:
        raise TailscaleError("tailscale not installed")
    with _lock:
        if _task and _task.running:
            if _task.kind == kind and not restart:
                return _task
            _task.stop()
        try:
            _task = _BackgroundCommand(kind, [exe, *args])
        except OSError as exc:
            raise TailscaleError(str(exc)) from exc
        return _task


def connect() -> dict:
    """Connecte le serveur au tailnet, en partant de l'etat reel :
    - deja connecte et reconnu (Running) : rien a faire ;
    - NeedsLogin : nouvelle connexion (`tailscale up` imprime le lien) ;
    - Stopped (mode Local) : reconnexion avec le compte memorise
      (`tailscale up`). Si la machine a ete supprimee de la console
      Tailscale entre-temps, elle n'est pas reconnue (NeedsLogin, ou
      Connecting qui dure) : on enchaine alors sur une nouvelle connexion
      (`tailscale up --force-reauth`).
    Toujours `up`, jamais `tailscale login` : celle-ci gere les profils et
    est reservee a root ("profiles access denied" pour le compte operateur).
    `up` lance par le compte operateur conserve --operator. Attend jusqu'a LOGIN_WAIT_SECONDS que la connexion aboutisse
    ou que le lien soit disponible, et retourne l'etat."""
    st = status()
    if st["state"] == "Running":
        return st
    task, relogin = None, False
    if st["state"] == "NeedsLogin":
        task, relogin = _start("login", "up", restart=True), True
    elif st["state"] == "Stopped":
        task = _start("login", "up", restart=True)
    start = time.monotonic()
    while time.monotonic() - start < LOGIN_WAIT_SECONDS:
        time.sleep(0.5)
        st = status()
        if st["state"] == "Running" or st["auth_url"]:
            break
        if relogin:
            if not task.running:
                break
            continue
        not_recognized = st["state"] == "NeedsLogin" or (
            st["state"] == "Connecting" and time.monotonic() - start >= RECOGNITION_WAIT_SECONDS
        )
        if not_recognized:
            task, relogin = _start("login", "up", "--force-reauth", restart=True), True
        elif task and not task.running and st["state"] not in ("Connecting", "Starting"):
            break
    return st


def enable(mode: str, port: int) -> str:
    """Expose l'appli (http://127.0.0.1:<port>) en mode "private" (tailscale
    serve) ou "public" (tailscale funnel), en HTTPS sur le port 443. Retourne
    "ok", "pending" (attend une autorisation sur le site Tailscale : lien dans
    status()["task"]) ou leve TailscaleError."""
    if mode not in ("private", "public"):
        raise ValueError(mode)
    # Une seule config serve : on repart de zero pour passer de prive a public
    # (et inversement) sans garder l'autre.
    _run("serve", "reset")
    task = _start(mode, "funnel" if mode == "public" else "serve", "--bg", str(port))
    task.wait(ENABLE_WAIT_SECONDS)
    if task.running:
        return "pending"
    if task.failed:
        raise TailscaleError(task.output()[-1500:] or f"tailscale exited with code {task.proc.returncode}")
    return "ok"


def disable(connected: bool):
    """Retire l'exposition serve/funnel et deconnecte le serveur du tailnet
    (`tailscale down`), sinon l'appli resterait joignable par l'IP Tailscale
    du serveur (http://100.x.y.z:<port>). Le compte reste memorise : enable()
    apres connect() reconnecte sans nouvelle connexion sur le site.
    Abandonne aussi une connexion ou activation en attente. `connected` :
    Tailscale est Running (sinon serve/down n'ont rien a retirer)."""
    global _task
    with _lock:
        if _task:
            _task.stop()
            _task = None
    if connected:
        _run("serve", "reset")
        _run("down")
