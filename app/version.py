"""Métadonnées affichées sur l'écran "À propos" (Settings / Admin). Valeurs
versionnées avec le code (et non plus dans le .env) : install.sh update ne
réécrit jamais une clé existante du .env, la version affichée resterait donc
celle de la première installation. A changer à chaque publication."""

APP_VERSION = "1.0.0"
APP_RELEASE_DATE = "2026-10-22"
APP_AUTHOR = "JLP352"
APP_REPO_URL = "https://github.com/jlp352/Tunoova"
# Compteur d'installations / mises a jour du site du projet : install.sh y
# envoie un POST ?event=install|update&version=APP_VERSION en fin de
# commande (desactivable avec TUNOOVA_NO_STATS=1).
APP_STATS_URL = "https://tunoova.com/api/app"
