---
name: tunoova-radio-plugin
description: Crée un plug-in radio Tunoova (script Python radio_plugins/<nom>.py) qui affiche l'émission ou le morceau en cours d'une radio ou d'un groupe de radios. À utiliser quand on demande un nouveau plug-in radio, le "titre en cours" / l'émission en cours d'une station pour Tunoova, ou la réparation d'un plug-in radio existant.
---

# Créer un plug-in radio Tunoova

Un plug-in est **un seul fichier Python** qui dit à Tunoova ce qui passe à l'antenne
d'une radio (émission ou morceau). Il est déposé dans `radio_plugins/` ou uploadé depuis
**Admin → Plug-ins radio**, puis exécuté **sur le serveur**.

Fichiers fournis avec ce skill :
- `template.py` : squelette commenté à copier.
- `test_plugin.py` : banc d'essai autonome (pas besoin de Tunoova, seulement `requests`).
- `example_rmc.py` : plug-in réel et simple (grille d'émissions JSON).

## 1. Recueillir les informations

Demander à l'utilisateur (une seule fois, tout ensemble) :
1. La ou les radios visées (nom, site web).
2. L'**URL du flux** utilisée dans Tunoova pour chaque radio (Admin → Radios, ou la fiche
   radio). Elle sert à la détection et au test.
3. Si une **clé d'API** est nécessaire et disponible (rare : la plupart des sources sont publiques).

## 2. Trouver la source des données

But : un endpoint HTTP qui renvoie l'émission ou le morceau en cours, idéalement en JSON et sans clé.
Par ordre de préférence :
1. **Le JSON qu'utilise le lecteur du site de la radio.** Explorer la page du direct
   (WebFetch sur la page, puis chercher dans le HTML/JS des URL contenant `live`, `onair`,
   `now`, `epg`, `programme`, `grille`, `livemeta`, `api`). Si l'outil ne voit pas le
   JavaScript, demander à l'utilisateur d'ouvrir les outils de développement du navigateur
   (F12 → Réseau → filtre Fetch/XHR) sur la page du direct et de copier les URL appelées.
2. Un agrégateur public : Radioplayer (pays couverts), API d'un groupe (ex. `onair.json` du groupe NRJ).
3. Une page HTML de grille des programmes (à parser avec `re`, en dernier recours).

Valider l'endpoint en l'appelant (WebFetch ou `curl`) et noter : la structure, les champs
titre/animateur/image, les horaires (format, fuseau, ms ou s), et si l'en-tête
`User-Agent` est requis. Rejeter les sources protégées par un défi Cloudflare/JavaScript :
le serveur ne peut pas les lire (le signaler dans la docstring, comme `nrj.py` pour Chérie FM).

## 3. Écrire le plug-in

Partir de `template.py`. **Contrat (vérifié à l'installation) :**

```python
NAME = "RMC"                                   # nom affiché dans l'Admin
def detect(stream_url: str, name: str) -> str | None
    # Station ID chez ce fournisseur si le plug-in gère cette radio
    # ("" s'il n'en a pas besoin), None sinon
def fetch(ref: str, stream_url: str, api_key: str | None) -> dict | None
    # ce qui passe à l'antenne, ou None si rien / inconnu
SUPPORTED_STATIONS = [("RMC", "rmc"), ...]     # facultatif : (nom affiché, Station ID)
```

Format renvoyé par `fetch` (tout autre champ est ignoré ; sans `show` non vide = rien affiché) :

```python
{
    "kind": "show" | "song",     # émission ou morceau
    "show": "Titre",             # émission, ou "Artiste - Titre" pour un morceau
    "detail": "Animateur" | None,
    "image_url": "https://..." | None,
    "start": epoch_secondes | None,
    "end": epoch_secondes | None,  # sert à rafraîchir dès la fin : à fournir si connu
}
```

Règles importantes (elles découlent de la façon dont Tunoova appelle les plug-ins) :
- **`detect` est appelé pour toutes les radios du catalogue**, plug-in par plug-in, et le
  premier qui ne renvoie pas `None` gagne. Il doit donc être **strict** (domaine du flux
  d'abord, nom normalisé ensuite) et **rapide** : pas d'appel réseau, sauf après un
  pré-filtre sur le domaine ou le nom (voir `nrj.py`). Ne jamais lever d'exception
  pour une radio inconnue : renvoyer `None`.
- Le **Station ID** (`ref`) est enregistré sur la radio et peut être corrigé à la main dans
  l'Admin : choisir un identifiant lisible et stable (slug ou numéro de la source). `fetch`
  doit renvoyer `None` pour un `ref` qu'il ne connaît pas.
- `fetch` : `requests` avec `timeout` (6 à 8 s) et `User-Agent`, puis `raise_for_status()`.
  Une exception est acceptée (le plug-in passe en statut « erreur » dans l'Admin et
  Tunoova se rabat sur les métadonnées ICY du flux). « Rien à l'antenne » = `None`, pas une exception.
- Choisir l'émission **dont les horaires encadrent l'heure actuelle** plutôt que de faire
  confiance au champ « en cours » de la source, souvent en retard (voir `example_rmc.py`).
- Horaires : convertir en **epoch en secondes** (les ms sont fréquentes : `// 1000`).
  Une heure locale sans fuseau → `zoneinfo.ZoneInfo("Europe/Paris")` (ou le fuseau de la radio).
- Une source qui sert plusieurs radios en un seul appel → petit cache au niveau du module
  (`_feeds = {}` avec expiration ~25 s), comme `nrj.py`. Tunoova met déjà chaque résultat en cache 30 s.
- Dépendances : **bibliothèque standard + `requests` uniquement.**
- Nom du fichier : `^[a-z][a-z0-9_]{0,60}\.py$` (ex. `radio_nova.py`), taille < 256 Ko.
- Style du dépôt : docstring de tête en français qui donne la source exacte, le format du
  Station ID, la méthode de détection et les limites connues, puis la ligne
  `Contrat commun a tous les plug-ins : cf. app/now_playing.py.` Commentaires en français, sans accents.
- Pas de `print`, pas d'écriture de fichiers, pas de thread : le script tourne dans le
  serveur web.

## 4. Tester

Le banc d'essai (`test_plugin.py`, dans le dossier de ce skill) charge le script exactement
comme Tunoova (contrat + normalisation) :

```bash
python test_plugin.py radio_plugins/mon_plugin.py \
    --stream "https://url-du-flux" --name "Nom de la radio" \
    --other "https://icecast.radiofrance.fr/franceinter-hifi.aac" "France Inter"
```

- `--stream/--name` : doit détecter un Station ID et afficher ce qui passe à l'antenne.
- `--other URL NOM` (répétable) : radios **d'un autre fournisseur** ; `detect` doit renvoyer `None`.
  Toujours tester au moins une radio homonyme ou proche (ex. une Nostalgie belge contre un plug-in français).
- `--ref ID` : tester `fetch` directement avec un Station ID ; `--api-key` si besoin.
- Sans `--stream`, chaque entrée de `SUPPORTED_STATIONS` est appelée avec son Station ID.

Corriger jusqu'à ce que tout soit `OK`, puis vérifier que le titre affiché correspond bien
au direct (site de la radio).

## 5. Livrer

- **Dans le dépôt Tunoova** : enregistrer le fichier dans `radio_plugins/`. Il est pris en
  compte à la prochaine ouverture de la page Admin → Plug-ins radio. Ajouter son nom à la
  liste des plug-ins livrés dans `Doc/Documentation technique.md`.
- **Sur une installation existante** : Admin → Plug-ins radio → *Installer un plug-in* →
  choisir le `.py` (et la clé d'API si besoin). Un script du même nom remplace l'ancien,
  dont la version précédente est gardée (bouton « Revenir à la version précédente »).
  Les radios sans plug-in sont alors redétectées automatiquement ; bouton **Tester** pour vérifier.

Terminer en résumant à l'utilisateur : la source utilisée, les radios reconnues, le résultat
du test, et les limites (API non documentée susceptible de changer, radios non couvertes).
