# Tunoova — Documentation technique

🇬🇧 [English](<Technical documentation.md>) · 🇫🇷 **Français**

Documents liés :

| Document                                                                              | Contenu                                                                        |
| ------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------ |
| [README (fonctionnel)](../README.fr.md) | Ce que fait l'appli, écran par écran, du point de vue utilisateur              |
| [DB Structure](<DB Structure.fr.md>)                                                     | Détail de chaque table et colonne (généré depuis `app/models.py`)              |
| [Installation](Installation.fr.md)                                                       | `install.sh` : installation, première configuration, mise à jour, reset admin, commandes utiles |
| [Audit Tunoova](Audit%20Tunoova.md)                                                   | Lancer un audit de sécurité (`scripts/audit.sh`) et traiter ses résultats      |

---

## 1. Architecture

```
 Navigateur (htmx + app.js)              Enceintes LAN (SoundTouch / DLNA)
        │  HTML partiel, JSON, audio              ▲  flux HTTP /media/cast/...
        ▼                                         │  (jeton signé, LAN uniquement)
 ┌───────────────────────────────────────────────────────────────┐
 │ waitress (32 threads)                                         │
 │  TrustedProxyMiddleware → SecureCookieMiddleware → Flask app  │
 │   ├─ blueprints : auth, main, podcast, radio, library, play,  │
 │   │               cast, notifications, settings, userprofile, │
 │   │               media, admin                                │
 │   ├─ services   : *_service.py, music_catalog, now_playing…   │
 │   ├─ APScheduler (thread) : 6 jobs cron                       │
 │   └─ boucle asyncio (thread) : contrôle UPnP/DLNA du Cast     │
 └───────────────┬───────────────────────────────┬───────────────┘
                 ▼                               ▼
        SQLite db/tunoova.db          APIs externes (iTunes, Radio Browser,
        db/profile_photos/            Deezer, YouTube Music, plug-ins radio)
        MP3_LIBRARY_PATH              ffmpeg / yt-dlp / deno / tailscale
```

Principes :

- **Monolithe Flask rendu côté serveur**, sans API REST séparée ni framework SPA. La navigation passe par **htmx** (`hx-boost` sur `<body>`) : seule la zone `#app-boost-root` est échangée, le lecteur `<audio>` et le mini-lecteur restent dans la page et la lecture ne s'interrompt pas.
- **Un seul processus** : le scheduler, la boucle asyncio du Cast et les caches mémoire (URL YouTube, titres radio, statut des jobs) vivent dans ce processus. **Ne pas lancer plusieurs workers** : les jobs tourneraient en double et les caches divergeraient.
- **SQLite** comme unique stockage, sans outil de migration (voir §9.3).
- Le **`.env` sert aussi de stockage de configuration modifiable** : l'Espace Admin le réécrit (`dotenv.set_key`).

## 2. Stack et dépendances

### Python (`requirements.txt`)

| Paquet | Usage |
|---|---|
| Flask, Flask-SQLAlchemy, Flask-Login, Flask-WTF | Web, ORM, sessions utilisateur, CSRF |
| bcrypt | Hash des mots de passe (utilisateurs et admin) |
| python-dotenv | Lecture et réécriture du `.env` |
| APScheduler | Jobs planifiés (syntaxe crontab) |
| user-agents | Appareil et OS dans `Log` / `LoginHistory` |
| requests, feedparser | Appels HTTP, lecture des flux RSS |
| mutagen | Lecture et écriture des tags ID3 |
| ytmusicapi, yt-dlp[default] | Recherche YouTube Music, extraction du flux audio |
| libsoundtouch, zeroconf, async-upnp-client, aiohttp | Découverte et contrôle des enceintes (Cast) |
| segno | QR code de l'adresse publique (Admin > Réseau) |
| waitress | Serveur WSGI de production |
| Werkzeug, urllib3, multidict (planchers) | Versions minimales de sécurité des dépendances indirectes |

### Système (installé par `install.sh`)

| Outil | Usage |
|---|---|
| python3, venv, pip | Exécution |
| git, openssh-client | Mise à jour depuis GitHub (deploy key si dépôt privé) |
| ffmpeg | Remultiplexage à la volée pour le Cast (flux YouTube MP4/DASH, reprise à une position) |
| deno | Runtime JS utilisé par yt-dlp pour les challenges YouTube |
| zip, unzip, curl | Backups, installation de deno |
| tailscale | Accès à distance (optionnel, Admin > Réseau) |

### Front-end

- `app/static/js/vendor/htmx.min.js` (seule librairie JS).
- `app/static/js/app.js` (~1 850 lignes, JS natif) : lecteur, panneau Lecture, Cast, préchargement, Media Session.
- `app/static/css/app.css` : thème clair/sombre via `data-theme` sur `<html>`.
- Icônes Lucide (SVG inlinés par `app/icons.py`, recolorables en CSS).

## 3. Arborescence

```
run.py                  Point d'entrée : create_app, init/migration DB, serveur
install.sh              Installation / mise à jour / reset admin (root)
requirements.txt
.env.example            Modèle de configuration (fusionné dans .env à chaque update)
app/
  __init__.py           create_app : extensions, context processors, en-têtes, blueprints
  config.py             Classe Config (lit les variables d'environnement)
  models.py             Modèles SQLAlchemy (source de vérité du schéma)
  version.py            APP_VERSION, APP_RELEASE_DATE…  (à changer à chaque publication)
  <blueprint>/routes.py auth, main, podcast, radio, library, play, cast,
                        notifications, settings, userprofile, admin/*
  media.py              Blueprint /media : flux audio, pochettes, photo de profil
  *_service.py          Logique métier (voir §10)
  templates/            Jinja2, un dossier par blueprint
  translations/         en.json, fr.json
  static/               css, js, icônes, images de marque
radio_plugins/          Plug-ins « titre en cours » (un script par fournisseur)
db/
  seed.py               ensure_schema_upgrades() + seed_reference_data()
  tunoova.db            Base (non versionnée)
  profile_photos/       Photos de profil (non versionnées, hors static)
  backups/              Sauvegardes (non versionnées)
mp3/                    Bibliothèque MP3 par défaut (non versionnée)
scripts/                audit.sh (versionné) ; gen_db_doc.py (non versionné)
Doc/
```

## 4. Démarrage

`run.py`, au niveau module (donc aussi lors d'un `import run`) :

1. `load_dotenv()` puis `create_app()` :
   - extensions (SQLAlchemy, LoginManager, CSRFProtect, i18n) ;
   - globals Jinja : `cover_for`, `icon`, `time_ago`, `short_count`, `t` ;
   - context processors injectés dans **chaque** rendu : `notifications_count` (badge), `current_play` (mini-lecteur rendu côté serveur depuis la table `Play`), `other_users` (formulaire de partage), `pwa_installable` (manifeste d'application, voir §8.8) ;
   - `after_request` : `Cache-Control: private` (+ `no-store` pour HTML/JSON/téléchargements) et en-têtes de sécurité (§14) ;
   - signal `user_loaded_from_cookie` : trace une reprise de session dans `LoginHistory` (une fois par session) ;
   - enregistrement des 12 blueprints ;
   - `scheduler.init_app` (démarre APScheduler) et `cast_service.init_app` (boucle asyncio) ;
   - middlewares WSGI `TrustedProxyMiddleware(SecureCookieMiddleware(app))`.
2. `db.create_all()`, `ensure_schema_upgrades()`, `seed_reference_data()`.
3. Si exécuté directement :
   - `FLASK_DEBUG=0` (production) : **waitress**, `threads=32` (chaque flux audio occupe un thread), `channel_timeout=900` (lecteur en pause), pas d'en-tête `Server` ;
   - `FLASK_DEBUG=1` (dev) : serveur Werkzeug avec reloader `stat` (ne surveille pas `.env`, que l'admin réécrit). En debug, le scheduler ne démarre que dans le processus enfant du reloader.

En production, le service systemd `tunoova` lance `.venv/bin/python run.py -p <port>` sous le compte système `tunoova`, avec `Restart=on-failure` (utilisé pour redémarrer après une restauration).

## 5. Configuration (`.env`)

| Clé | Défaut | Modifiée par | Rôle |
|---|---|---|---|
| `SESSION_SECRET_KEY` | — (obligatoire) | install.sh | Signature des sessions, jetons, cookie admin |
| `ADMIN_EMAIL`, `ADMIN_PASSWORD_HASH` | — | install.sh, Admin > Tableau de bord, `install.sh reset` | Compte admin (hors base) |
| `DATABASE_PATH` | `db/tunoova.db` | — | Base SQLite |
| `MP3_LIBRARY_PATH` | `./mp3` | Admin > Tableau de bord | Racine de la bibliothèque |
| `MP3_UPLOAD_MAX_MB` | 200 | — | Taille max d'une requête (`MAX_CONTENT_LENGTH`) : lot d'upload MP3, archive de restauration |
| `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASSWORD`, `SMTP_FROM` | port 587 | Admin > Tableau de bord | Envoi des emails |
| `JOB_<NOM>_CRON`, `JOB_<NOM>_ENABLED` | voir §11 | Admin > Jobs | Planification des 6 jobs |
| `BACKUP_PATH`, `BACKUP_RETENTION_DAYS` | `db/backups`, 7 | Admin > Tableau de bord | Sauvegardes |
| `CAST_BASE_URL` | vide | — | URL de base des flux Cast si l'IP détectée n'est pas joignable par les enceintes |
| `REMOTE_ACCESS_MODE` | vide | Admin > Réseau | `off`, `external`, `private`, `public` |
| `PUBLIC_HOSTNAME` | vide | Admin > Réseau | Nom DNS en mode `external` |
| `TRUSTED_PROXY_COUNT` | 0 | Admin > Réseau | Nombre de reverse proxies de confiance |
| `UPDATE_ANNOUNCED_VERSION` | — | Admin (vérification de mise à jour) | Version annoncée aux utilisateurs |
| `FLASK_DEBUG` | 0 | install.sh (force 0) | Mode développement |
| `MP3_SCAN_INTERVAL_MINUTES`, `PODCASTINDEX_API_KEY`, `PODCASTINDEX_API_SECRET` | — | — | **Inutilisées** dans le code |

Configuration hors `.env` (installation par `install.sh`) : `/etc/tunoova/update.conf`, deploy key et `known_hosts` dans `/etc/tunoova/`, état de mise à jour dans `/var/lib/tunoova/`, journal `/var/log/tunoova-update.log`.

## 6. Front-end

### Navigation et lecteur persistant

- `<body hx-boost="true" hx-target="#app-boost-root" hx-select="#app-boost-root">` : chaque lien interne charge la page complète côté serveur mais n'échange que la zone de contenu.
- L'élément `<audio>`, le mini-lecteur, le panneau `#play-overlay` (écran Lecture) et la fenêtre Cast sont **hors** de cette zone. Le panneau Lecture s'ouvre par-dessus la page, alimenté par `GET /play/<type>/<id>/data` (JSON), sans navigation.
- Le bouton Retour du navigateur (restauration d'historique htmx) ne respecte pas `hx-preserve` : le mini-lecteur est donc aussi rendu côté serveur à chaque page (`inject_current_play`), et `app.js` surcharge `popstate`.
- Les listes (recherche live, tri) sont des fragments htmx : `GET /<écran>/list?q=…&sort=…`.

### `app.js` — responsabilités

| Domaine | Fonctions principales |
|---|---|
| Lecture | `tunoovaStartPlayback`, `tunoovaTogglePlayback`, `tunoovaSkipTrack`, `tunoovaPlayNextAfterEnded`, reprise (`tunoovaSyncFromServer`, `GET /play/current`) |
| État serveur | `reportState` → `POST /play/state` (statut + position, périodique) |
| Préchargement | `tunoovaPreloadNext` : télécharge le morceau suivant en Blob (`?prefetch=1`), `tunoovaWarmStream` → `POST /media/ytmusic/<id>/warm` |
| Panneau Lecture | `tunoovaShowPlayOverlay`, `tunoovaFillPlayOverlay`, like, commentaires, partage |
| Radio | `tunoovaRefreshRadioNowPlaying` → `GET /radio/<id>/now-playing` (périodique) |
| Cast | fenêtre, liste, sélection, volume, polling `GET /cast/status` toutes les ~3 s |
| Media Session | métadonnées, `playbackState`, `setPositionState`, actions play/pause/next/previous (écran de verrouillage) |
| CSRF | jeton lu dans `<meta name="csrf-token">`, envoyé sur chaque `fetch` POST (`WTF_CSRF_TIME_LIMIT=None` car la page n'est jamais rechargée) |

Convention : les routes appelées en `fetch` renvoient du JSON si l'en-tête `X-Requested-With: XMLHttpRequest` est présent, sinon une redirection (repli sans JS).

### Thème et langue

- `data-theme="dark|light"` sur `<html>` selon `Users.DarkMode`.
- Langue : voir §13.

## 7. Pages et routes

Toutes les routes de l'appli exigent `@login_required` sauf mention contraire. `log_screen_view(<Frame>)` écrit une ligne dans `Log` à l'affichage de chaque écran.

### 7.1 Authentification — `app/auth/routes.py`

| Route | Méthodes | Template | Rôle |
|---|---|---|---|
| `/signin` | GET, POST | `auth/signin.html` | Connexion. Vérifie le blocage IP (`login_throttle`), le blocage compte (`Users.LockedUntil`), le mot de passe bcrypt. Succès : remise à zéro des échecs, `LastLogin`, session neuve, `login_user(remember=True)`, suppression du cookie admin, redirection vers `firstlogin` ou Home. Chaque issue est tracée dans `LoginHistory`. Public. |
| `/signin/request-account` | POST | `auth/_request_sent.html` | Demande de compte : email à l'admin (`notify_admin`). Champ piège `website` + limites (3/h par IP, 1/24 h par email, 20/24 h au total). Réponse identique si la demande est bloquée. Public. |
| `/signin/request-reset` | POST | idem | Demande de réinitialisation, mêmes protections ; réponse identique que le compte existe ou non. Public. |
| `/activate/<token>` | GET, POST | `auth/set_password.html` | Choix du mot de passe initial (jeton 24 h). Public. |
| `/reset-password/<token>` | GET, POST | idem | Nouveau mot de passe (jeton 1 h, lié au hash actuel : usage unique). Public. |
| `/firstlogin` | GET, POST | `auth/firstlogin.html` | Profil initial. Nom, tranche d'âge et pays obligatoires. `language_only=1` : change la langue sans valider. Passe `FirstLogin` à faux. |
| `/logout` | GET | — | Trace `logout`, vide la session, supprime les cookies remember et admin. |
| `/legal/terms`, `/legal/privacy` | GET | `auth/terms.html`, `auth/privacy.html` | Textes légaux (FR/EN). Public. |

### 7.2 Accueil — `app/main/routes.py`

| Route | Rôle |
|---|---|
| `GET /` | Redirige vers `firstlogin` si besoin. Top 5 `PodcastUsers` et top 5 `RadioUsers` par `CountListen` ; artistes distincts des 10 `MusicUsers` les plus écoutés (`CountListen > 0`, morceau disponible). Ordre des sections lu dans `Users.SortPrefs["home"]`. |
| `POST /home/order` | Enregistre l'ordre des sections (`podcast`, `radio`, `music`). |
| `GET /manifest.webmanifest` | Manifeste d'application web (installation Chrome). Public : Chrome le télécharge sans cookies. |

### 7.3 Podcast — `app/podcast/routes.py`, `app/podcast_service.py`

| Route | Rôle |
|---|---|
| `GET /podcast/` | Liste des abonnements (tri `newest`/`oldest` par date du dernier épisode, `az`/`za`). Avec `q` (≥ 3 car.) : podcasts dont le nom correspond, podcasts dont un épisode correspond, et résultats iTunes (pays FR ou US selon la langue). `?added=<id>` fait défiler jusqu'au podcast ajouté. |
| `GET /podcast/list` | Fragment htmx de la même liste. |
| `GET /podcast/<id>` | Détail : épisodes paginés par 20, filtre appliqué à l'affichage, ordre des séries « [1/2] » (`order_episodes`). |
| `GET /podcast/<id>/episodes?offset=` | Page suivante (défilement infini). |
| `POST /podcast/<id>/refresh` | `sync_episodes` immédiat. |
| `POST /podcast/add` | Abonnement depuis un résultat iTunes ; premier import sans statut `New`. |
| `GET/POST /podcast/add/advanced` | Ajout manuel (titre, RSS, image, filtre). |
| `GET/POST /podcast/<id>/edit` | Modification. |
| `POST /podcast/<id>/delete` | Désabonnement (supprime `PodcastUsers` et ses `EpisodesUsers`). |
| `POST /podcast/episode/<id>/play` | `New` → `Unread`, `CountListen`+1, `set_current_play`, renvoie le JSON de lecture (URL directe de l'épisode). |

Règles :

- `Podcast` est le catalogue partagé (unique par `LinkRSS`), `PodcastUsers` l'abonnement d'un utilisateur, `EpisodesUsers` ses épisodes.
- `sync_episodes` lit au plus 50 entrées du flux, ajoute les URL inconnues, applique le filtre `max:/contain:/exclude:` et marque `New` sauf au premier import.

### 7.4 Radio — `app/radio/routes.py`, `app/radio_service.py`, `app/now_playing.py`

| Route | Rôle |
|---|---|
| `GET /radio/` | Liste (`newest`/`oldest` par `LastListen`, `az`/`za`). Avec `q` : filtre local + Radio Browser (pays du profil en priorité via `Country.ApiName`). Les radios partagées non acceptées (`RadioStatus=Shared`) sont exclues (`user_radios`). |
| `GET /radio/list` | Fragment htmx. |
| `POST /radio/add/play?name=&stream_url=&image_url=` | Clic sur un résultat de recherche : ajout aux favoris + lecture. |
| `GET/POST /radio/add/advanced` | Ajout manuel. |
| `GET/POST /radio/<id>/edit` | Modification. |
| `POST /radio/<id>/delete` | Suppression du favori. |
| `POST /radio/<id>/play` | `CountListen`+1, `Radio.RadioCount`+1, `Shared/New` → `Read`, `set_current_play`, JSON de lecture (URL directe du flux). |
| `GET /radio/<id>/now-playing` | Titre en cours (plug-in ou ICY), interrogé périodiquement par le mini-lecteur. |

Radio Browser : interrogé sur plusieurs miroirs avec repli, favicons vérifiés en parallèle (24 workers, cache 6 h).

### 7.5 Musique — `app/library/routes.py`, `app/music_catalog.py`, `app/youtube_music_service.py`

| Route | Rôle |
|---|---|
| `GET /library/` | Écran Musique. Tris `artist_az/za`, `album_az/za`, `favorites_az/za`. Recherche (≥ 3 car.) sur les MP3 locaux avec affichage en cascade (album / artiste / morceaux), plus section YouTube Music (`search_smart`) si `YoutubeMusicEnabled`. Mémorise recherche et tri en session pour les boutons Retour. |
| `GET /library/list` | Fragment htmx. |
| `GET /library/artist/<id>` | Albums d'un artiste (`?fav=1` : seulement ce que l'utilisateur a écouté). |
| `GET /library/artist/<id>/others` | Albums YouTube de l'artiste absents du catalogue. |
| `GET /library/artist/top?name=` | « Les plus populaires » (YouTube) ; repli vers l'écran artiste si YouTube désactivé ou sans résultat. |
| `POST /library/artist/top/play` | Lance la file « plus populaires » (`Play.MusicQueue`). |
| `GET /library/album/<id>` | Morceaux d'un album (`?fav=1`). |
| `POST /library/album/<id>/play` | Lance le premier morceau de l'album. |
| `GET /library/youtube/artist`, `GET /library/youtube/album` | Prévisualisation éphémère d'un artiste ou d'un album YouTube (rien n'est enregistré). |
| `GET /library/youtube/album/import` | Importe tous les morceaux d'un album YouTube dans le catalogue. |
| `GET/POST /library/youtube/track/add` | Ajoute un morceau YouTube au catalogue et le lance. |
| `POST /library/<track_id>/play` | Lecture d'un morceau (`?queue=` : file explicite). Crée `MusicUsers` au besoin, compte l'écoute. |

Règles du catalogue :

- `MusicArtist` → `MusicAlbum` → `MusicTrack`, partagés par tous et par les deux sources (`Source = local | youtube`). Les noms sont dédoublonnés par une clé normalisée (`NameKey`, `TitleKey`). Les compilations sont rattachées à « Various Artists », chaque piste gardant son interprète.
- `MusicTrack.IsAvailable=False` + `MissingSince` : MP3 disparu, masqué partout, purgé après 30 jours (job `music_purge`).
- `MusicTrack.FromTop=True` : morceau ajouté seulement via « Les plus populaires » ; son album n'apparaît pas dans la bibliothèque.
- `MusicUsers` ne contient que les morceaux liés à l'utilisateur (écoute, like, partage, nouveauté).
- Images : `cover_service` cherche sur **Deezer**, puis **iTunes** en secours pour les pochettes ; URL stockées dans `MusicAlbum.CoverUrl` / `MusicArtist.PictureUrl`, nouvel essai après 7 jours (`CheckedAt`). Pochette embarquée dans le MP3 servie par `/media/album_cover/<id>`. À l'affichage, 6 recherches maximum ; le job `mp3_scan` complète le reste.

### 7.6 Lecture — `app/play/routes.py`, `app/play_service.py`

`<type>` ∈ `podcast` (id = `EpisodesUsersId`), `radio` (id = `RadioUsersId`), `music` (id = `TrackId`).

| Route | Rôle |
|---|---|
| `GET /play/current` | JSON de la lecture en cours (resynchronisation multi-appareils). |
| `GET /play/<type>/<id>` | Page complète (repli sans JS) ; `set_current_play`. |
| `GET /play/<type>/<id>/data` | JSON du panneau Lecture : flux, titres, image, compteurs, précédent/suivant, état Cast. `?prefetch=1` : sans effet de bord (préchargement). |
| `POST /play/<type>/<id>/like` | `value=Like|Not Like` ; met à jour le vote personnel et les compteurs du catalogue (`apply_like`). |
| `POST /play/<type>/<id>/comment` | Ajoute un commentaire au fil (`PodcastComment`, `RadioComment`, `MusicTrackComment`). |
| `GET /play/<type>/<id>/comments` | Fil de commentaires. |
| `POST /play/<type>/<id>/comments/<cid>/delete` | Suppression d'un de ses commentaires. |
| `POST /play/<type>/<id>/share` | `to_users_id` multiple ; crée l'entrée chez le destinataire avec statut `New` (`SharedBy` renseigné ; radio en `Shared`). |
| `POST /play/state` | Statut (`Play`/`Pause`/`Stop`) et position, envoyés périodiquement par le navigateur. |

Précédent / Suivant :

- **podcast** : ordre d'affichage des épisodes du podcast ;
- **musique** : `Play.MusicQueue` si le morceau en fait partie, sinon ordre de l'écran Artiste (album par album, numéro de piste) ;
- **radio** : sans objet.

`Play` contient une seule ligne par utilisateur : type, item, URL, statut, position, enceinte Cast, file musique.

### 7.7 Cast — `app/cast/routes.py`, `app/cast_service.py`

| Route | Rôle |
|---|---|
| `GET /cast/devices` | Enceintes (`CastDevice`), « en ligne » si vues depuis moins de 15 min, enceinte active. |
| `POST /cast/select` | Diffuse l'item en cours sur `device_id`, à `position` (sauf radio). Arrête l'enceinte précédente. Renseigne `Play.CastDeviceId`. |
| `POST /cast/stop` | Arrête la diffusion, retour en local. |
| `GET /cast/status` | Position, durée, état de l'enceinte (polling). |
| `POST /cast/playpause` | Pause / reprise sur l'enceinte. |
| `GET/POST /cast/volume` | Lecture / réglage du volume. |

Fonctionnement :

- **Découverte** (job `cast_scan`) : mDNS `_soundtouch._tcp` (libsoundtouch patché pour zeroconf récent, `libsoundtouch_patch.py`) et SSDP (`ssdp:all`) pour les renderers DLNA. Enceintes non vues depuis 24 h supprimées.
- **Diffusion** : toujours en UPnP **AVTransport** (`SetAVTransportURI` + `Play`), y compris pour les SoundTouch ; la Web API SoundTouch (port 8090) ne sert qu'au volume.
- L'enceinte lit `GET /media/cast/<type>/<id>?token=…&start=…` (§7.10). Le serveur interroge l'enceinte toutes les 3 s et **enchaîne lui-même** le morceau suivant en fin de lecture.
- Le contrôle UPnP tourne dans une boucle asyncio dédiée (`run_coro`), démarrée par `cast_service.init_app`.
- Outil de test sans enceinte : `fake_dlna_device.py`, dans le dépôt Tunoova-ESP32 (`Enceinte DLNA fictive/`).

### 7.8 Notifications — `app/notifications/routes.py`

Pas de table dédiée : la liste est calculée à chaque affichage.

| Source | Requête |
|---|---|
| Épisodes | `EpisodesUsers.EpisodeStatus = 'New'` |
| Musique | `MusicUsers.MusicStatus = 'New'` et morceau disponible : partages (un par morceau) et nouveaux MP3 (regroupés par artiste + album) |
| Radios partagées | `RadioUsers.RadioStatus in ('Shared', 'New')` |
| Mise à jour | `UPDATE_ANNOUNCED_VERSION` dans `.env` |

- `count_new()` alimente le badge de la barre de navigation (si `Users.Notifications`).
- `POST /notifications/clear` : `New` → `Unread` (épisodes, musique), suppression des radios `Shared`, `New` → `Read` (radios).
- Les nouveaux MP3 sont notifiés par `music_catalog.notify_new_tracks` (scan), uniquement aux utilisateurs ayant déjà un morceau de l'artiste dans `MusicUsers`.

### 7.9 Profil et Paramètres

| Route | Rôle |
|---|---|
| `GET/POST /userprofile/` | Profil. En htmx : sauvegarde automatique, réponse `_save_status.html`. Email unique ; nom, email, tranche d'âge et pays obligatoires. Photo : `profile_service.save_profile_photo` (png, jpg, gif, webp) dans `db/profile_photos/`. |
| `GET/POST /settings/` | `DarkMode`, `YoutubeMusicEnabled`, `StreamQuality` (`auto`/`low`/`high`, appliqué au format yt-dlp), `Langue`. En htmx : 204. « Vider le cache local » est purement côté client (`localStorage.clear()` + Cache API). |

### 7.10 Médias — `app/media.py`

| Route | Accès | Rôle |
|---|---|---|
| `/media/profile_photo` | session | Photo de l'utilisateur connecté (hors `static/`) |
| `/media/mp3/<track_id>` | session | MP3 local (`send_file`, gère Range) |
| `/media/album_cover/<album_id>` | session | Pochette embarquée dans les tags |
| `/media/ytmusic/<track_id>` | session | Relais du flux YouTube par le serveur, en-têtes Range transmis (l'URL googlevideo est liée à l'IP du serveur) ; nouvelle extraction si l'URL expire |
| `POST /media/ytmusic/<track_id>/warm` | session | Pré-extraction de l'URL du morceau suivant |
| `/media/cast/<type>/<id>` | **jeton signé (6 h) + IP LAN** | Flux pour les enceintes : MP3 direct, ou relais HTTP, ou ffmpeg (YouTube, MP4, reprise à `start`) |

Synthèse des flux audio :

| Contenu | Navigateur | Enceinte (Cast) |
|---|---|---|
| Podcast | URL directe de l'épisode | Relais serveur (ffmpeg si MP4 ou `start`) |
| Radio | URL directe du flux | Relais serveur |
| MP3 local | `/media/mp3/<id>` | `/media/cast/…` (ffmpeg si `start`) |
| YouTube Music | `/media/ytmusic/<id>` (relais) | ffmpeg → AAC ADTS |

## 8. Espace Admin — `app/admin/`

Blueprint `admin` (`/admin`), découpé en modules qui partagent le même `bp`. Toutes les routes sauf `/admin/login` et `/admin/locale/<lang>` portent `@admin_required`.

**Session admin** (`decorators.py`) : cookie signé dédié `tunoova_admin` (chemin `/admin`, HttpOnly, 8 h sans prolongation), contenant une empreinte de l'email + hash admin. Changer le compte admin invalide toutes les sessions. Le cookie est supprimé à toute connexion ou déconnexion d'un utilisateur de l'appli. Blocage anti-bruteforce par IP et par email (`login_throttle.ADMIN_SCOPE`).

### 8.1 Tableau de bord — `routes.py`, `app_settings.py`, `update.py`

| Route | Rôle |
|---|---|
| `GET /admin/` | Tuiles : utilisateurs, taille DB, tables, backups, jobs (prochain), réseau, MP3/artistes/morceaux, podcasts, radios ; formulaires de configuration ; À propos. |
| `POST /admin/settings/smtp` | Réécrit `SMTP_*` (mot de passe inchangé si vide). |
| `POST /admin/settings/mp3-path` | `MP3_LIBRARY_PATH` (le dossier doit exister). |
| `POST /admin/settings/backup` | `BACKUP_PATH` (créé si besoin), `BACKUP_RETENTION_DAYS`. |
| `POST /admin/settings/admin-account` | Email et/ou mot de passe admin (mot de passe actuel requis, règle de robustesse), puis nouveau cookie admin. |
| `POST /admin/settings/purge-logs` | Purge `Log` et/ou `LoginHistory` (plus d'un mois, ou tout). |
| `GET /admin/update/check` | `git fetch` et comparaison du HEAD local et distant ; annonce la version (`UPDATE_ANNOUNCED_VERSION`). |
| `POST /admin/update/run` | Dépose une demande dans `/var/lib/tunoova/request/update`. |
| `GET /admin/update/status` | État et fin du journal de mise à jour. |

**Mécanisme de mise à jour** : l'appli (compte sans droits root) ne lance pas `install.sh`. Un service systemd root `tunoova-update` (installé par `install.sh`) surveille le fichier de demande et exécute `install.sh update` depuis un miroir du dépôt appartenant à root. Indisponible hors installation par `install.sh`.

### 8.2 Comptes utilisateurs — `routes.py`

| Route | Rôle |
|---|---|
| `GET /admin/users` | Liste, recherche (email, nom), tri (nom, email, statut, blocage, 1re connexion, dernière connexion). |
| `POST /admin/users/create` | Crée l'utilisateur (mot de passe aléatoire), envoie le lien d'activation. |
| `POST /admin/users/<id>/reset-password` | Envoie le lien de réinitialisation. |
| `POST /admin/users/<id>/toggle-active` | Active / désactive (`IsActive` ; Flask-Login refuse un compte inactif). |
| `GET/POST /admin/users/<id>/edit` | Email, nom, `FirstLogin`, mot de passe forcé (débloque aussi le compte, aucun email envoyé). |
| `POST /admin/users/<id>/delete` | Supprime l'utilisateur et toutes ses données (abonnements, épisodes, radios, musique, commentaires, état de lecture, journaux). |

### 8.3 Base de données — `db_browser.py`

| Route | Rôle |
|---|---|
| `GET /admin/db` | Tables par catégorie, description, nombre de lignes. |
| `GET /admin/db/<table>` | Lignes paginées par 50, tri, filtres typés (=, ≠, contient, commence par, entre…). |
| `GET/POST /admin/db/<table>/new` | Ajout. |
| `GET/POST /admin/db/<table>/<pk>/edit` | Modification. |
| `POST /admin/db/<table>/<pk>/delete` | Suppression. |

Générique (fonctionne sur toute table de `db.metadata`). Colonnes sensibles (`password|hash|secret|token|apikey`) masquées et non éditables. Les catégories et descriptions viennent de `TABLE_METADATA` ; une nouvelle table tombe dans « Autres ».

### 8.4 Jobs planifiés — `jobs.py`

| Route | Rôle |
|---|---|
| `GET /admin/jobs` | Liste (cron, activé, dernière et prochaine exécution, résultat). |
| `GET /admin/jobs/status` | JSON pour le rafraîchissement sans rechargement. |
| `POST /admin/jobs/<id>/update` | Cron et activation (écrits dans `.env` et appliqués à chaud). |
| `POST /admin/jobs/<id>/run` | Exécution immédiate. |

### 8.5 Plug-ins radio — `radio_plugins.py`

| Route | Rôle |
|---|---|
| `GET /admin/radio-plugins` | Plug-ins (version, clé, statut du dernier test, radios liées) et radios avec leur plug-in et leur ID de station. |
| `POST /admin/radio-plugins/upload` | Installe ou remplace un script (nom `^[a-z][a-z0-9_]*\.py$`, 256 Ko max, fonctions `detect` et `fetch` vérifiées). L'ancienne version va dans `radio_plugins/old/`. |
| `POST /admin/radio-plugins/<id>/update` | Clé d'API, activation. |
| `POST /admin/radio-plugins/<id>/test` | Test sur les radios liées. |
| `POST /admin/radio-plugins/<id>/rollback` | Retour à la version précédente. |
| `POST /admin/radio-plugins/<id>/delete` | Suppression ; les radios liées sont redétectées. |
| `POST /admin/radio-plugins/radio/<id>/test` | Test d'une radio. |

**Contrat d'un plug-in** (`app/now_playing.py`) :

```python
NAME = "Radio France"
def detect(stream_url, name) -> str | None   # ID de station, "" si inutile, None si non géré
def fetch(ref, stream_url, api_key) -> dict | None
# {"kind": "show"|"song", "show": str, "detail": str|None,
#  "image_url": str|None, "start": epoch|None, "end": epoch|None}
```

Un script uploadé est **exécuté sur le serveur** : n'installer que du code de confiance. Résultats en cache 30 s (120 s si vide). Sans plug-in, repli sur les métadonnées ICY du flux (`app/icy.py`). Plug-ins livrés : `radiofrance`, `rtl`, `rmc`, `nrj`, `radioplayer_be`.

Pour créer un nouveau plug-in : voir [Plugins radio/README.md](Plugins%20radio/README.md) (skill Claude `tunoova-radio-plugin`, modèle et banc d'essai).

### 8.6 MP3 — `mp3_browser.py`, `app/mp3_files_service.py`

| Route | Rôle |
|---|---|
| `GET /admin/mp3?path=` | Explorateur du dossier. |
| `POST /admin/mp3/folder`, `/rename`, `/move`, `/delete` (+ `GET /delete-info`) | Opérations sur fichiers et dossiers. |
| `POST /admin/mp3/upload` | Upload par lots (sous `MP3_UPLOAD_MAX_MB`), dossiers compris. |
| `POST /admin/mp3/sync`, `/scan` | Synchronisation du catalogue, scan complet. |
| `GET/POST /admin/mp3/tags` | Lecture / écriture des tags ID3 d'un fichier ou d'un dossier. |

Tous les chemins passent par `_safe_path()` (refus de `..`, chemins absolus, liens symboliques sortants). Un renommage ou déplacement met à jour `MusicTrack.SourceRef` : le morceau garde écoutes, likes et commentaires.

### 8.7 Backup — `backups.py`, `app/backup_service.py`

| Route | Rôle |
|---|---|
| `GET /admin/backups` | Liste (planifié/manuel, avant mise à jour, avant restauration). |
| `POST /admin/backups/create` | Backup immédiat. |
| `GET /admin/backups/download/<name>` | Fichier brut. |
| `GET /admin/backups/bundle/<name>` | Zip base + `profile_photos/`. |
| `POST /admin/backups/restore` | Restaure un zip : validation (une seule `.db`, base Tunoova valide, 2 Go max décompressés), backup `pre_restore` de l'état actuel, remplacement, puis redémarrage (sortie en erreur, relancé par systemd). |

Copie cohérente par `sqlite3.Connection.backup()`. Fichiers : `tunoova_[pre_update_|pre_restore_]AAAAMMJJ_HHMMSS.db` et `tunoova_photos_….zip`. Rétention `BACKUP_RETENTION_DAYS` pour tous les types.

### 8.8 Réseau — `remote_access.py`, `app/remote_access.py`, `app/tailscale_service.py`

| Route | Rôle |
|---|---|
| `GET /admin/network` | Mode, état Tailscale, URL publique, QR code. |
| `GET /admin/network/status` | Polling de l'état Tailscale (connexion, autorisation Funnel). |
| `POST /admin/network/mode` | Applique le mode et `TRUSTED_PROXY_COUNT`. |

| Mode | `TRUSTED_PROXY_COUNT` | Mise en œuvre |
|---|---|---|
| `off` | 0 | Accès direct |
| `external` | 1 | Reverse proxy de l'utilisateur, `PUBLIC_HOSTNAME` |
| `private` | 1 | `tailscale serve` |
| `public` | 1 | `tailscale funnel` |

L'appli pilote la commande `tailscale` (sans shell), autorisée par `tailscale set --operator=tunoova`. Pas d'API web Tailscale.

En mode `external`, `private` ou `public`, `base.html` déclare le manifeste `/manifest.webmanifest` (route `main.manifest`, icônes `brand/favicon-192.png` et `brand/app-icon-512.png`) : Chrome affiche alors l'icône **Installer** dans la barre d'adresse. Il n'est pas déclaré en mode `off`, car Chrome n'installe une appli qu'en HTTPS.

## 9. Base de données

### 9.1 Vue d'ensemble

SQLite, 23 tables. Le détail des colonnes et le diagramme des relations sont dans [DB Structure.fr.md](<DB Structure.fr.md>), généré par :

```bash
.venv/bin/python scripts/gen_db_doc.py          # régénère
.venv/bin/python scripts/gen_db_doc.py --check  # code retour 1 si la doc n'est plus à jour
```

| Domaine | Tables |
|---|---|
| Comptes | `Users` |
| Référentiels | `Langues`, `AgeRange`, `Hobby`, `MusicGenre`, `Country` |
| Podcasts | `Podcast`, `PodcastUsers`, `EpisodesUsers`, `PodcastComment` |
| Radios | `Radio`, `RadioUsers`, `RadioComment`, `RadioPlugIn` |
| Musique | `MusicArtist`, `MusicAlbum`, `MusicTrack`, `MusicUsers`, `MusicTrackComment` |
| Lecture et activité | `Play`, `CastDevice`, `Log`, `LoginHistory` |

### 9.2 Conventions

- **Catalogue partagé + table par utilisateur** : `Podcast`/`PodcastUsers`, `Radio`/`RadioUsers`, `MusicTrack`/`MusicUsers`. Le catalogue porte les compteurs globaux (likes, dislikes, commentaires, écoutes) ; la table utilisateur porte le vote personnel, le compteur d'écoutes personnel et le statut.
- `PodcastUsers` et `RadioUsers` recopient nom, URL et image : chaque utilisateur peut les renommer sans toucher au catalogue.
- **Statuts** :

  | Colonne | Valeurs |
  |---|---|
  | `EpisodesUsers.EpisodeStatus` | `New` (notifié), `Unread` (lu ou notification effacée). `Read` est prévu dans le modèle mais jamais écrit. |
  | `MusicUsers.MusicStatus` | `New`, `Unread`, NULL |
  | `RadioUsers.RadioStatus` | `Shared` (partage pas encore accepté, hors liste), `New` (partage d'une radio déjà en liste), `Read`, NULL |
  | `Play.PlayStatus` | `Play`, `Pause`, `Stop` |
  | `*Like` (utilisateur) | `Like`, `Not Like`, NULL |

- Listes multi-choix (`Users.Hobby`, `Users.MusicGenre`) stockées en texte séparé par des virgules ; libellés traduits par `translate_choice` (clés `hobby_*`, `genre_*`, `age_range_*`, `country_*`).
- `Users.SortPrefs` (JSON) : dernier tri par écran (`app/sort_prefs.py`) et ordre de l'Accueil.
- Dates en UTC (`datetime.utcnow`).
- Clés primaires entières auto-incrémentées.

### 9.3 Évolution du schéma

Pas d'Alembic. À chaque démarrage :

1. `db.create_all()` crée les tables manquantes ;
2. `ensure_schema_upgrades()` (`db/seed.py`) ajoute les colonnes manquantes par `ALTER TABLE … ADD COLUMN`, de façon idempotente (vérification par `inspect`) ;
3. `seed_reference_data()` remplit les référentiels vides.

Ce code s'exécute au démarrage, pas seulement lors d'une mise à jour : `install.sh` initialise ou met à jour la base par un simple `python -c "import run"`, et un `git pull` suivi d'un redémarrage suffit à appliquer les ajouts.

Ce qui est automatique :

- **nouvelle table** : il suffit d'ajouter la classe dans `app/models.py` ;
- **nouvelle colonne sur une table existante** : à condition d'ajouter le bloc correspondant dans `ensure_schema_upgrades()`, sur ce modèle :

  ```python
  if "Country" not in columns:
      db.session.execute(text("ALTER TABLE Users ADD COLUMN Country VARCHAR(80)"))
      db.session.commit()
  ```

- **nouvelles données de référence** : `seed_reference_data()` n'insère que ce qui manque.

Ce qui doit être codé à la main dans `ensure_schema_upgrades()` et testé **avant** la mise à jour en production :

- un renommage ou une suppression de colonne ou de table ;
- un changement de type, ou le passage d'une colonne en `NOT NULL` ;
- toute migration de données.

SQLite ne gère guère que `ADD COLUMN` en `ALTER TABLE`. Pour le reste, il faut recréer la table :
1. créer une table temporaire ;
2. y copier les données ;
3. supprimer l'ancienne table ;
4. renommer la table temporaire.

Toujours tester l'état via `inspect(db.engine)` avant d'agir, pour que la fonction reste rejouable à chaque démarrage.

Procédure pour un changement de schéma :

1. modifier `app/models.py` ;
2. ajouter si besoin le bloc dans `ensure_schema_upgrades()` (voir ci-dessus) ;
3. relancer `scripts/gen_db_doc.py`, et mettre à jour la version anglaise `DB Structure.md`.

En cas de problème, `install.sh update` sauvegarde la base avant chaque mise à jour : voir [Installation.fr.md § Revenir en arrière](Installation.fr.md#45-revenir-en-arrière-après-une-mise-à-jour).

## 10. Services (couche métier)

| Module | Rôle |
|---|---|
| `podcast_service` | Recherche iTunes, catalogue, abonnement, synchro RSS, ordre des séries, partage d'épisode |
| `radio_service` | Recherche Radio Browser, catalogue, favoris, partage |
| `now_playing`, `icy` | Plug-ins « titre en cours », repli ICY |
| `music_catalog` | Création/dédoublonnage artistes, albums, morceaux ; notifications nouveaux MP3 ; purge ; images |
| `mp3_library_service` | Scan de `MP3_LIBRARY_PATH` (tags mutagen, détection des modifications par mtime/taille, masquage/réapparition) |
| `mp3_files_service` | Explorateur de fichiers Admin |
| `cover_service` | Pochettes et photos (Deezer, iTunes), sans accès base |
| `youtube_music_service` | ytmusicapi (recherche, artistes, albums, top) ; yt-dlp (URL du flux, cache mémoire 30 min) |
| `play_service` | Contexte de lecture, likes, commentaires, file, état `Play`, partage, URL Cast |
| `cast_service` | Découverte et contrôle des enceintes |
| `backup_service`, `update_service` | Sauvegarde/restauration, mise à jour |
| `remote_access`, `tailscale_service` | Mode d'accès, middlewares proxy/cookie, pilotage Tailscale |
| `login_throttle`, `tokens`, `logging_util` | Anti-bruteforce, jetons signés, journaux |
| `profile_service`, `sort_prefs`, `email_utils`, `i18n` | Photo et référentiels, tris mémorisés, SMTP, traductions |

## 11. Jobs planifiés — `app/scheduler.py`

| Job | Cron par défaut | Fonction |
|---|---|---|
| `mp3_scan` | `*/15 * * * *` | `scan_library(force=True, resolve_images=True)` + notification des nouveaux MP3 |
| `music_purge` | `0 4 1 * *` | Suppression des morceaux masqués depuis plus de 30 jours |
| `rss_poll` | `*/30 * * * *` | `sync_episodes(mark_new=True)` pour tous les abonnements |
| `db_backup` | `0 3 * * *` | Backup base + photos, nettoyage selon la rétention |
| `cast_scan` | `*/5 * * * *` | Découverte des enceintes |
| `log_purge` | `30 4 * * *` | Suppression des `Log` et `LoginHistory` de plus de 30 jours |

Statut (dernière exécution, résultat, erreur, en cours) gardé **en mémoire** : perdu au redémarrage. Un job en échec est journalisé sans arrêter le scheduler.

Ajouter un job : une fonction `_run_<id>()` qui renvoie `(clé_de_traduction, paramètres)`, une entrée dans `JOBS`, les clés `JOB_<ID>_CRON/ENABLED` dans `.env.example`, les libellés dans `en.json` et `fr.json`.

## 12. Intégrations externes

| Service | Usage | Authentification | Module |
|---|---|---|---|
| Apple iTunes Search | Recherche de podcasts ; pochettes en secours | aucune | `podcast_service`, `cover_service` |
| Radio Browser | Recherche de radios | aucune | `radio_service` |
| Flux RSS | Épisodes | aucune | `podcast_service` |
| Deezer API | Pochettes et photos d'artistes | aucune | `cover_service` |
| YouTube Music | Recherche (ytmusicapi), flux (yt-dlp + deno) | aucune | `youtube_music_service` |
| APIs des radios | Titre en cours (Radio France, RTL, RMC, NRJ, Radioplayer BE) | aucune (clé possible par plug-in) | `radio_plugins/` |
| SMTP | Emails d'activation, de réinitialisation, demandes à l'admin | `SMTP_*` | `email_utils` |
| GitHub | Vérification et téléchargement des mises à jour | anonyme ou deploy key | `update_service`, `install.sh` |
| Tailscale | Accès à distance | compte Tailscale de l'admin | `tailscale_service` |

Ces services ne sont pas contractuels (APIs non documentées pour la plupart des plug-ins, YouTube). Prévoir des pannes : tous les appels sont « best-effort » avec timeout et l'écran s'affiche sans le résultat.

## 13. Internationalisation

- Fichiers `app/translations/en.json` et `fr.json` (clés plates), chargés une fois au démarrage.
- Dans les templates : `t('clé', param=…)`. En Python : `translate()`.
- Langue retenue (`current_locale`) :
  1. sur `/admin/*`, choix FR/EN de l'admin (`session["admin_locale"]`) ;
  2. `Users.Langue` si connecté ;
  3. préférence en session ;
  4. `Accept-Language` du navigateur, sinon anglais.
- Les messages `flash` de quelques routes (Podcast, Radio) sont encore écrits en français en dur.

## 14. Sécurité

| Sujet | Mise en œuvre |
|---|---|
| Mots de passe | bcrypt ; règle commune appli/admin/install.sh (`admin/password_policy.py`) |
| Anti-bruteforce | 5 échecs → 15 min, par compte (`Users.LockedUntil`) et par IP (`login_throttle`, calculé sur `LoginHistory` des 24 dernières heures) ; admin : par IP et par email |
| Demandes à l'admin | Champ piège + quotas IP/email/global ; réponse identique (pas d'énumération d'emails) |
| Session utilisateur | Cookie remember 30 jours glissants, HttpOnly, SameSite=Lax ; session vidée à chaque connexion/déconnexion |
| Session admin | Cookie séparé, chemin `/admin`, 8 h, invalidé si le compte admin change |
| Cookie `Secure` | Posé requête par requête (`SecureCookieMiddleware`) : en HTTPS/proxy oui, en HTTP local non |
| IP réelle | `TrustedProxyMiddleware` : `X-Forwarded-*` lus seulement si la requête vient d'un proxy de confiance (`TRUSTED_PROXY_COUNT`) |
| CSRF | Flask-WTF sur tous les POST |
| Jetons | itsdangerous : activation 24 h, réinitialisation 1 h liée au hash (usage unique), Cast 6 h lié au type et à l'item |
| En-têtes | `X-Content-Type-Options: nosniff`, `X-Frame-Options: DENY`, `Referrer-Policy`, CSP minimale (`frame-ancestors`, `object-src`, `base-uri`, `form-action`) ; pas de CSP `script-src` (scripts inline) |
| Cache | `private` partout, `no-store` sur HTML/JSON/téléchargements |
| Fichiers | Photos hors `static/` ; chemins MP3 confinés (`_safe_path`) ; redirections limitées aux URL internes (`safe_local_url`) |
| Admin DB | Colonnes sensibles masquées |
| Plug-ins | Code exécuté sur le serveur : réservé à l'admin, scripts de confiance uniquement |
| Production | waitress, `FLASK_DEBUG=0` forcé, compte système dédié, `.env` en 600 |
| Audit | `scripts/audit.sh` (pip-audit, bandit, semgrep, gitleaks, ZAP…) ; procédure dans [Audit Tunoova](Audit%20Tunoova.md) |

## 15. Journalisation et exploitation

| Où | Quoi |
|---|---|
| Table `Log` | Un enregistrement par écran affiché : utilisateur, `Frame`, date, appareil, IP, OS |
| Table `LoginHistory` | Événements : `success`, `invalid_user`, `invalid_password`, `user_locked`, `ip_locked`, `cookie_resume`, `logout`, `admin_success`, `admin_invalid`, `admin_locked`, `account_request`, `reset_request`, `request_blocked` |
| journald | Journal de l'appli : `sudo journalctl -u tunoova -n 100 --no-pager` |
| `/var/log/tunoova-update.log` | Mises à jour lancées depuis l'Admin |

Commandes utiles :

```bash
sudo systemctl status tunoova
sudo systemctl restart tunoova
sudo ./install.sh update        # mise à jour complète (backup, pip, .env, droits)
sudo ./install.sh reset         # nouveau mot de passe admin
.venv/bin/pip install -U yt-dlp # si la lecture YouTube cesse de fonctionner
```

Installation, mise à jour, sauvegarde et restauration manuelle : [Installation.fr.md](Installation.fr.md).

## 16. Développement

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
cp .env.example .env            # renseigner SESSION_SECRET_KEY, ADMIN_EMAIL, ADMIN_PASSWORD_HASH
FLASK_DEBUG=1 .venv/bin/python run.py -p 5000
```

- Pas de suite de tests automatisés dans le dépôt.
- Publier une version : changer `APP_VERSION` et `APP_RELEASE_DATE` dans `app/version.py` (c'est ce que compare le bouton Mise à jour).
- Nouvelle clé de configuration : l'ajouter à `.env.example` (fusionnée dans `.env` par `install.sh update`).
- Nouveau texte d'interface : clé dans `en.json` **et** `fr.json`.
- Documentation : README, Installation, Documentation technique et DB Structure existent en anglais (`*.md`, `Technical documentation.md`) et en français (`*.fr.md`, `Documentation technique.md`) ; tenir les deux à jour. Captures : `Doc/Captures/` (français) et `Doc/Captures/en/` (anglais), mêmes noms de fichiers.

## 17. Points d'attention

- **Processus unique** : scheduler, caches et boucle Cast en mémoire ; pas de multi-workers.
- **SQLite** : suffisant pour un usage familial ; écritures sérialisées. Les flux audio longs occupent des threads waitress (32).
- **yt-dlp** casse régulièrement face aux changements de YouTube : le mettre à jour en priorité en cas de panne.
- **Statut des jobs** perdu au redémarrage.
- **Flash en dur** en français dans `podcast/routes.py` et `radio/routes.py`.
- Valeurs `EpisodeStatus = 'Read'` jamais utilisées.
