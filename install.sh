#!/usr/bin/env bash
#
# install.sh - Install / update / reset the admin password for the
#              Tunoova application (Flask + SQLite).
#
# Prerequisite: the repository has already been cloned manually (git clone
# ...), and this script is run FROM the root of that clone:
#
#   git clone https://github.com/jlp352/Tunoova.git
#   cd Tunoova
#   sudo ./install.sh
#
# The script never clones the repository itself: it operates on the folder
# it is located in (auto-detected). "update" runs a `git pull` on that same
# folder to fetch new commits.
#
# Usage:
#   sudo ./install.sh [install|update|reset]
#
#   (no argument): auto-detects whether this is a first install or an
#                  update.
#   install       : force a first-time install.
#   update        : force an update of an existing install.
#   reset         : reset the admin account password.
#
# The install also sets up the "Update" button of the admin space: a root
# systemd service (${SERVICE_NAME}-update) started when the application drops
# a request file, which runs "install.sh update" from a root-owned mirror of
# the GitHub repository (the application itself never gets root rights). If
# the repository is private, a read-only deploy key is generated and must be
# added once on GitHub.
#
# Must be run as root (sudo). Written for an apt-based distribution
# (Debian/Ubuntu); adapt if using another distribution.
#
set -Eeuo pipefail

# --- Configuration (overridable via environment variables) -----------------
APP_NAME="tunoova"
APP_USER="${APP_USER:-tunoova}"
APP_GROUP="${APP_GROUP:-tunoova}"
# Default: the folder containing install.sh itself, i.e. the existing git
# clone. Overridable if the script is copied elsewhere.
SCRIPT_DIR="$(cd -- "$(dirname -- "$(readlink -f -- "${BASH_SOURCE[0]}")")" &>/dev/null && pwd)"
APP_DIR="${APP_DIR:-$SCRIPT_DIR}"
SERVICE_NAME="${SERVICE_NAME:-tunoova}"
DEFAULT_PORT="${DEFAULT_PORT:-5000}"
PYTHON_BIN="${PYTHON_BIN:-python3}"
VENV_DIR="${APP_DIR}/.venv"
PORT_FILE="${APP_DIR}/.tunoova_port"

# Admin "Update" button (see setup_update_access / install_update_service).
# Everything executed as root comes from these root-owned locations, never
# from APP_DIR (owned by APP_USER, hence writable by the application).
CONF_DIR="/etc/${APP_NAME}"
UPDATE_CONF="${CONF_DIR}/update.conf"
DEPLOY_KEY="${CONF_DIR}/deploy_key"
# Copie de la meme cle pour APP_USER (verification de nouvelle version) : ssh
# refuse une cle privee lisible par un autre compte que son proprietaire.
APP_DEPLOY_KEY="${CONF_DIR}/deploy_key_app"
KNOWN_HOSTS="${CONF_DIR}/known_hosts"
STATE_DIR="/var/lib/${APP_NAME}"
MIRROR_DIR="${STATE_DIR}/src"
REQUEST_DIR="${STATE_DIR}/request"
REQUEST_FILE="${REQUEST_DIR}/update"
STATUS_FILE="${STATE_DIR}/update.status"
UPDATE_LOG="/var/log/${APP_NAME}-update.log"
UPDATER_BIN="/usr/local/sbin/${APP_NAME}-update"
UPDATE_SERVICE="${SERVICE_NAME}-update"
# Published GitHub host key (avoids trusting the first ssh-keyscan answer).
GITHUB_HOST_KEY="github.com ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIOMqqnkVzrm0SdG6UOoqKLsabgH5C9okWi0dh2l9GKJl"

# --- Output ------------------------------------------------------------------
c_info="\033[1;34m"; c_ok="\033[1;32m"; c_warn="\033[1;33m"; c_err="\033[1;31m"; c_off="\033[0m"
info()    { echo -e "${c_info}[info]${c_off} $*"; }
success() { echo -e "${c_ok}[ok]${c_off} $*"; }
warn()    { echo -e "${c_warn}[warning]${c_off} $*"; }
error()   { echo -e "${c_err}[error]${c_off} $*" >&2; }

# "set -e" stops the script as soon as a command fails, but without saying
# which one: this trap prints the offending line and command so a failure
# is never silent and hard to diagnose.
trap 'error "Failed at line ${LINENO}: ${BASH_COMMAND}"' ERR

usage() {
    cat <<EOF
Usage: sudo $0 [install|update|reset]

  (no argument)  Auto-detect: installs if absent, updates otherwise.
  install        Force a first-time install into ${APP_DIR}.
  update         Update an existing install.
  reset          Reset the admin account password.

Current APP_DIR: ${APP_DIR}
Overridable variables: APP_USER, APP_DIR, SERVICE_NAME, DEFAULT_PORT.
TUNOOVA_NO_STATS=1 disables the anonymous install/update counter.
EOF
}

require_root() {
    if [[ ${EUID} -ne 0 ]]; then
        error "This script must be run as root (sudo)."
        exit 1
    fi
}

# Checks that the script is indeed being run from a clone of the Tunoova
# repository (not an empty/unrelated folder) before touching anything.
validate_repo() {
    if [[ ! -f "${APP_DIR}/run.py" || ! -f "${APP_DIR}/requirements.txt" || ! -d "${APP_DIR}/app" ]]; then
        error "${APP_DIR} does not look like a clone of the Tunoova repository (run.py/requirements.txt/app missing)."
        error "Clone the repository first, then run this script from its root: git clone <url> && cd <folder> && sudo ./install.sh"
        exit 1
    fi
    if [[ ! -d "${APP_DIR}/.git" ]]; then
        error "${APP_DIR} is not a git repository (no .git folder). Updates via 'git pull' will not work."
        error "Use a clone obtained with 'git clone', not a downloaded archive."
        exit 1
    fi
}

is_installed() {
    # Ne pas faire "systemctl ... | grep -q ...": sous `pipefail` (active plus
    # haut), grep -q ferme le pipe des qu'il trouve sa correspondance, ce qui
    # peut faire mourir systemctl par SIGPIPE (code 141) avant qu'il ait fini
    # d'ecrire - pipefail remonte alors ce 141 comme echec du pipeline entier,
    # meme si le service existe bien. On capture donc la sortie dans une
    # variable au lieu de la streamer vers un second processus.
    local matches
    matches="$(systemctl list-unit-files --no-legend "${SERVICE_NAME}.service" 2>/dev/null || true)"
    [[ -n "$matches" ]]
}

# --- Rules mirrored from app/admin/password_policy.py ----------------------
is_strong_password() {
    local pw="$1"
    [[ ${#pw} -ge 8 ]] || return 1
    [[ "$pw" =~ [A-Z] ]] || return 1
    [[ "$pw" =~ [a-z] ]] || return 1
    [[ "$pw" =~ [0-9] ]] || return 1
    [[ "$pw" =~ [^A-Za-z0-9] ]] || return 1
    return 0
}

hash_password() {
    # Reads the password from stdin (avoids exposing it via `ps`/shell history).
    "${VENV_DIR}/bin/python" -c '
import bcrypt, sys
pw = sys.stdin.readline().rstrip("\n").encode()
print(bcrypt.hashpw(pw, bcrypt.gensalt()).decode())
'
}

prompt_password() {
    # $1 = label to display. Prints the validated password to stdout - this
    # is intentional: callers use `pw=$(prompt_password "...")` to capture
    # it. Because of that, everything meant to be SEEN by the user (prompts,
    # warnings) must go to stderr instead of stdout: stdout is exactly what
    # `$(...)` swallows into the variable, so anything printed there inside
    # this function is invisible on the terminal, not merely reordered.
    local label="$1" pw pw2
    while true; do
        printf '%s: ' "$label" >&2
        IFS= read -rs pw
        printf '\n' >&2
        printf 'Confirm password: ' >&2
        IFS= read -rs pw2
        printf '\n' >&2
        if [[ "$pw" != "$pw2" ]]; then
            warn "Passwords do not match, try again." >&2
            continue
        fi
        if ! is_strong_password "$pw"; then
            warn "Password too weak: min. 8 characters, at least 1 uppercase letter, 1 lowercase letter, 1 digit and 1 special character." >&2
            continue
        fi
        break
    done
    printf '%s' "$pw"
}

# --- Shared steps ------------------------------------------------------

install_system_packages() {
    if ! command -v apt-get >/dev/null 2>&1; then
        warn "apt-get not found: manually verify that python3, python3-venv, python3-pip, git, curl, zip, unzip, ffmpeg and openssh-client are installed."
        return
    fi
    info "Checking system packages (python3, venv, pip, git, curl, zip, unzip, ffmpeg, openssh-client)..."
    apt-get update -qq
    # ffmpeg : remultiplexage a la volee des flux audio YouTube Music vers du
    # Cast (Bose SoundTouch, cf. app/media.py::_relay_youtube_via_ffmpeg) - ces
    # flux sont servis par YouTube en MP4 fragmente/DASH, illisible par le
    # renderer DLNA embarque d'un Bose (contrairement au MP3/AAC classique).
    # openssh-client : acces au depot GitHub prive par deploy key (bouton Update).
    apt-get install -y -qq python3 python3-venv python3-pip git curl zip unzip ffmpeg openssh-client >/dev/null
}

# deno : runtime JavaScript attendu par yt-dlp pour resoudre les challenges
# YouTube (EJS). Sans lui, yt-dlp se rabat sur un client de secours dont les
# URLs googlevideo sont revoquees avant leur expiration (403 en lecture,
# cf. app/media.py::_relay_ranged). Installe dans /usr/local/bin, deja dans
# le PATH du service. Non bloquant : la lecture fonctionne (moins bien) sans.
install_deno() {
    if command -v deno >/dev/null 2>&1; then
        info "Updating deno (JavaScript runtime for yt-dlp)..."
        deno upgrade -q >/dev/null 2>&1 || warn "deno upgrade failed: keeping $(deno --version | head -1)."
        return
    fi
    info "Installing deno (JavaScript runtime for yt-dlp)..."
    if curl -fsSL https://deno.land/install.sh | DENO_INSTALL=/usr/local sh -s -- -y >/dev/null 2>&1; then
        success "deno installed ($(/usr/local/bin/deno --version | head -1))."
    else
        warn "deno installation failed: YouTube Music playback may be unreliable. Install it manually: https://docs.deno.com/runtime/getting_started/installation/"
    fi
}

create_system_user() {
    # Explicitly create the group: depending on the distro/config
    # (USERGROUPS_ENAB in /etc/login.defs), "useradd" alone does not always
    # create a group with the same name, which would make the later
    # "chown APP_USER:APP_GROUP" fail.
    if ! getent group "${APP_GROUP}" >/dev/null 2>&1; then
        info "Creating system group '${APP_GROUP}'..."
        groupadd --system "${APP_GROUP}"
    fi

    if id -u "${APP_USER}" >/dev/null 2>&1; then
        info "System account '${APP_USER}' already present."
    else
        info "Creating system account '${APP_USER}'..."
        # Home dir = the existing clone (APP_DIR), no --create-home: the
        # folder already exists (it's the manually cloned repository).
        useradd --system --gid "${APP_GROUP}" --home-dir "${APP_DIR}" --shell /usr/sbin/nologin "${APP_USER}"
    fi
}

# Runs a git command against APP_DIR while bypassing git's "dubious
# ownership" protection (the folder's owner may be root on first run, then
# ${APP_USER} after fix_permissions - regardless of who runs this script, we
# explicitly allow it for this call only, without touching the global git
# config).
git_app() {
    git -C "${APP_DIR}" -c "safe.directory=${APP_DIR}" "$@"
}

create_directories() {
    info "Creating data folders (db/backups, db/profile_photos, mp3)..."
    # db/backups, db/profile_photos and mp3/ are intentionally excluded from the git repo (.gitignore).
    mkdir -p "${APP_DIR}/db/backups" "${APP_DIR}/db/profile_photos" "${APP_DIR}/mp3"
}

create_venv() {
    if [[ -d "${VENV_DIR}" ]]; then
        info "Python virtual environment already present."
        return
    fi
    info "Creating the Python virtual environment..."
    sudo -u "${APP_USER}" "${PYTHON_BIN}" -m venv "${VENV_DIR}"
}

install_python_requirements() {
    info "Installing Python dependencies (requirements.txt)..."
    sudo -u "${APP_USER}" "${VENV_DIR}/bin/pip" install --upgrade pip --quiet
    sudo -u "${APP_USER}" "${VENV_DIR}/bin/pip" install -r "${APP_DIR}/requirements.txt"
}

setup_initial_env() {
    if [[ -f "${APP_DIR}/.env" ]]; then
        info ".env already present, left as-is."
        return
    fi
    info "Creating .env from .env.example..."
    cp "${APP_DIR}/.env.example" "${APP_DIR}/.env"
    local secret
    secret=$("${VENV_DIR}/bin/python" -c "import secrets; print(secrets.token_hex(32))")
    sed -i "s#^SESSION_SECRET_KEY=.*#SESSION_SECRET_KEY=${secret}#" "${APP_DIR}/.env"
    chown "${APP_USER}:${APP_GROUP}" "${APP_DIR}/.env"
    chmod 600 "${APP_DIR}/.env"
}

# run.py runs db.create_all() + ensure_schema_upgrades() + seed_reference_data()
# at module level (outside the __main__ block): a plain `import run` is
# therefore enough to create/update the database structure without starting
# the server.
init_database() {
    info "Initializing the database (tables + reference data)..."
    ( cd "${APP_DIR}" && sudo -u "${APP_USER}" "${VENV_DIR}/bin/python" -c "import run" )
    success "Database ready (${APP_DIR}/db/tunoova.db)."
}

# Adds to the existing .env any key present in .env.example but missing from
# the current .env (new parameters introduced by an update). Keys that are
# already defined are never touched.
merge_env_file() {
    local example="${APP_DIR}/.env.example" target="${APP_DIR}/.env"
    local added=()
    # Ecrit en root dans un dossier appartenant a APP_USER : refuser un lien
    # symbolique (sinon ecriture dans n'importe quel fichier systeme).
    if [[ -L "$target" ]]; then
        error "${target} is a symbolic link, refusing to modify it."
        exit 1
    fi
    local key
    while IFS= read -r line; do
        # "[[ cond ]] && continue" would abort the script under "set -e"
        # whenever cond is false (the common case here): if/continue avoids the pitfall.
        if [[ "$line" =~ ^[[:space:]]*# ]]; then continue; fi
        if [[ "$line" =~ ^[[:space:]]*$ ]]; then continue; fi
        if [[ "$line" != *"="* ]]; then continue; fi
        key="${line%%=*}"
        if ! grep -q "^${key}=" "$target"; then
            echo "$line" >> "$target"
            added+=("$key")
        fi
    done < "$example"
    if [[ ${#added[@]} -gt 0 ]]; then
        warn "New parameters added to .env (check/fill in their value): ${added[*]}"
    else
        info "No new .env parameter to add."
    fi
    # Jamais de mode debug en production : le debogueur Werkzeug offre une
    # console Python dans le navigateur, et les anciennes installations
    # heritaient de FLASK_DEBUG=1 de .env.example.
    if grep -qE '^FLASK_DEBUG=[^0]' "$target"; then
        sed -i -E 's/^FLASK_DEBUG=.*/FLASK_DEBUG=0/' "$target"
        warn "FLASK_DEBUG forced to 0 in .env (debug mode must never run in production)."
    fi
}

# Runs as APP_USER (not root): db/ belongs to the application, a symbolic
# link planted there must not make root copy a system file into the backups.
backup_database() {
    local stamp
    stamp=$(date +%Y%m%d_%H%M%S)
    sudo -u "${APP_USER}" mkdir -p "${APP_DIR}/db/backups"

    local db_file="${APP_DIR}/db/tunoova.db"
    if [[ -f "$db_file" ]]; then
        local backup_file="${APP_DIR}/db/backups/tunoova_pre_update_${stamp}.db"
        sudo -u "${APP_USER}" cp "$db_file" "$backup_file"
        info "Database backed up before update: ${backup_file}"
    fi

    local photos_dir="${APP_DIR}/db/profile_photos"
    if [[ -d "$photos_dir" ]] && [[ -n "$(ls -A "$photos_dir" 2>/dev/null)" ]]; then
        local photos_archive="${APP_DIR}/db/backups/tunoova_photos_pre_update_${stamp}.zip"
        sudo -u "${APP_USER}" bash -c 'cd "$1" && zip -qr "$2" .' _ "$photos_dir" "$photos_archive"
        info "Profile photos backed up before update: ${photos_archive}"
    fi
}

fix_permissions() {
    info "Applying permissions (${APP_USER}:${APP_GROUP}) on ${APP_DIR}..."
    chown -R "${APP_USER}:${APP_GROUP}" "${APP_DIR}"
    chmod 750 "${APP_DIR}"
    # "[[ ... ]] && cmd" would return a non-zero status (aborting the script
    # under "set -e") when .env does not exist yet (1st call, before
    # setup_initial_env): if/then avoids the pitfall.
    if [[ -f "${APP_DIR}/.env" && ! -L "${APP_DIR}/.env" ]]; then
        chmod 600 "${APP_DIR}/.env"
    fi
}

write_systemd_unit() {
    local port="$1"
    info "Creating the systemd service '${SERVICE_NAME}'..."
    cat > "/etc/systemd/system/${SERVICE_NAME}.service" <<EOF
[Unit]
Description=Tunoova - music / podcast / radio application
After=network.target

[Service]
Type=simple
User=${APP_USER}
Group=${APP_GROUP}
WorkingDirectory=${APP_DIR}
# PATH complet (pas seulement le venv) : necessaire pour que le process
# puisse trouver des binaires systeme appeles en sous-processus, ex. ffmpeg
# (app/media.py::_relay_youtube_via_ffmpeg, Cast YouTube Music).
Environment=PATH=${VENV_DIR}/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
ExecStart=${VENV_DIR}/bin/python run.py -p ${port}
Restart=on-failure
RestartSec=5
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
EOF
    systemctl daemon-reload
}

get_port() {
    # Le port finit dans le fichier systemd ecrit en root : .tunoova_port
    # appartient a APP_USER, on n'accepte donc qu'un nombre.
    local port=""
    if [[ -f "${PORT_FILE}" ]]; then
        port=$(head -n1 "${PORT_FILE}" | tr -d '[:space:]')
    else
        local unit="/etc/systemd/system/${SERVICE_NAME}.service"
        if [[ -f "$unit" ]]; then
            port=$(grep -oP '(?<=-p )\d+' "$unit" 2>/dev/null | head -n1 || true)
        fi
    fi
    if [[ ! "$port" =~ ^[0-9]{1,5}$ ]]; then
        port="${DEFAULT_PORT}"
    fi
    echo "$port"
}

verify_running() {
    local port="$1" tries=0
    sleep 2
    if ! systemctl is-active --quiet "${SERVICE_NAME}"; then
        error "The ${SERVICE_NAME} service did not start. Details: journalctl -u ${SERVICE_NAME} -n 50 --no-pager"
        exit 1
    fi
    until curl -fsS -o /dev/null "http://127.0.0.1:${port}/" 2>/dev/null; do
        tries=$((tries + 1))
        if [[ $tries -ge 15 ]]; then
            error "The service is running but not responding on port ${port}. Details: journalctl -u ${SERVICE_NAME} -n 50 --no-pager"
            exit 1
        fi
        sleep 1
    done
    local ip
    ip=$(hostname -I 2>/dev/null | awk '{print $1}')
    if [[ -z "${ip}" ]]; then
        ip="127.0.0.1"
    fi
    success "Tunoova is up and reachable at: http://${ip}:${port}/ -- open http://${ip}:${port}/admin to start configuration."
}

# --- Admin "Update" button --------------------------------------------------

# "owner/repo" from a GitHub URL (https or ssh form).
repo_slug() {
    local url="$1"
    url="${url%.git}"
    url="${url#https://github.com/}"
    url="${url#ssh://git@github.com/}"
    url="${url#git@github.com:}"
    echo "$url"
}

# Value of a key of ${UPDATE_CONF} (read as text, never sourced).
conf_value() {
    sed -n "s/^$1=//p" "${UPDATE_CONF}" 2>/dev/null | head -n1
}

git_ssh_command() {
    echo "ssh -i ${DEPLOY_KEY} -o IdentitiesOnly=yes -o UserKnownHostsFile=${KNOWN_HOSTS} -o StrictHostKeyChecking=yes -o BatchMode=yes"
}

# public / private / unknown (network error). The GitHub API answers 404 for
# a private repository when called without credentials.
github_visibility() {
    local code
    code=$(curl -s -o /dev/null -w '%{http_code}' --max-time 15 "https://api.github.com/repos/$1" || true)
    case "$code" in
        200) echo public ;;
        404) echo private ;;
        *)   echo unknown ;;
    esac
}

deploy_key_works() {
    GIT_SSH_COMMAND="$(git_ssh_command)" GIT_TERMINAL_PROMPT=0 \
        git ls-remote "git@github.com:$1.git" HEAD >/dev/null 2>&1
}

# Generates the deploy key if needed and waits until it has been added on
# GitHub. Without a terminal (run by the update service), never prompts.
ensure_deploy_key() {
    local slug="$1" answer
    if [[ ! -f "${DEPLOY_KEY}" ]]; then
        info "Generating the deploy key ${DEPLOY_KEY}..."
        ssh-keygen -q -t ed25519 -N "" -C "${APP_NAME}@$(hostname)" -f "${DEPLOY_KEY}"
    fi
    # ssh exige une cle privee lisible par son seul proprietaire (0600) :
    # une copie pour root (mise a jour), une pour APP_USER (l'appli s'en sert
    # pour verifier s'il existe une nouvelle version, lecture seule cote GitHub).
    chown root:root "${DEPLOY_KEY}" "${DEPLOY_KEY}.pub"
    chmod 600 "${DEPLOY_KEY}"
    chmod 644 "${DEPLOY_KEY}.pub"
    install -m 600 -o "${APP_USER}" -g "${APP_GROUP}" "${DEPLOY_KEY}" "${APP_DEPLOY_KEY}"
    echo "${GITHUB_HOST_KEY}" > "${KNOWN_HOSTS}"
    chmod 644 "${KNOWN_HOSTS}"

    local first_try=1
    while ! deploy_key_works "$slug"; do
        if [[ ! -t 0 ]]; then
            error "The deploy key is not authorized on https://github.com/${slug} (Settings > Deploy keys)."
            return 1
        fi
        echo
        if [[ $first_try -eq 0 ]]; then
            # Affiche la vraie cause (cle differente, host key...) au lieu de
            # simplement redemander la cle.
            warn "GitHub still refuses the key. SSH answer:"
            ssh -i "${DEPLOY_KEY}" -o IdentitiesOnly=yes -o UserKnownHostsFile="${KNOWN_HOSTS}" \
                -o StrictHostKeyChecking=yes -o BatchMode=yes -T git@github.com 2>&1 | sed 's/^/    /' || true
            warn "Key fingerprint on this machine (must match the one shown by GitHub under Deploy keys):"
            echo "    $(ssh-keygen -lf "${DEPLOY_KEY}.pub")"
            echo
        fi
        first_try=0
        warn "The GitHub repository ${slug} is private. Add this READ-ONLY deploy key to it:"
        echo "  1. Open https://github.com/${slug}/settings/keys and click 'Add deploy key'"
        echo "  2. Title: ${APP_NAME}@$(hostname) - leave 'Allow write access' UNCHECKED"
        echo "  3. Key:"
        echo
        cat "${DEPLOY_KEY}.pub"
        echo
        read -rp "Press Enter once the key is added (or type 'skip' to continue without the Update button): " answer
        if [[ "$answer" == "skip" ]]; then
            return 1
        fi
    done
    success "Deploy key authorized on ${slug}."
}

# Writes ${UPDATE_CONF} (repository URL/branch, root-owned) after checking
# whether the repository is public (anonymous https) or private (deploy key).
# Re-checked on every install/update: a repository made public switches to
# https by itself.
setup_update_access() {
    mkdir -p "${CONF_DIR}"
    chmod 755 "${CONF_DIR}"

    local slug branch url="" previous_url=""
    if [[ -f "${UPDATE_CONF}" ]]; then
        slug=$(conf_value REPO_SLUG)
        branch=$(conf_value REPO_BRANCH)
        previous_url=$(conf_value REPO_URL)
    else
        slug=$(repo_slug "$(git_app remote get-url origin)")
        branch=$(git_app rev-parse --abbrev-ref HEAD)
    fi
    if [[ ! "$slug" =~ ^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$ || ! "$branch" =~ ^[A-Za-z0-9_./-]+$ ]]; then
        warn "Unsupported repository (${slug}, branch ${branch}): the Update button needs a GitHub clone."
        return 1
    fi

    case "$(github_visibility "$slug")" in
        public)
            info "GitHub repository ${slug} is public: anonymous access."
            url="https://github.com/${slug}.git"
            ;;
        private)
            info "GitHub repository ${slug} is private: deploy key required."
            ensure_deploy_key "$slug" || return 1
            url="git@github.com:${slug}.git"
            ;;
        *)
            if [[ -z "${previous_url}" ]]; then
                warn "GitHub unreachable: the Update button will be set up on the next update."
                return 1
            fi
            url="${previous_url}"
            ;;
    esac

    cat > "${UPDATE_CONF}.tmp" <<EOF
# Generated by install.sh - read by ${UPDATER_BIN} (root) and by the application.
REPO_SLUG=${slug}
REPO_BRANCH=${branch}
REPO_URL=${url}
APP_DIR=${APP_DIR}
APP_USER=${APP_USER}
APP_GROUP=${APP_GROUP}
SERVICE_NAME=${SERVICE_NAME}
EOF
    chmod 644 "${UPDATE_CONF}.tmp"
    mv "${UPDATE_CONF}.tmp" "${UPDATE_CONF}"
}

# Root-owned clone of the repository: the code run as root by the updater
# comes from here (fetched from GitHub by root), never from APP_DIR.
update_mirror() {
    # Chaque commande est suivie de "|| return 1" : appelee dans un "if",
    # "set -e" ne s'applique pas et un fetch rate passerait inapercu.
    local repo_url repo_branch repo_slug
    repo_url=$(conf_value REPO_URL)
    repo_branch=$(conf_value REPO_BRANCH)
    repo_slug=$(conf_value REPO_SLUG)
    if [[ -z "$repo_url" || -z "$repo_branch" ]]; then
        echo "[error] ${UPDATE_CONF} missing or incomplete." >&2
        return 1
    fi
    mkdir -p "${STATE_DIR}" || return 1
    chown "root:${APP_GROUP}" "${STATE_DIR}" || return 1
    chmod 755 "${STATE_DIR}" || return 1
    export GIT_SSH_COMMAND GIT_TERMINAL_PROMPT=0
    GIT_SSH_COMMAND="$(git_ssh_command)"
    info "Fetching ${repo_slug} (${repo_branch}) into ${MIRROR_DIR}..."
    if [[ ! -d "${MIRROR_DIR}/.git" ]]; then
        rm -rf "${MIRROR_DIR}" || return 1
        git clone --quiet --branch "$repo_branch" "$repo_url" "${MIRROR_DIR}" || return 1
    else
        git -C "${MIRROR_DIR}" remote set-url origin "$repo_url" || return 1
        git -C "${MIRROR_DIR}" fetch --quiet origin "$repo_branch" || return 1
        git -C "${MIRROR_DIR}" reset --quiet --hard FETCH_HEAD || return 1
        git -C "${MIRROR_DIR}" clean -qfdx || return 1
    fi
}

# APP_DIR takes the mirror's code. git runs as APP_USER: its hooks and
# config (.git belongs to the application) are never executed as root.
sync_app_from_mirror() {
    info "Updating ${APP_DIR} from ${MIRROR_DIR}..."
    # Le miroir appartient a root : git le refuse ("dubious ownership") sauf
    # exception safe.directory. Pour un depot local, l'exception doit etre
    # passee au process upload-pack lui-meme (git ne lui transmet pas les -c).
    local upload_pack="git -c safe.directory=${MIRROR_DIR} -c safe.directory=${MIRROR_DIR}/.git upload-pack"
    sudo -u "${APP_USER}" git -C "${APP_DIR}" fetch --quiet --upload-pack="${upload_pack}" "${MIRROR_DIR}" HEAD || return 1
    sudo -u "${APP_USER}" git -C "${APP_DIR}" reset --quiet --hard FETCH_HEAD || return 1
}

write_updater() {
    # Script autonome (fonctions copiees depuis ce fichier) : il doit pouvoir
    # rafraichir le miroir AVANT de lancer le install.sh qui s'y trouve.
    cat > "${UPDATER_BIN}.tmp" <<EOF
#!/usr/bin/env bash
# Generated by install.sh - run as root by ${UPDATE_SERVICE}.service when the
# application creates ${REQUEST_FILE} (Admin > Update button).
set -Eeuo pipefail
APP_GROUP="${APP_GROUP}"
UPDATE_CONF="${UPDATE_CONF}"
DEPLOY_KEY="${DEPLOY_KEY}"
KNOWN_HOSTS="${KNOWN_HOSTS}"
STATE_DIR="${STATE_DIR}"
MIRROR_DIR="${MIRROR_DIR}"
REQUEST_FILE="${REQUEST_FILE}"
STATUS_FILE="${STATUS_FILE}"
UPDATE_LOG="${UPDATE_LOG}"
info() { echo "[info] \$*"; }
$(declare -f conf_value git_ssh_command update_mirror)

set_status() { printf '%s %s\n' "\$1" "\$(date -Iseconds)" > "\${STATUS_FILE}.tmp" && mv "\${STATUS_FILE}.tmp" "\${STATUS_FILE}"; }

APP_DIR=\$(conf_value APP_DIR)
rm -f "\${REQUEST_FILE}"
exec > "\${UPDATE_LOG}" 2>&1
set_status running
if update_mirror && APP_DIR="\${APP_DIR}" TUNOOVA_FROM_MIRROR=1 bash "\${MIRROR_DIR}/install.sh" update; then
    set_status success
else
    set_status failed
    exit 1
fi
EOF
    chmod 750 "${UPDATER_BIN}.tmp"
    # mv (nouvel inode) : ce script peut etre en cours d'execution, bash le
    # lit au fil de l'eau.
    mv "${UPDATER_BIN}.tmp" "${UPDATER_BIN}"
}

install_update_service() {
    info "Setting up the admin Update button (${UPDATE_SERVICE})..."
    mkdir -p "${REQUEST_DIR}"
    chown "${APP_USER}:${APP_GROUP}" "${REQUEST_DIR}"
    chmod 750 "${REQUEST_DIR}"
    touch "${UPDATE_LOG}"
    chown "root:${APP_GROUP}" "${UPDATE_LOG}"
    chmod 640 "${UPDATE_LOG}"

    write_updater

    cat > "/etc/systemd/system/${UPDATE_SERVICE}.service" <<EOF
[Unit]
Description=Tunoova - application update (admin Update button)
After=network-online.target

[Service]
Type=oneshot
ExecStart=${UPDATER_BIN}
TimeoutStartSec=30min
EOF
    cat > "/etc/systemd/system/${UPDATE_SERVICE}.path" <<EOF
[Unit]
Description=Tunoova - watch for update requests from the admin space

[Path]
PathExists=${REQUEST_FILE}
Unit=${UPDATE_SERVICE}.service

[Install]
WantedBy=multi-user.target
EOF
    systemctl daemon-reload
    systemctl --quiet enable --now "${UPDATE_SERVICE}.path"
}

# Install: sets up the Update button if GitHub access works (never blocking).
setup_update_button() {
    if setup_update_access && update_mirror; then
        install_update_service
    else
        warn "Admin Update button not set up. Run 'sudo $0 update' later to retry."
    fi
}

# --- Tailscale (Admin > Network) -------------------------------------------

# Installs the Tailscale client (official script, needs root) and lets APP_USER
# drive it without sudo (tailscale up/serve/funnel from Admin > Network).
# Nothing is exposed until the server is signed in to a Tailscale account and
# a mode is chosen on that page. An existing Tailscale is left as-is (never
# restarted if it was stopped on purpose), except to switch it to userspace
# networking when the machine has no TUN device.
setup_tailscale() {
    local fresh=0
    if ! command -v tailscale >/dev/null 2>&1; then
        info "Installing the Tailscale client (Admin > Network)..."
        curl -fsSL https://tailscale.com/install.sh | sh || return 1
        fresh=1
    fi
    if use_tailscale_userspace; then
        systemctl --quiet enable tailscaled
        systemctl restart tailscaled || return 1
    elif [[ $fresh -eq 1 ]]; then
        systemctl --quiet enable --now tailscaled || return 1
    fi
    allow_tailscale_operator
}

# Sans /dev/net/tun (conteneur LXC/Proxmox, certains VPS), tailscaled ne
# demarre pas en mode noyau. Le mode userspace suffit a Tunoova : serve et
# funnel relaient vers 127.0.0.1. Retourne 0 si la config vient d'etre changee.
TAILSCALED_DEFAULTS="/etc/default/tailscaled"
use_tailscale_userspace() {
    if [[ -c /dev/net/tun ]] || grep -q -- "--tun=userspace-networking" "${TAILSCALED_DEFAULTS}" 2>/dev/null; then
        return 1
    fi
    warn "/dev/net/tun not available (container?): Tailscale switched to userspace networking mode."
    if grep -q '^FLAGS=' "${TAILSCALED_DEFAULTS}" 2>/dev/null; then
        sed -i 's/^FLAGS="\(.*\)"/FLAGS="\1 --tun=userspace-networking"/' "${TAILSCALED_DEFAULTS}"
    else
        echo 'FLAGS="--tun=userspace-networking"' >> "${TAILSCALED_DEFAULTS}"
    fi
    return 0
}

allow_tailscale_operator() {
    if systemctl is-failed --quiet tailscaled; then
        warn "The tailscaled service failed to start. Details: journalctl -u tailscaled -n 30 --no-pager"
        return 0
    fi
    if ! systemctl is-active --quiet tailscaled; then
        return 0
    fi
    # Juste apres l'installation, tailscaled est "active" pour systemd avant
    # d'avoir cree son socket : on reessaie quelques secondes.
    local tries=0 output
    until output=$(tailscale set --operator="${APP_USER}" 2>&1); do
        tries=$((tries + 1))
        if systemctl is-failed --quiet tailscaled; then
            warn "The tailscaled service failed to start. Details: journalctl -u tailscaled -n 30 --no-pager"
            return 0
        fi
        if [[ $tries -ge 15 ]]; then
            warn "Could not set the Tailscale operator: ${output}"
            warn "Run: sudo tailscale set --operator=${APP_USER}"
            return 0
        fi
        sleep 1
    done
    info "Tailscale operator set to '${APP_USER}'. Sign in to Tailscale from Admin > Network (no need to run 'tailscale up')."
}

# --- Install/update counter (project website) ------------------------------

# Reads a string constant from app/version.py (KEY = "value").
version_value() {
    grep -oP "^${1}\s*=\s*\"\K[^\"]+" "${APP_DIR}/app/version.py" 2>/dev/null | head -n1 || true
}

# Sends the event (install|update) and the version to APP_STATS_URL. Best
# effort: silent, 5 s max, never fails the install/update. Opt-out with
# TUNOOVA_NO_STATS=1.
report_event() {
    local event="$1" url version
    if [[ "${TUNOOVA_NO_STATS:-0}" == "1" ]]; then
        return 0
    fi
    url=$(version_value APP_STATS_URL)
    version=$(version_value APP_VERSION)
    if [[ ! "$url" =~ ^https:// || ! "$version" =~ ^[0-9A-Za-z._-]+$ ]]; then
        return 0
    fi
    curl -fsS -m 5 -o /dev/null -X POST "${url}?event=${event}&version=${version}" 2>/dev/null || true
}

# --- Main commands --------------------------------------------

do_install() {
    if is_installed; then
        error "An installation already exists in ${APP_DIR}. Use: sudo $0 update"
        exit 1
    fi
    info "=== Installing Tunoova (from ${APP_DIR}) ==="
    install_system_packages
    install_deno
    create_system_user
    create_directories

    # APP_DIR still belongs to whoever did the manual "git clone" (root in
    # general): without this chown, the following steps (run as "sudo -u
    # ${APP_USER}") do not have permission to write into it (e.g. creating
    # the venv). fix_permissions is called again further below to reclaim
    # files written by root in the meantime (.env in particular).
    fix_permissions

    create_venv
    install_python_requirements
    setup_initial_env
    init_database

    echo
    local email
    while true; do
        read -rp "Admin account email: " email
        # "[[ cond ]] && break" would abort the script under "set -e"
        # whenever cond is false (the normal case here, as long as the email is invalid):
        # if/break avoids the pitfall.
        if [[ "$email" =~ ^[^[:space:]@]+@[^[:space:]@]+\.[^[:space:]]+$ ]]; then
            break
        fi
        warn "Invalid email address, try again."
    done
    local pw hash
    pw=$(prompt_password "Admin password")
    hash=$(printf '%s' "$pw" | hash_password)
    unset pw
    sed -i "s#^ADMIN_EMAIL=.*#ADMIN_EMAIL=${email}#" "${APP_DIR}/.env"
    sed -i "s#^ADMIN_PASSWORD_HASH=.*#ADMIN_PASSWORD_HASH=${hash}#" "${APP_DIR}/.env"

    fix_permissions

    echo
    local port
    read -rp "Application listen port [${DEFAULT_PORT}]: " port
    port="${port:-${DEFAULT_PORT}}"
    echo "${port}" > "${PORT_FILE}"
    chown "${APP_USER}:${APP_GROUP}" "${PORT_FILE}"

    write_systemd_unit "${port}"
    setup_update_button
    setup_tailscale || warn "Tailscale setup failed (Admin > Network). Retry with: sudo $0 update"
    systemctl --quiet enable "${SERVICE_NAME}"
    systemctl restart "${SERVICE_NAME}"
    verify_running "${port}"
    report_event install
    success "Installation complete."
}

do_update() {
    if ! is_installed; then
        error "No existing installation found (service ${SERVICE_NAME} absent). Use: sudo $0 install"
        exit 1
    fi
    info "=== Updating Tunoova (${APP_DIR}) ==="

    info "Stopping the service..."
    systemctl stop "${SERVICE_NAME}" || true

    # Rattrape une nouvelle dependance systeme introduite depuis la derniere
    # installation/mise a jour (ex. ffmpeg pour le Cast YouTube Music,
    # app/media.py::_relay_youtube_via_ffmpeg) - sans ca, seul un "install"
    # sur une machine neuve l'aurait installee.
    install_system_packages
    install_deno

    backup_database

    # Lance par le service Update : le miroir vient d'etre rafraichi par
    # ${UPDATER_BIN}. Lance a la main : on (re)configure l'acces GitHub (peut
    # demander la deploy key) puis on rafraichit le miroir.
    local update_button=0
    if [[ "${TUNOOVA_FROM_MIRROR:-0}" == "1" ]]; then
        setup_update_access || true
        update_button=1
    elif setup_update_access && update_mirror; then
        update_button=1
    fi

    if [[ $update_button -eq 1 ]]; then
        sync_app_from_mirror
    else
        warn "GitHub access for the Update button unavailable: falling back to 'git pull' with this clone's own credentials."
        local current_branch
        current_branch=$(git_app rev-parse --abbrev-ref HEAD)
        info "Fetching source code (git pull on branch ${current_branch})..."
        git_app fetch origin
        git_app reset --hard "origin/${current_branch}"
    fi

    create_directories
    install_python_requirements

    info "Checking .env parameters..."
    merge_env_file

    # Database structure: run.py calls db.create_all() (creates any missing
    # tables) then ensure_schema_upgrades() (adds columns introduced since -
    # see db/seed.py) on every startup. Additive changes are therefore
    # applied automatically below, after the safety backup made above. A
    # non-additive structural change (renamed/removed column, data
    # migration) must be explicitly coded into
    # db/seed.py::ensure_schema_upgrades() by a developer BEFORE running
    # this update.
    init_database

    fix_permissions

    local port
    port=$(get_port)
    # Regenere aussi le fichier de service a chaque update (idempotent) : sans
    # ca, une installation existante ne recevait jamais les changements
    # apportes au template systemd lui-meme (ex. le PATH complet ajoute pour
    # ffmpeg ci-dessus) - seule une reinstallation neuve en beneficiait.
    write_systemd_unit "${port}"
    if [[ $update_button -eq 1 ]]; then
        install_update_service
    fi
    setup_tailscale || warn "Tailscale setup failed (Admin > Network). Retry with: sudo $0 update"
    systemctl --quiet enable "${SERVICE_NAME}"
    systemctl start "${SERVICE_NAME}"
    verify_running "${port}"
    report_event update
    success "Update complete."
}

do_reset() {
    if [[ ! -f "${APP_DIR}/.env" ]]; then
        error ".env file not found in ${APP_DIR}. Is the application installed?"
        exit 1
    fi
    info "=== Resetting the admin password ==="
    local current_email
    current_email=$(grep -E '^ADMIN_EMAIL=' "${APP_DIR}/.env" | cut -d= -f2- || true)
    info "Admin account concerned: ${current_email:-unknown}"

    local pw hash
    pw=$(prompt_password "New admin password")
    hash=$(printf '%s' "$pw" | hash_password)
    unset pw
    sed -i "s#^ADMIN_PASSWORD_HASH=.*#ADMIN_PASSWORD_HASH=${hash}#" "${APP_DIR}/.env"

    local port
    port=$(get_port)
    systemctl restart "${SERVICE_NAME}"
    verify_running "${port}"
    success "Admin password reset, the application has restarted with the change applied."
}

# --- Script entry point ----------------------------------------------------

main() {
    local action="${1:-auto}"
    case "$action" in
        -h|--help|help) usage; exit 0 ;;
    esac
    require_root
    validate_repo
    case "$action" in
        auto)
            if is_installed; then do_update; else do_install; fi
            ;;
        install) do_install ;;
        update)  do_update ;;
        reset)   do_reset ;;
        *)
            error "Unknown argument: ${action}"
            usage
            exit 1
            ;;
    esac
}

main "$@"
