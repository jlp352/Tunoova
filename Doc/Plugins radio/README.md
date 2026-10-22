# Plug-ins radio

Un plug-in radio est un petit script Python qui indique à Tunoova ce qui passe à l'antenne
d'une radio (émission ou morceau en cours). Les plug-ins livrés avec Tunoova se trouvent
dans `radio_plugins/`. Le contrat à respecter est décrit dans `app/now_playing.py` et dans
la Documentation technique.

## Créer un nouveau plug-in avec Claude

Le dossier [tunoova-radio-plugin](tunoova-radio-plugin/) est un **skill Claude** : il
contient la marche à suivre, un modèle et un banc d'essai pour que Claude écrive et teste
le plug-in à votre place.

| Fichier | Rôle |
|---|---|
| `SKILL.md` | Instructions pour Claude : trouver la source des données, écrire, tester, installer |
| `template.py` | Squelette d'un plug-in |
| `test_plugin.py` | Banc d'essai autonome (Python + `requests`, sans installer Tunoova) |
| `example_rmc.py` | Exemple réel (plug-in RMC) |

### Avec Claude Code

1. Copier le dossier `tunoova-radio-plugin` dans `~/.claude/skills/` (pour tous vos projets)
   ou dans `.claude/skills/` du projet.
2. Demander par exemple : « Crée un plug-in radio Tunoova pour Radio Nova, flux
   https://... ».

### Avec claude.ai

1. Compresser le dossier `tunoova-radio-plugin` en `.zip`.
2. Paramètres → Capacités → Skills → importer le zip.
3. Dans une conversation, faire la même demande. Claude fournit le fichier `.py`.

### Sans Claude

Suivre `SKILL.md` à la main : partir de `template.py`, puis vérifier le script avec

```bash
python test_plugin.py mon_plugin.py --stream "URL du flux" --name "Nom de la radio" \
    --other "https://icecast.radiofrance.fr/franceinter-hifi.aac" "France Inter"
```

## Installer le plug-in

Admin → Plug-ins radio → **Installer un plug-in** → choisir le fichier `.py` (et la clé
d'API si nécessaire), puis **Tester**. Les radios concernées sont détectées
automatiquement. Un script portant le même nom remplace l'ancien, qui reste disponible via
« Revenir à la version précédente ».

⚠️ Un plug-in est exécuté sur le serveur : n'installer que des scripts de confiance.
