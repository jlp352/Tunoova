#!/usr/bin/env bash
# Audit de securite de Tunoova (cf. Doc/Audit Tunoova.md) : enchaine les
# controles et agrege les rapports dans scripts/audit-reports/<date>/.
#
#   1. pip-audit   : vulnerabilites connues (requirements.txt + .venv)
#   2. bandit      : analyse statique Python
#   3. semgrep     : analyse statique (regles flask, python, security-audit, js)
#   4. gitleaks    : secrets dans l'historique Git et les changements en cours
#   5. flask-unsign: la SESSION_SECRET_KEY resiste-t-elle a un dictionnaire ?
#   6. OWASP ZAP   : scan passif de l'appli lancee en local (option --zap)
#
# Bandit et semgrep : les resultats deja examines et juges sans risque sont
# listes dans scripts/audit-baseline.txt (cle outil|regle|fichier|ligne de
# code, insensible aux decalages de numeros de ligne). Seuls les NOUVEAUX
# resultats font echouer l'audit. Apres examen d'un nouveau resultat juge
# sans risque : relancer avec --update-baseline.
#
# Usage : scripts/audit.sh [--zap] [--cookie <cookie de prod>] [--update-baseline]
#   --zap              lance aussi le scan ZAP (Java 17+ et ZAP_HOME=<dossier
#                      contenant zap.sh> requis ; appli demarree sur le port 5055)
#   --cookie <valeur>  teste ce cookie "session" (ex. copie depuis la prod)
#                      au lieu d'un cookie genere avec la cle locale
#   --update-baseline  accepte tous les resultats bandit/semgrep actuels
#
# Les outils sont installes a part, dans ~/.cache/tunoova-audit (jamais dans
# le .venv de l'appli). Code de sortie : 0 si tout est OK, 1 sinon.

set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TOOLS="${TUNOOVA_AUDIT_TOOLS:-$HOME/.cache/tunoova-audit}"
REPORTS="${ROOT}/scripts/audit-reports/$(date +%Y%m%d_%H%M%S)"
BASELINE="${ROOT}/scripts/audit-baseline.txt"
APP_PY="${ROOT}/.venv/bin/python"
GITLEAKS_VERSION="8.30.1"
ZAP_PORT=5055

RUN_ZAP=0
UPDATE_BASELINE=0
COOKIE=""
while [[ $# -gt 0 ]]; do
    case "$1" in
        --zap) RUN_ZAP=1 ;;
        --update-baseline) UPDATE_BASELINE=1 ;;
        --cookie) COOKIE="${2:-}"; shift ;;
        -h|--help) sed -n '2,27p' "$0"; exit 0 ;;
        *) echo "Option inconnue : $1" >&2; exit 2 ;;
    esac
    shift
done

declare -a SUMMARY=()
FAILED=0
record() {  # record <etape> <OK|ECHEC|IGNORE> <detail>
    SUMMARY+=("$(printf '%-14s %-7s %s' "$1" "$2" "$3")")
    if [[ "$2" == "ECHEC" ]]; then FAILED=1; fi
}
step() { printf '\n\033[1m== %s\033[0m\n' "$1"; }

mkdir -p "$REPORTS"
cd "$ROOT"

# --- Outils ------------------------------------------------------------------
step "Preparation des outils (${TOOLS})"
if [[ ! -x "${TOOLS}/venv/bin/python" ]]; then
    python3 -m venv "${TOOLS}/venv" || { echo "Creation du venv impossible" >&2; exit 2; }
fi
"${TOOLS}/venv/bin/pip" install -q --upgrade pip pip-audit bandit semgrep "flask-unsign[wordlist]" \
    || { echo "Installation des outils impossible" >&2; exit 2; }
TPY="${TOOLS}/venv/bin/python"

GITLEAKS="${TOOLS}/gitleaks-${GITLEAKS_VERSION}/gitleaks"
if [[ ! -x "$GITLEAKS" ]]; then
    dl="${TOOLS}/gitleaks-${GITLEAKS_VERSION}"
    mkdir -p "$dl"
    base="https://github.com/gitleaks/gitleaks/releases/download/v${GITLEAKS_VERSION}"
    archive="gitleaks_${GITLEAKS_VERSION}_linux_x64.tar.gz"
    if curl -fsSL "${base}/${archive}" -o "${dl}/${archive}" \
        && curl -fsSL "${base}/gitleaks_${GITLEAKS_VERSION}_checksums.txt" -o "${dl}/checksums.txt" \
        && (cd "$dl" && grep " ${archive}\$" checksums.txt | sha256sum -c --quiet -) \
        && tar xzf "${dl}/${archive}" -C "$dl" gitleaks; then
        :
    else
        echo "Telechargement/verification de gitleaks impossible" >&2
        rm -f "${dl}/gitleaks"
    fi
fi

# Filtre bandit/semgrep contre la baseline : affiche les nouveaux resultats,
# ecrit leur nombre sur la derniere ligne.
FILTER="${TOOLS}/baseline_filter.py"
cat > "$FILTER" <<'PYEOF'
import json, os, sys

tool, report, baseline, update = sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4] == "1"
data = json.load(open(report, encoding="utf-8"))

def line_of(path, lineno):
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            return f.read().splitlines()[lineno - 1].strip()
    except (OSError, IndexError):
        return ""

findings = []
if tool == "bandit":
    for r in data["results"]:
        path = os.path.relpath(r["filename"])
        findings.append((f'bandit|{r["test_id"]}|{path}|{line_of(path, r["line_number"])}',
                         f'{r["issue_severity"]:<6} {r["test_id"]} {path}:{r["line_number"]} {r["issue_text"]}'))
else:
    for r in data["results"]:
        rule = r["check_id"].split(".")[-1]
        path = r["path"]
        findings.append((f'semgrep|{rule}|{path}|{line_of(path, r["start"]["line"])}',
                         f'{r["extra"]["severity"]:<7} {rule} {path}:{r["start"]["line"]}'))

known = set()
if os.path.exists(baseline):
    known = {l.rstrip("\n") for l in open(baseline, encoding="utf-8") if l.strip() and not l.startswith("#")}

if update:
    others = {k for k in known if not k.startswith(tool + "|")}
    keys = sorted(others | {k for k, _ in findings})
    with open(baseline, "w", encoding="utf-8") as f:
        f.write("# Resultats bandit/semgrep examines et juges sans risque (scripts/audit.sh).\n")
        f.write("# Format : outil|regle|fichier|ligne de code. Regenerer avec --update-baseline.\n")
        f.write("\n".join(keys) + "\n")
    print(f"{len(findings)} resultat(s) {tool} enregistres dans la baseline")
    print(0)
    sys.exit(0)

new = [label for key, label in findings if key not in known]
for label in new:
    print("  NOUVEAU", label)
print(f"{len(findings)} resultat(s), dont {len(new)} nouveau(x) hors baseline")
print(len(new))
PYEOF

# --- 1. pip-audit ------------------------------------------------------------
step "1. pip-audit (dependances)"
"${TOOLS}/venv/bin/pip-audit" -r requirements.txt 2>&1 | tee "${REPORTS}/pip-audit-requirements.txt" | tail -n 15
rc_req=${PIPESTATUS[0]}
rc_venv=0
if [[ -x "$APP_PY" ]]; then
    site=$("$APP_PY" -c "import sysconfig; print(sysconfig.get_paths()['purelib'])")
    "${TOOLS}/venv/bin/pip-audit" --path "$site" 2>&1 | tee "${REPORTS}/pip-audit-venv.txt" | tail -n 15
    rc_venv=${PIPESTATUS[0]}
fi
if [[ $rc_req -eq 0 && $rc_venv -eq 0 ]]; then
    record "pip-audit" "OK" "aucune vulnerabilite connue"
else
    record "pip-audit" "ECHEC" "vulnerabilites : voir ${REPORTS}/pip-audit-*"
fi

# --- 2. bandit ---------------------------------------------------------------
step "2. bandit (analyse statique Python)"
"${TOOLS}/venv/bin/bandit" -r app radio_plugins db run.py -q -f json -o "${REPORTS}/bandit.json" >/dev/null 2>&1
if [[ -s "${REPORTS}/bandit.json" ]]; then
    out=$("$TPY" "$FILTER" bandit "${REPORTS}/bandit.json" "$BASELINE" "$UPDATE_BASELINE")
    echo "$out" | sed '$d'
    new=$(echo "$out" | tail -n 1)
    if [[ "$new" -eq 0 ]]; then record "bandit" "OK" "$(echo "$out" | tail -n 2 | head -n 1)"
    else record "bandit" "ECHEC" "${new} nouveau(x) resultat(s) a examiner"; fi
else
    record "bandit" "ECHEC" "bandit n'a pas produit de rapport"
fi

# --- 3. semgrep --------------------------------------------------------------
step "3. semgrep (analyse statique, regles communautaires)"
SEMGREP_SEND_METRICS=off "${TOOLS}/venv/bin/semgrep" scan --metrics off -q \
    --config p/flask --config p/python --config p/security-audit --config p/javascript --config p/secrets \
    --exclude .venv --exclude scripts --exclude Doc --exclude mp3 --exclude db/backups \
    --json -o "${REPORTS}/semgrep.json" . >/dev/null 2>&1
if [[ -s "${REPORTS}/semgrep.json" ]]; then
    out=$("$TPY" "$FILTER" semgrep "${REPORTS}/semgrep.json" "$BASELINE" "$UPDATE_BASELINE")
    echo "$out" | sed '$d'
    new=$(echo "$out" | tail -n 1)
    if [[ "$new" -eq 0 ]]; then record "semgrep" "OK" "$(echo "$out" | tail -n 2 | head -n 1)"
    else record "semgrep" "ECHEC" "${new} nouveau(x) resultat(s) a examiner"; fi
else
    record "semgrep" "ECHEC" "semgrep n'a pas produit de rapport (reseau ?)"
fi

# --- 4. gitleaks -------------------------------------------------------------
step "4. gitleaks (secrets)"
if [[ -x "$GITLEAKS" ]]; then
    # Historique complet + changements non commites (fichiers suivis). Le
    # .env, ignore par Git, n'est volontairement pas analyse.
    "$GITLEAKS" git . --redact --no-banner -f json -r "${REPORTS}/gitleaks-history.json" 2>&1 | tail -n 2
    rc_hist=${PIPESTATUS[0]}
    "$GITLEAKS" git . --pre-commit --redact --no-banner -f json -r "${REPORTS}/gitleaks-worktree.json" 2>&1 | tail -n 2
    rc_wt=${PIPESTATUS[0]}
    if [[ $rc_hist -eq 0 && $rc_wt -eq 0 ]]; then record "gitleaks" "OK" "aucun secret dans Git"
    else record "gitleaks" "ECHEC" "secret(s) detecte(s) : voir ${REPORTS}/gitleaks-*.json"; fi
else
    record "gitleaks" "ECHEC" "gitleaks indisponible"
fi

# --- 5. flask-unsign ---------------------------------------------------------
step "5. flask-unsign (robustesse de la SESSION_SECRET_KEY)"
source_desc="cookie fourni"
if [[ -z "$COOKIE" && -x "$APP_PY" && -f "${ROOT}/.env" ]]; then
    source_desc="cle locale (.env)"
    COOKIE=$("$APP_PY" - <<'PYEOF' 2>/dev/null | tail -n 1
import os, sys
sys.path.insert(0, os.getcwd())
import run
client = run.app.test_client()
client.get("/admin/locale/fr")
print(client.get_cookie("session").value)
PYEOF
)
fi
if [[ -n "$COOKIE" ]]; then
    out=$("${TOOLS}/venv/bin/flask-unsign" --unsign --cookie "$COOKIE" --no-literal-eval 2>&1 | tr '\r' '\n' | tail -n 1)
    echo "$out" > "${REPORTS}/flask-unsign.txt"
    echo "$out"
    if echo "$out" | grep -q "Failed to find secret key"; then
        record "flask-unsign" "OK" "cle introuvable par dictionnaire (${source_desc})"
    else
        record "flask-unsign" "ECHEC" "CLE RETROUVEE (${source_desc}) : la changer immediatement"
    fi
else
    record "flask-unsign" "IGNORE" "pas de cookie (ni --cookie, ni .venv/.env locaux)"
fi

# --- 6. OWASP ZAP (option) ---------------------------------------------------
if [[ $RUN_ZAP -eq 1 ]]; then
    step "6. OWASP ZAP (scan passif sur http://127.0.0.1:${ZAP_PORT})"
    if [[ -z "${ZAP_HOME:-}" || ! -x "${ZAP_HOME}/zap.sh" ]]; then
        record "zap" "ECHEC" "ZAP_HOME non defini ou zap.sh introuvable"
    elif ! command -v java >/dev/null 2>&1; then
        record "zap" "ECHEC" "java introuvable (Java 17+ requis)"
    else
        FLASK_DEBUG=0 "$APP_PY" run.py -p "$ZAP_PORT" > "${REPORTS}/zap-app.log" 2>&1 &
        app_pid=$!
        for _ in $(seq 1 30); do
            curl -s -o /dev/null "http://127.0.0.1:${ZAP_PORT}/signin" && break
            sleep 1
        done
        cat > "${REPORTS}/zap-plan.yaml" <<EOF
env:
  contexts:
    - name: tunoova
      urls: ["http://127.0.0.1:${ZAP_PORT}"]
      includePaths: ["http://127.0.0.1:${ZAP_PORT}.*"]
      excludePaths: [".*/logout.*", ".*/admin/locale/.*", ".*/media/.*", ".*/cast/.*"]
  parameters:
    failOnError: false
    progressToStdout: false
jobs:
  - type: spider
    parameters: {context: tunoova, url: "http://127.0.0.1:${ZAP_PORT}/", maxDuration: 5}
  - type: passiveScan-wait
    parameters: {maxDuration: 10}
  - type: report
    parameters: {template: traditional-json, reportDir: "${REPORTS}", reportFile: zap}
  - type: report
    parameters: {template: traditional-html, reportDir: "${REPORTS}", reportFile: zap}
EOF
        "${ZAP_HOME}/zap.sh" -cmd -dir "${TOOLS}/zaphome" -autorun "${REPORTS}/zap-plan.yaml" > "${REPORTS}/zap.log" 2>&1
        kill "$app_pid" 2>/dev/null
        if [[ -s "${REPORTS}/zap.json" ]]; then
            counts=$("$TPY" - "${REPORTS}/zap.json" <<'PYEOF'
import json, sys
d = json.load(open(sys.argv[1]))
risk = {"3": 0, "2": 0, "1": 0, "0": 0}
for site in d["site"]:
    for a in site["alerts"]:
        risk[a["riskcode"]] += 1
        if a["riskcode"] in ("3", "2"):
            print("  ", a["riskdesc"].split()[0], a["name"], file=sys.stderr)
print(risk["3"], risk["2"], risk["1"])
PYEOF
)
            read -r high med low <<< "$counts"
            if [[ "$high" -eq 0 ]]; then
                record "zap" "OK" "eleve 0, moyen ${med}, faible ${low} (cf. zap.html)"
            else
                record "zap" "ECHEC" "${high} alerte(s) de risque eleve (cf. zap.html)"
            fi
        else
            record "zap" "ECHEC" "ZAP n'a pas produit de rapport (cf. zap.log)"
        fi
    fi
fi

# --- Synthese ----------------------------------------------------------------
step "Synthese (rapports : ${REPORTS})"
printf '%s\n' "${SUMMARY[@]}" | tee "${REPORTS}/synthese.txt"
exit $FAILED
