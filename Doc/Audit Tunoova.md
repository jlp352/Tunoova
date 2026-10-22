# Audit de sécurité de Tunoova

Ce document explique comment lancer un audit de sécurité de l'appli avec `scripts/audit.sh`, et comment lire et traiter ses résultats.

À lancer :

- avant chaque mise en production ;
- après un changement de `requirements.txt` ;
- régulièrement (par exemple une fois par mois), pour détecter les nouvelles failles publiées dans les dépendances.

## 1. Ce que l'audit contrôle

| # | Contrôle | Outil | Échoue si… |
|---|---|---|---|
| 1 | Vulnérabilités connues des dépendances (`requirements.txt` et paquets installés dans `.venv`) | pip-audit | une dépendance a une faille publiée |
| 2 | Analyse statique du code Python (`app`, `radio_plugins`, `db`, `run.py`) | bandit | un résultat n'est pas dans la baseline |
| 3 | Analyse statique (règles Flask, Python, security-audit, JavaScript, secrets) | semgrep | un résultat n'est pas dans la baseline |
| 4 | Secrets dans l'historique Git et les changements en cours | gitleaks | un secret est détecté |
| 5 | Robustesse de la `SESSION_SECRET_KEY` (attaque par dictionnaire sur un cookie de session) | flask-unsign | la clé est retrouvée |
| 6 | Scan dynamique passif de l'appli lancée en local (option `--zap`) | OWASP ZAP | une alerte de risque élevé est levée |

Le fichier `.env` est ignoré par Git : gitleaks ne l'analyse pas.

## 2. Prérequis

- Linux (ou WSL) avec `python3`, `python3-venv`, `curl` et un accès Internet (installation des outils, règles semgrep, bases de vulnérabilités).
- Le venv de l'appli (`.venv`) et un `.env` à la racine du projet, pour les contrôles 1 (paquets installés) et 5 (clé locale).
- Pour le scan ZAP uniquement : Java 17 ou plus, et [OWASP ZAP](https://www.zaproxy.org/download/) installé. La variable `ZAP_HOME` doit désigner le dossier qui contient `zap.sh`.

Les outils (pip-audit, bandit, semgrep, flask-unsign, gitleaks) sont installés automatiquement au premier lancement dans `~/.cache/tunoova-audit`, jamais dans le venv de l'appli. Gitleaks est téléchargé depuis GitHub, avec vérification de sa somme de contrôle. Pour choisir un autre dossier : variable `TUNOOVA_AUDIT_TOOLS`.

## 3. Lancer l'audit

Depuis la racine du projet :

```bash
scripts/audit.sh                                      # contrôles 1 à 5
scripts/audit.sh --cookie "<cookie de prod>"          # contrôle 5 sur la clé de production
ZAP_HOME=/opt/zap scripts/audit.sh --zap              # contrôles 1 à 6
scripts/audit.sh --update-baseline                    # accepte les résultats bandit/semgrep actuels
scripts/audit.sh --help
```

| Option | Effet |
|---|---|
| `--zap` | Démarre l'appli sur le port 5055 (`FLASK_DEBUG=0`), lance le scan ZAP (spider de 5 min puis scan passif), puis arrête l'appli. Les pages `/logout`, `/admin/locale`, `/media` et `/cast` sont exclues. |
| `--cookie <valeur>` | Teste ce cookie `session` au lieu d'un cookie généré avec la clé du `.env` local. |
| `--update-baseline` | Enregistre tous les résultats bandit et semgrep actuels comme acceptés (voir §5). |

### Tester la clé de production

Par défaut, le contrôle 5 teste la clé du `.env` local, qui n'est pas celle du serveur. Pour tester la clé de production :

1. ouvrir l'appli de production dans le navigateur ;
2. dans les outils de développement (onglet Application / Stockage > Cookies), copier la valeur du cookie `session` ;
3. lancer `scripts/audit.sh --cookie "<valeur copiée>"`.

## 4. Lire les résultats

Le script affiche chaque étape puis une synthèse (exemple) :

```
pip-audit      OK      aucune vulnerabilite connue
bandit         OK      12 resultat(s), dont 0 nouveau(x) hors baseline
semgrep        OK      9 resultat(s), dont 0 nouveau(x) hors baseline
gitleaks       OK      aucun secret dans Git
flask-unsign   OK      cle introuvable par dictionnaire (cle locale (.env))
```

Chaque ligne vaut `OK`, `ECHEC` ou `IGNORE` (contrôle impossible, par exemple sans cookie ni `.env`). Le code de sortie vaut 0 si tout est OK, 1 sinon : le script peut servir de garde-fou dans un script de publication.

Les rapports détaillés sont écrits dans `scripts/audit-reports/<AAAAMMJJ_HHMMSS>/` (dossier ignoré par Git) :

| Fichier | Contenu |
|---|---|
| `synthese.txt` | La synthèse ci-dessus |
| `pip-audit-requirements.txt`, `pip-audit-venv.txt` | Failles des dépendances, avec la version corrigée |
| `bandit.json`, `semgrep.json` | Tous les résultats de l'analyse statique |
| `gitleaks-history.json`, `gitleaks-worktree.json` | Secrets détectés (valeurs masquées) |
| `flask-unsign.txt` | Résultat de l'attaque par dictionnaire |
| `zap.html`, `zap.json`, `zap.log`, `zap-app.log` | Rapport ZAP, journaux du scan et de l'appli |

## 5. Traiter un échec

| Contrôle | Que faire |
|---|---|
| **pip-audit** | Monter la version du paquet dans `requirements.txt` (au moins la version corrigée indiquée). Pour une dépendance indirecte (installée par Flask, requests, aiohttp…), ajouter un plancher `paquet>=version` dans la section prévue en fin de `requirements.txt`. Puis `.venv/bin/pip install -r requirements.txt` et relancer l'audit. |
| **bandit / semgrep** | Examiner chaque ligne `NOUVEAU`. Faille réelle : corriger le code. Faux positif : relancer avec `--update-baseline` pour l'ajouter à `scripts/audit-baseline.txt`, puis commiter ce fichier. |
| **gitleaks** | Un secret versionné doit être considéré comme compromis : le **changer** (mot de passe SMTP, clé d'API…), puis le retirer du dépôt. Le supprimer du dernier commit ne suffit pas, il reste dans l'historique. |
| **flask-unsign** | La clé est trop faible : en générer une nouvelle (`python -c "import secrets; print(secrets.token_hex(32))"`), la mettre dans `SESSION_SECRET_KEY` du `.env`, puis redémarrer l'appli. Toutes les sessions sont alors invalidées. |
| **ZAP** | Ouvrir `zap.html` et corriger les alertes de risque élevé. Les alertes de risque moyen ou faible sont à examiner, sans bloquer l'audit. |

### La baseline bandit / semgrep

`scripts/audit-baseline.txt` liste les résultats déjà examinés et jugés sans risque. Chaque ligne a la forme `outil|règle|fichier|ligne de code`. Elle repère un résultat par le contenu de la ligne de code, pas par son numéro : ajouter du code ailleurs dans le fichier ne fait pas réapparaître un résultat accepté.

`--update-baseline` accepte **tous** les résultats actuels. À n'utiliser qu'après avoir examiné chaque nouveau résultat.

## 6. Contrôles complémentaires en production

Ces points ne sont pas couverts par le script :

- `FLASK_DEBUG=0` dans le `.env` du serveur (`install.sh update` le force et l'affiche).
- Le service tourne bien sous waitress (`sudo journalctl -u tunoova -n 20` ne doit pas montrer le serveur de développement Werkzeug).
- La clé de production résiste au dictionnaire : `scripts/audit.sh --cookie "<cookie de prod>"`.
- Pas de fichier `~/.netrc` pour le compte système `tunoova`.
- Revue des connexions suspectes : Admin > Base de données > `LoginHistory` (échecs répétés, blocages, IP inhabituelles).
