# Tunoova — Technical documentation

🇬🇧 **English** · 🇫🇷 [Français](<Documentation technique.md>)

Related documents:

| Document | Content |
| --- | --- |
| [README (functional)](../README.md) | What the app does, screen by screen, from the user's point of view |
| [DB Structure](<DB Structure.md>) | Every table and column (generated from `app/models.py`) |
| [Installation](Installation.md) | `install.sh`: installation, first-time setup, updates, admin reset, useful commands |
| [Audit Tunoova](Audit%20Tunoova.md) | Running a security audit (`scripts/audit.sh`) and handling its results (in French) |

---

## 1. Architecture

```
 Browser (htmx + app.js)                 LAN speakers (SoundTouch / DLNA)
        │  partial HTML, JSON, audio              ▲  HTTP stream /media/cast/...
        ▼                                         │  (signed token, LAN only)
 ┌───────────────────────────────────────────────────────────────┐
 │ waitress (32 threads)                                         │
 │  TrustedProxyMiddleware → SecureCookieMiddleware → Flask app  │
 │   ├─ blueprints : auth, main, podcast, radio, library, play,  │
 │   │               cast, notifications, settings, userprofile, │
 │   │               media, admin                                │
 │   ├─ services   : *_service.py, music_catalog, now_playing…   │
 │   ├─ APScheduler (thread) : 6 cron jobs                       │
 │   └─ asyncio loop (thread) : UPnP/DLNA control for Cast       │
 └───────────────┬───────────────────────────────┬───────────────┘
                 ▼                               ▼
        SQLite db/tunoova.db          External APIs (iTunes, Radio Browser,
        db/profile_photos/            Deezer, YouTube Music, radio plug-ins)
        MP3_LIBRARY_PATH              ffmpeg / yt-dlp / deno / tailscale
```

Principles:

- **Server-rendered Flask monolith**, with no separate REST API and no SPA framework. Navigation goes through **htmx** (`hx-boost` on `<body>`): only the `#app-boost-root` area is swapped, the `<audio>` player and the mini-player stay in the page, and playback is never interrupted.
- **A single process**: the scheduler, the Cast asyncio loop and the in-memory caches (YouTube URLs, radio titles, job status) live in this process. **Do not run several workers**: jobs would run twice and caches would diverge.
- **SQLite** as the only storage, with no migration tool (see §9.3).
- **`.env` also serves as editable configuration storage**: the Admin area rewrites it (`dotenv.set_key`).

## 2. Stack and dependencies

### Python (`requirements.txt`)

| Package | Use |
|---|---|
| Flask, Flask-SQLAlchemy, Flask-Login, Flask-WTF | Web, ORM, user sessions, CSRF |
| bcrypt | Password hashing (users and admin) |
| python-dotenv | Reading and rewriting `.env` |
| APScheduler | Scheduled jobs (crontab syntax) |
| user-agents | Device and OS in `Log` / `LoginHistory` |
| requests, feedparser | HTTP calls, RSS feed parsing |
| mutagen | Reading and writing ID3 tags |
| ytmusicapi, yt-dlp[default] | YouTube Music search, audio stream extraction |
| libsoundtouch, zeroconf, async-upnp-client, aiohttp | Speaker discovery and control (Cast) |
| segno | QR code for the public address (Admin > Network) |
| waitress | Production WSGI server |
| Werkzeug, urllib3, multidict (floors) | Minimum security versions of indirect dependencies |

### System (installed by `install.sh`)

| Tool | Use |
|---|---|
| python3, venv, pip | Runtime |
| git, openssh-client | Updates from GitHub (deploy key if the repository is private) |
| ffmpeg | On-the-fly remuxing for Cast (YouTube MP4/DASH streams, resume at a position) |
| deno | JS runtime used by yt-dlp for YouTube challenges |
| zip, unzip, curl | Backups, deno installation |
| tailscale | Remote access (optional, Admin > Network) |

### Front end

- `app/static/js/vendor/htmx.min.js` (the only JS library).
- `app/static/js/app.js` (~1,850 lines, vanilla JS): player, Now Playing panel, Cast, preloading, Media Session.
- `app/static/css/app.css`: light/dark theme via `data-theme` on `<html>`.
- Lucide icons (SVG inlined by `app/icons.py`, recolorable in CSS).

## 3. Directory layout

```
run.py                  Entry point: create_app, DB init/migration, server
install.sh              Install / update / admin reset (root)
requirements.txt
.env.example            Configuration template (merged into .env on each update)
app/
  __init__.py           create_app: extensions, context processors, headers, blueprints
  config.py             Config class (reads environment variables)
  models.py             SQLAlchemy models (schema source of truth)
  version.py            APP_VERSION, APP_RELEASE_DATE…  (change on each release)
  <blueprint>/routes.py auth, main, podcast, radio, library, play, cast,
                        notifications, settings, userprofile, admin/*
  media.py              /media blueprint: audio streams, covers, profile photo
  *_service.py          Business logic (see §10)
  templates/            Jinja2, one folder per blueprint
  translations/         en.json, fr.json
  static/               css, js, icons, brand images
radio_plugins/          "Now on air" plug-ins (one script per provider)
db/
  seed.py               ensure_schema_upgrades() + seed_reference_data()
  tunoova.db            Database (not versioned)
  profile_photos/       Profile photos (not versioned, outside static)
  backups/              Backups (not versioned)
mp3/                    Default MP3 library (not versioned)
scripts/                audit.sh (versioned); gen_db_doc.py (not versioned)
Doc/
```

## 4. Startup

`run.py`, at module level (so also on `import run`):

1. `load_dotenv()` then `create_app()`:
   - extensions (SQLAlchemy, LoginManager, CSRFProtect, i18n);
   - Jinja globals: `cover_for`, `icon`, `time_ago`, `short_count`, `t`;
   - context processors injected into **every** render: `notifications_count` (badge), `current_play` (mini-player rendered server-side from the `Play` table), `other_users` (share form), `pwa_installable` (app manifest, see §8.8);
   - `after_request`: `Cache-Control: private` (+ `no-store` for HTML/JSON/downloads) and security headers (§14);
   - `user_loaded_from_cookie` signal: records a session resume in `LoginHistory` (once per session);
   - registration of the 12 blueprints;
   - `scheduler.init_app` (starts APScheduler) and `cast_service.init_app` (asyncio loop);
   - WSGI middlewares `TrustedProxyMiddleware(SecureCookieMiddleware(app))`.
2. `db.create_all()`, `ensure_schema_upgrades()`, `seed_reference_data()`.
3. When run directly:
   - `FLASK_DEBUG=0` (production): **waitress**, `threads=32` (each audio stream holds a thread), `channel_timeout=900` (paused player), no `Server` header;
   - `FLASK_DEBUG=1` (dev): Werkzeug server with the `stat` reloader (does not watch `.env`, which the admin rewrites). In debug, the scheduler only starts in the reloader's child process.

In production, the `tunoova` systemd service runs `.venv/bin/python run.py -p <port>` under the `tunoova` system account, with `Restart=on-failure` (used to restart after a restore).

## 5. Configuration (`.env`)

| Key | Default | Changed by | Role |
|---|---|---|---|
| `SESSION_SECRET_KEY` | — (required) | install.sh | Signs sessions, tokens, admin cookie |
| `ADMIN_EMAIL`, `ADMIN_PASSWORD_HASH` | — | install.sh, Admin > Dashboard, `install.sh reset` | Admin account (outside the database) |
| `DATABASE_PATH` | `db/tunoova.db` | — | SQLite database |
| `MP3_LIBRARY_PATH` | `./mp3` | Admin > Dashboard | Library root |
| `MP3_UPLOAD_MAX_MB` | 200 | — | Max request size (`MAX_CONTENT_LENGTH`): MP3 upload batch, restore archive |
| `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASSWORD`, `SMTP_FROM` | port 587 | Admin > Dashboard | Sending emails |
| `JOB_<NAME>_CRON`, `JOB_<NAME>_ENABLED` | see §11 | Admin > Scheduled jobs | Scheduling of the 6 jobs |
| `BACKUP_PATH`, `BACKUP_RETENTION_DAYS` | `db/backups`, 7 | Admin > Dashboard | Backups |
| `CAST_BASE_URL` | empty | — | Base URL of Cast streams if the detected IP is not reachable by the speakers |
| `REMOTE_ACCESS_MODE` | empty | Admin > Network | `off`, `external`, `private`, `public` |
| `PUBLIC_HOSTNAME` | empty | Admin > Network | DNS name in `external` mode |
| `TRUSTED_PROXY_COUNT` | 0 | Admin > Network | Number of trusted reverse proxies |
| `UPDATE_ANNOUNCED_VERSION` | — | Admin (update check) | Version announced to users |
| `FLASK_DEBUG` | 0 | install.sh (forces 0) | Development mode |
| `MP3_SCAN_INTERVAL_MINUTES`, `PODCASTINDEX_API_KEY`, `PODCASTINDEX_API_SECRET` | — | — | **Unused** in the code |

Configuration outside `.env` (set up by `install.sh`): `/etc/tunoova/update.conf`, deploy key and `known_hosts` in `/etc/tunoova/`, update state in `/var/lib/tunoova/`, log in `/var/log/tunoova-update.log`.

## 6. Front end

### Navigation and persistent player

- `<body hx-boost="true" hx-target="#app-boost-root" hx-select="#app-boost-root">`: every internal link loads the full page server-side but only swaps the content area.
- The `<audio>` element, the mini-player, the `#play-overlay` panel (Now Playing screen) and the Cast dialog are **outside** this area. The Now Playing panel opens over the page, fed by `GET /play/<type>/<id>/data` (JSON), without navigation.
- The browser Back button (htmx history restore) does not honor `hx-preserve`: the mini-player is therefore also rendered server-side on every page (`inject_current_play`), and `app.js` overrides `popstate`.
- Lists (live search, sort) are htmx fragments: `GET /<screen>/list?q=…&sort=…`.

### `app.js` — responsibilities

| Area | Main functions |
|---|---|
| Playback | `tunoovaStartPlayback`, `tunoovaTogglePlayback`, `tunoovaSkipTrack`, `tunoovaPlayNextAfterEnded`, resume (`tunoovaSyncFromServer`, `GET /play/current`) |
| Server state | `reportState` → `POST /play/state` (status + position, periodic) |
| Preloading | `tunoovaPreloadNext`: downloads the next track as a Blob (`?prefetch=1`), `tunoovaWarmStream` → `POST /media/ytmusic/<id>/warm` |
| Now Playing panel | `tunoovaShowPlayOverlay`, `tunoovaFillPlayOverlay`, like, comments, share |
| Radio | `tunoovaRefreshRadioNowPlaying` → `GET /radio/<id>/now-playing` (periodic) |
| Cast | dialog, list, selection, volume, polling `GET /cast/status` every ~3 s |
| Media Session | metadata, `playbackState`, `setPositionState`, play/pause/next/previous actions (lock screen) |
| CSRF | token read from `<meta name="csrf-token">`, sent with every `fetch` POST (`WTF_CSRF_TIME_LIMIT=None` because the page is never reloaded) |

Convention: routes called with `fetch` return JSON when the `X-Requested-With: XMLHttpRequest` header is present, otherwise a redirect (no-JS fallback).

### Theme and language

- `data-theme="dark|light"` on `<html>` from `Users.DarkMode`.
- Language: see §13.

## 7. Pages and routes

All app routes require `@login_required` unless stated otherwise. `log_screen_view(<Frame>)` writes a row to `Log` whenever a screen is displayed.

### 7.1 Authentication — `app/auth/routes.py`

| Route | Methods | Template | Role |
|---|---|---|---|
| `/signin` | GET, POST | `auth/signin.html` | Sign-in. Checks the IP lock (`login_throttle`), the account lock (`Users.LockedUntil`), the bcrypt password. On success: failures reset, `LastLogin`, fresh session, `login_user(remember=True)`, admin cookie removed, redirect to `firstlogin` or Home. Every outcome is recorded in `LoginHistory`. Public. |
| `/signin/request-account` | POST | `auth/_request_sent.html` | Account request: email to the admin (`notify_admin`). Honeypot field `website` + limits (3/h per IP, 1/24 h per email, 20/24 h overall). Same response if the request is blocked. Public. |
| `/signin/request-reset` | POST | same | Reset request, same protections; same response whether or not the account exists. Public. |
| `/activate/<token>` | GET, POST | `auth/set_password.html` | Choose the initial password (24 h token). Public. |
| `/reset-password/<token>` | GET, POST | same | New password (1 h token, bound to the current hash: single use). Public. |
| `/firstlogin` | GET, POST | `auth/firstlogin.html` | Initial profile. Name, age range and country required. `language_only=1`: changes the language without submitting. Sets `FirstLogin` to false. |
| `/logout` | GET | — | Records `logout`, clears the session, removes the remember and admin cookies. |
| `/legal/terms`, `/legal/privacy` | GET | `auth/terms.html`, `auth/privacy.html` | Legal texts (FR/EN). Public. |

### 7.2 Home — `app/main/routes.py`

| Route | Role |
|---|---|
| `GET /` | Redirects to `firstlogin` if needed. Top 5 `PodcastUsers` and top 5 `RadioUsers` by `CountListen`; distinct artists of the 10 most-played `MusicUsers` (`CountListen > 0`, track available). Section order read from `Users.SortPrefs["home"]`. |
| `POST /home/order` | Saves the section order (`podcast`, `radio`, `music`). |
| `GET /manifest.webmanifest` | Web app manifest (Chrome install). Public: Chrome fetches it without cookies. |

### 7.3 Podcast — `app/podcast/routes.py`, `app/podcast_service.py`

| Route | Role |
|---|---|
| `GET /podcast/` | Subscription list (sort `newest`/`oldest` by latest episode date, `az`/`za`). With `q` (≥ 3 chars): podcasts whose name matches, podcasts with a matching episode, and iTunes results (country FR or US depending on the language). `?added=<id>` scrolls to the added podcast. |
| `GET /podcast/list` | htmx fragment of the same list. |
| `GET /podcast/<id>` | Details: episodes paginated by 20, filter applied at display time, "[1/2]" series order (`order_episodes`). |
| `GET /podcast/<id>/episodes?offset=` | Next page (infinite scroll). |
| `POST /podcast/<id>/refresh` | Immediate `sync_episodes`. |
| `POST /podcast/add` | Subscribe from an iTunes result; first import without `New` status. |
| `GET/POST /podcast/add/advanced` | Manual add (title, RSS, image, filter). |
| `GET/POST /podcast/<id>/edit` | Edit. |
| `POST /podcast/<id>/delete` | Unsubscribe (deletes `PodcastUsers` and its `EpisodesUsers`). |
| `POST /podcast/episode/<id>/play` | `New` → `Unread`, `CountListen`+1, `set_current_play`, returns the playback JSON (direct episode URL). |

Rules:

- `Podcast` is the shared catalog (unique by `LinkRSS`), `PodcastUsers` a user's subscription, `EpisodesUsers` their episodes.
- `sync_episodes` reads at most 50 feed entries, adds unknown URLs, applies the `max:/contain:/exclude:` filter and marks `New` except on the first import.

### 7.4 Radio — `app/radio/routes.py`, `app/radio_service.py`, `app/now_playing.py`

| Route | Role |
|---|---|
| `GET /radio/` | List (`newest`/`oldest` by `LastListen`, `az`/`za`). With `q`: local filter + Radio Browser (profile country first via `Country.ApiName`). Shared stations not yet accepted (`RadioStatus=Shared`) are excluded (`user_radios`). |
| `GET /radio/list` | htmx fragment. |
| `POST /radio/add/play?name=&stream_url=&image_url=` | Click on a search result: add to favorites + play. |
| `GET/POST /radio/add/advanced` | Manual add. |
| `GET/POST /radio/<id>/edit` | Edit. |
| `POST /radio/<id>/delete` | Remove the favorite. |
| `POST /radio/<id>/play` | `CountListen`+1, `Radio.RadioCount`+1, `Shared/New` → `Read`, `set_current_play`, playback JSON (direct stream URL). |
| `GET /radio/<id>/now-playing` | What is on air (plug-in or ICY), polled periodically by the mini-player. |

Radio Browser: queried across several mirrors with fallback, favicons checked in parallel (24 workers, 6 h cache).

### 7.5 Music — `app/library/routes.py`, `app/music_catalog.py`, `app/youtube_music_service.py`

| Route | Role |
|---|---|
| `GET /library/` | Music screen. Sorts `artist_az/za`, `album_az/za`, `favorites_az/za`. Search (≥ 3 chars) on local MP3s with cascading display (album / artist / tracks), plus a YouTube Music section (`search_smart`) if `YoutubeMusicEnabled`. Remembers search and sort in the session for the Back buttons. |
| `GET /library/list` | htmx fragment. |
| `GET /library/artist/<id>` | An artist's albums (`?fav=1`: only what the user has played). |
| `GET /library/artist/<id>/others` | The artist's YouTube albums missing from the catalog. |
| `GET /library/artist/top?name=` | "Most Popular" (YouTube); falls back to the artist screen if YouTube is off or returns nothing. |
| `POST /library/artist/top/play` | Starts the "most popular" queue (`Play.MusicQueue`). |
| `GET /library/album/<id>` | An album's tracks (`?fav=1`). |
| `POST /library/album/<id>/play` | Plays the album's first track. |
| `GET /library/youtube/artist`, `GET /library/youtube/album` | Transient preview of a YouTube artist or album (nothing is saved). |
| `GET /library/youtube/album/import` | Imports all tracks of a YouTube album into the catalog. |
| `GET/POST /library/youtube/track/add` | Adds a YouTube track to the catalog and plays it. |
| `POST /library/<track_id>/play` | Plays a track (`?queue=`: explicit queue). Creates `MusicUsers` if needed, counts the play. |

Catalog rules:

- `MusicArtist` → `MusicAlbum` → `MusicTrack`, shared by everyone and by both sources (`Source = local | youtube`). Names are deduplicated by a normalized key (`NameKey`, `TitleKey`). Compilations are attached to "Various Artists", each track keeping its performer.
- `MusicTrack.IsAvailable=False` + `MissingSince`: MP3 gone, hidden everywhere, purged after 30 days (`music_purge` job).
- `MusicTrack.FromTop=True`: track added only through "Most Popular"; its album does not appear in the library.
- `MusicUsers` only holds the tracks linked to the user (play, like, share, new).
- Images: `cover_service` searches **Deezer**, then **iTunes** as a fallback for covers; URLs stored in `MusicAlbum.CoverUrl` / `MusicArtist.PictureUrl`, retried after 7 days (`CheckedAt`). A cover embedded in the MP3 is served by `/media/album_cover/<id>`. At display time, 6 lookups at most; the `mp3_scan` job does the rest.

### 7.6 Playback — `app/play/routes.py`, `app/play_service.py`

`<type>` ∈ `podcast` (id = `EpisodesUsersId`), `radio` (id = `RadioUsersId`), `music` (id = `TrackId`).

| Route | Role |
|---|---|
| `GET /play/current` | JSON of the current playback (multi-device resync). |
| `GET /play/<type>/<id>` | Full page (no-JS fallback); `set_current_play`. |
| `GET /play/<type>/<id>/data` | Now Playing panel JSON: stream, titles, image, counters, previous/next, Cast state. `?prefetch=1`: no side effects (preloading). |
| `POST /play/<type>/<id>/like` | `value=Like|Not Like`; updates the personal vote and the catalog counters (`apply_like`). |
| `POST /play/<type>/<id>/comment` | Adds a comment to the thread (`PodcastComment`, `RadioComment`, `MusicTrackComment`). |
| `GET /play/<type>/<id>/comments` | Comment thread. |
| `POST /play/<type>/<id>/comments/<cid>/delete` | Deletes one of your comments. |
| `POST /play/<type>/<id>/share` | Multiple `to_users_id`; creates the entry for the recipient with `New` status (`SharedBy` set; radio as `Shared`). |
| `POST /play/state` | Status (`Play`/`Pause`/`Stop`) and position, sent periodically by the browser. |

Previous / Next:

- **podcast**: display order of the podcast's episodes;
- **music**: `Play.MusicQueue` if the track is in it, otherwise the Artist screen order (album by album, track number);
- **radio**: not applicable.

`Play` has a single row per user: type, item, URL, status, position, Cast speaker, music queue.

### 7.7 Cast — `app/cast/routes.py`, `app/cast_service.py`

| Route | Role |
|---|---|
| `GET /cast/devices` | Speakers (`CastDevice`), "online" if seen less than 15 min ago, active speaker. |
| `POST /cast/select` | Casts the current item to `device_id`, at `position` (except radio). Stops the previous speaker. Sets `Play.CastDeviceId`. |
| `POST /cast/stop` | Stops casting, back to local playback. |
| `GET /cast/status` | Position, duration, speaker state (polling). |
| `POST /cast/playpause` | Pause / resume on the speaker. |
| `GET/POST /cast/volume` | Read / set the volume. |

How it works:

- **Discovery** (`cast_scan` job): mDNS `_soundtouch._tcp` (libsoundtouch patched for recent zeroconf, `libsoundtouch_patch.py`) and SSDP (`ssdp:all`) for DLNA renderers. Speakers not seen for 24 h are deleted.
- **Casting**: always through UPnP **AVTransport** (`SetAVTransportURI` + `Play`), including for SoundTouch; the SoundTouch Web API (port 8090) is only used for volume.
- The speaker plays `GET /media/cast/<type>/<id>?token=…&start=…` (§7.10). The server polls the speaker every 3 s and **chains the next track itself** at the end of playback.
- UPnP control runs in a dedicated asyncio loop (`run_coro`), started by `cast_service.init_app`.
- Test tool without a speaker: `fake_dlna_device.py`, in the Tunoova-ESP32 repository (`Enceinte DLNA fictive/`).

### 7.8 Notifications — `app/notifications/routes.py`

No dedicated table: the list is computed on each display.

| Source | Query |
|---|---|
| Episodes | `EpisodesUsers.EpisodeStatus = 'New'` |
| Music | `MusicUsers.MusicStatus = 'New'` and track available: shares (one per track) and new MP3s (grouped by artist + album) |
| Shared radios | `RadioUsers.RadioStatus in ('Shared', 'New')` |
| Update | `UPDATE_ANNOUNCED_VERSION` in `.env` |

- `count_new()` feeds the navigation bar badge (if `Users.Notifications`).
- `POST /notifications/clear`: `New` → `Unread` (episodes, music), `Shared` radios deleted, `New` → `Read` (radios).
- New MP3s are notified by `music_catalog.notify_new_tracks` (scan), only to users who already have a track by the artist in `MusicUsers`.

### 7.9 Profile and Settings

| Route | Role |
|---|---|
| `GET/POST /userprofile/` | Profile. With htmx: autosave, `_save_status.html` response. Unique email; name, email, age range and country required. Photo: `profile_service.save_profile_photo` (png, jpg, gif, webp) in `db/profile_photos/`. |
| `GET/POST /settings/` | `DarkMode`, `YoutubeMusicEnabled`, `StreamQuality` (`auto`/`low`/`high`, applied to the yt-dlp format), `Langue`. With htmx: 204. "Clear local cache" is purely client-side (`localStorage.clear()` + Cache API). |

### 7.10 Media — `app/media.py`

| Route | Access | Role |
|---|---|---|
| `/media/profile_photo` | session | Signed-in user's photo (outside `static/`) |
| `/media/mp3/<track_id>` | session | Local MP3 (`send_file`, handles Range) |
| `/media/album_cover/<album_id>` | session | Cover embedded in the tags |
| `/media/ytmusic/<track_id>` | session | YouTube stream relayed by the server, Range headers forwarded (the googlevideo URL is bound to the server's IP); new extraction if the URL expires |
| `POST /media/ytmusic/<track_id>/warm` | session | Pre-extracts the next track's URL |
| `/media/cast/<type>/<id>` | **signed token (6 h) + LAN IP** | Stream for speakers: direct MP3, HTTP relay, or ffmpeg (YouTube, MP4, resume at `start`) |

Audio stream summary:

| Content | Browser | Speaker (Cast) |
|---|---|---|
| Podcast | Direct episode URL | Server relay (ffmpeg if MP4 or `start`) |
| Radio | Direct stream URL | Server relay |
| Local MP3 | `/media/mp3/<id>` | `/media/cast/…` (ffmpeg if `start`) |
| YouTube Music | `/media/ytmusic/<id>` (relay) | ffmpeg → AAC ADTS |

## 8. Admin area — `app/admin/`

`admin` blueprint (`/admin`), split into modules sharing the same `bp`. Every route except `/admin/login` and `/admin/locale/<lang>` carries `@admin_required`.

**Admin session** (`decorators.py`): dedicated signed cookie `tunoova_admin` (path `/admin`, HttpOnly, 8 h with no extension), holding a fingerprint of the admin email + hash. Changing the admin account invalidates all sessions. The cookie is removed whenever an app user signs in or out. Anti-brute-force lock per IP and per email (`login_throttle.ADMIN_SCOPE`).

### 8.1 Dashboard — `routes.py`, `app_settings.py`, `update.py`

| Route | Role |
|---|---|
| `GET /admin/` | Tiles: users, DB size, tables, backups, jobs (next), network, MP3s/artists/tracks, podcasts, radios; configuration forms; About. |
| `POST /admin/settings/smtp` | Rewrites `SMTP_*` (password unchanged if empty). |
| `POST /admin/settings/mp3-path` | `MP3_LIBRARY_PATH` (the folder must exist). |
| `POST /admin/settings/backup` | `BACKUP_PATH` (created if needed), `BACKUP_RETENTION_DAYS`. |
| `POST /admin/settings/admin-account` | Admin email and/or password (current password required, strength rule), then a new admin cookie. |
| `POST /admin/settings/purge-logs` | Purges `Log` and/or `LoginHistory` (older than one month, or everything). |
| `GET /admin/update/check` | `git fetch` and comparison of the local and remote HEAD; announces the version (`UPDATE_ANNOUNCED_VERSION`). |
| `POST /admin/update/run` | Drops a request in `/var/lib/tunoova/request/update`. |
| `GET /admin/update/status` | Status and tail of the update log. |

**Update mechanism**: the app (account without root rights) does not run `install.sh`. A root systemd service `tunoova-update` (installed by `install.sh`) watches the request file and runs `install.sh update` from a root-owned mirror of the repository. Unavailable if the app was not installed with `install.sh`.

### 8.2 User accounts — `routes.py`

| Route | Role |
|---|---|
| `GET /admin/users` | List, search (email, name), sort (name, email, status, lock, first login, last sign-in). |
| `POST /admin/users/create` | Creates the user (random password), sends the activation link. |
| `POST /admin/users/<id>/reset-password` | Sends the reset link. |
| `POST /admin/users/<id>/toggle-active` | Activate / deactivate (`IsActive`; Flask-Login rejects an inactive account). |
| `GET/POST /admin/users/<id>/edit` | Email, name, `FirstLogin`, forced password (also unlocks the account, no email sent). |
| `POST /admin/users/<id>/delete` | Deletes the user and all their data (subscriptions, episodes, radios, music, comments, playback state, logs). |

### 8.3 Database — `db_browser.py`

| Route | Role |
|---|---|
| `GET /admin/db` | Tables by category, description, row count. |
| `GET /admin/db/<table>` | Rows paginated by 50, sort, typed filters (=, ≠, contains, starts with, between…). |
| `GET/POST /admin/db/<table>/new` | Add. |
| `GET/POST /admin/db/<table>/<pk>/edit` | Edit. |
| `POST /admin/db/<table>/<pk>/delete` | Delete. |

Generic (works on any table in `db.metadata`). Sensitive columns (`password|hash|secret|token|apikey`) are masked and not editable. Categories and descriptions come from `TABLE_METADATA`; a new table lands in "Other".

### 8.4 Scheduled jobs — `jobs.py`

| Route | Role |
|---|---|
| `GET /admin/jobs` | List (cron, enabled, last and next run, result). |
| `GET /admin/jobs/status` | JSON for refreshing without reloading. |
| `POST /admin/jobs/<id>/update` | Cron and enabled flag (written to `.env` and applied live). |
| `POST /admin/jobs/<id>/run` | Immediate run. |

### 8.5 Radio plug-ins — `radio_plugins.py`

| Route | Role |
|---|---|
| `GET /admin/radio-plugins` | Plug-ins (version, key, last test status, linked stations) and stations with their plug-in and station ID. |
| `POST /admin/radio-plugins/upload` | Installs or replaces a script (name `^[a-z][a-z0-9_]*\.py$`, 256 KB max, `detect` and `fetch` functions checked). The old version goes to `radio_plugins/old/`. |
| `POST /admin/radio-plugins/<id>/update` | API key, enabled flag. |
| `POST /admin/radio-plugins/<id>/test` | Test on the linked stations. |
| `POST /admin/radio-plugins/<id>/rollback` | Back to the previous version. |
| `POST /admin/radio-plugins/<id>/delete` | Deletion; the linked stations are re-detected. |
| `POST /admin/radio-plugins/radio/<id>/test` | Test one station. |

**Plug-in contract** (`app/now_playing.py`):

```python
NAME = "Radio France"
def detect(stream_url, name) -> str | None   # station ID, "" if not needed, None if not handled
def fetch(ref, stream_url, api_key) -> dict | None
# {"kind": "show"|"song", "show": str, "detail": str|None,
#  "image_url": str|None, "start": epoch|None, "end": epoch|None}
```

An uploaded script is **executed on the server**: only install trusted code. Results cached 30 s (120 s if empty). Without a plug-in, falls back to the stream's ICY metadata (`app/icy.py`). Bundled plug-ins: `radiofrance`, `rtl`, `rmc`, `nrj`, `radioplayer_be`.

To create a new plug-in: see [Plugins radio/README.md](Plugins%20radio/README.md) (Claude skill `tunoova-radio-plugin`, template and test bench; in French).

### 8.6 MP3 — `mp3_browser.py`, `app/mp3_files_service.py`

| Route | Role |
|---|---|
| `GET /admin/mp3?path=` | Folder explorer. |
| `POST /admin/mp3/folder`, `/rename`, `/move`, `/delete` (+ `GET /delete-info`) | File and folder operations. |
| `POST /admin/mp3/upload` | Batched upload (under `MP3_UPLOAD_MAX_MB`), folders included. |
| `POST /admin/mp3/sync`, `/scan` | Catalog sync, full scan. |
| `GET/POST /admin/mp3/tags` | Read / write the ID3 tags of a file or a folder. |

All paths go through `_safe_path()` (rejects `..`, absolute paths, symlinks pointing outside). A rename or move updates `MusicTrack.SourceRef`: the track keeps its plays, likes and comments.

### 8.7 Backup — `backups.py`, `app/backup_service.py`

| Route | Role |
|---|---|
| `GET /admin/backups` | List (scheduled/manual, before update, before restore). |
| `POST /admin/backups/create` | Immediate backup. |
| `GET /admin/backups/download/<name>` | Raw file. |
| `GET /admin/backups/bundle/<name>` | Zip of database + `profile_photos/`. |
| `POST /admin/backups/restore` | Restores a zip: validation (a single `.db`, valid Tunoova database, 2 GB max uncompressed), `pre_restore` backup of the current state, replacement, then restart (exits with an error, restarted by systemd). |

Consistent copy through `sqlite3.Connection.backup()`. Files: `tunoova_[pre_update_|pre_restore_]YYYYMMDD_HHMMSS.db` and `tunoova_photos_….zip`. `BACKUP_RETENTION_DAYS` retention for all types.

### 8.8 Network — `remote_access.py`, `app/remote_access.py`, `app/tailscale_service.py`

| Route | Role |
|---|---|
| `GET /admin/network` | Mode, Tailscale state, public URL, QR code. |
| `GET /admin/network/status` | Polling of the Tailscale state (connection, Funnel authorization). |
| `POST /admin/network/mode` | Applies the mode and `TRUSTED_PROXY_COUNT`. |

| Mode | `TRUSTED_PROXY_COUNT` | Implementation |
|---|---|---|
| `off` | 0 | Direct access |
| `external` | 1 | User's reverse proxy, `PUBLIC_HOSTNAME` |
| `private` | 1 | `tailscale serve` |
| `public` | 1 | `tailscale funnel` |

The app drives the `tailscale` command (no shell), authorized by `tailscale set --operator=tunoova`. No Tailscale web API.

In `external`, `private` or `public` mode, `base.html` declares the `/manifest.webmanifest` manifest (`main.manifest` route, icons `brand/favicon-192.png` and `brand/app-icon-512.png`): Chrome then shows the **Install** icon in the address bar. It is not declared in `off` mode, because Chrome only installs an app over HTTPS.

## 9. Database

### 9.1 Overview

SQLite, 23 tables. Column details and the relationship diagram are in [DB Structure.md](<DB Structure.md>). The French version, [DB Structure.fr.md](<DB Structure.fr.md>), is generated by:

```bash
.venv/bin/python scripts/gen_db_doc.py          # regenerate
.venv/bin/python scripts/gen_db_doc.py --check  # exit code 1 if the doc is out of date
```

The English version is translated by hand: update it after a schema change.

| Area | Tables |
|---|---|
| Accounts | `Users` |
| Reference data | `Langues`, `AgeRange`, `Hobby`, `MusicGenre`, `Country` |
| Podcasts | `Podcast`, `PodcastUsers`, `EpisodesUsers`, `PodcastComment` |
| Radios | `Radio`, `RadioUsers`, `RadioComment`, `RadioPlugIn` |
| Music | `MusicArtist`, `MusicAlbum`, `MusicTrack`, `MusicUsers`, `MusicTrackComment` |
| Playback and activity | `Play`, `CastDevice`, `Log`, `LoginHistory` |

### 9.2 Conventions

- **Shared catalog + per-user table**: `Podcast`/`PodcastUsers`, `Radio`/`RadioUsers`, `MusicTrack`/`MusicUsers`. The catalog holds the global counters (likes, dislikes, comments, plays); the user table holds the personal vote, the personal play count and the status.
- `PodcastUsers` and `RadioUsers` copy the name, URL and image: each user can rename them without touching the catalog.
- **Statuses**:

  | Column | Values |
  |---|---|
  | `EpisodesUsers.EpisodeStatus` | `New` (notified), `Unread` (played or notification cleared). `Read` exists in the model but is never written. |
  | `MusicUsers.MusicStatus` | `New`, `Unread`, NULL |
  | `RadioUsers.RadioStatus` | `Shared` (share not accepted yet, not in the list), `New` (share of a station already in the list), `Read`, NULL |
  | `Play.PlayStatus` | `Play`, `Pause`, `Stop` |
  | `*Like` (user) | `Like`, `Not Like`, NULL |

- Multi-choice lists (`Users.Hobby`, `Users.MusicGenre`) stored as comma-separated text; labels translated by `translate_choice` (keys `hobby_*`, `genre_*`, `age_range_*`, `country_*`).
- `Users.SortPrefs` (JSON): last sort per screen (`app/sort_prefs.py`) and Home order.
- Dates in UTC (`datetime.utcnow`).
- Auto-incremented integer primary keys.

### 9.3 Schema changes

No Alembic. On every startup:

1. `db.create_all()` creates the missing tables;
2. `ensure_schema_upgrades()` (`db/seed.py`) adds the missing columns with `ALTER TABLE … ADD COLUMN`, idempotently (checked with `inspect`);
3. `seed_reference_data()` fills empty reference tables.

This code runs at startup, not only during an update: `install.sh` initializes or updates the database with a plain `python -c "import run"`, and a `git pull` followed by a restart is enough to apply additions.

What is automatic:

- **new table**: just add the class in `app/models.py`;
- **new column on an existing table**: provided you add the matching block in `ensure_schema_upgrades()`, following this pattern:

  ```python
  if "Country" not in columns:
      db.session.execute(text("ALTER TABLE Users ADD COLUMN Country VARCHAR(80)"))
      db.session.commit()
  ```

- **new reference data**: `seed_reference_data()` only inserts what is missing.

What must be coded by hand in `ensure_schema_upgrades()` and tested **before** updating production:

- renaming or dropping a column or a table;
- a type change, or making a column `NOT NULL`;
- any data migration.

SQLite's `ALTER TABLE` supports little more than `ADD COLUMN`. For anything else, the table must be recreated:
1. create a temporary table;
2. copy the data into it;
3. drop the old table;
4. rename the temporary table.

Always check the state with `inspect(db.engine)` before acting, so that the function can be replayed on every startup.

Procedure for a schema change:

1. edit `app/models.py`;
2. add the block in `ensure_schema_upgrades()` if needed (see above);
3. run `scripts/gen_db_doc.py` again, and update the English `DB Structure.md`.

If something goes wrong, `install.sh update` backs up the database before every update: see [Installation.md § Rolling back](Installation.md#45-rolling-back-after-an-update).

## 10. Services (business layer)

| Module | Role |
|---|---|
| `podcast_service` | iTunes search, catalog, subscription, RSS sync, series order, episode sharing |
| `radio_service` | Radio Browser search, catalog, favorites, sharing |
| `now_playing`, `icy` | "Now on air" plug-ins, ICY fallback |
| `music_catalog` | Creating/deduplicating artists, albums, tracks; new-MP3 notifications; purge; images |
| `mp3_library_service` | Scan of `MP3_LIBRARY_PATH` (mutagen tags, change detection by mtime/size, hiding/reappearing) |
| `mp3_files_service` | Admin file explorer |
| `cover_service` | Covers and photos (Deezer, iTunes), no database access |
| `youtube_music_service` | ytmusicapi (search, artists, albums, top); yt-dlp (stream URL, 30 min in-memory cache) |
| `play_service` | Playback context, likes, comments, queue, `Play` state, sharing, Cast URL |
| `cast_service` | Speaker discovery and control |
| `backup_service`, `update_service` | Backup/restore, update |
| `remote_access`, `tailscale_service` | Access mode, proxy/cookie middlewares, Tailscale control |
| `login_throttle`, `tokens`, `logging_util` | Anti-brute-force, signed tokens, logs |
| `profile_service`, `sort_prefs`, `email_utils`, `i18n` | Photo and reference data, remembered sorts, SMTP, translations |

## 11. Scheduled jobs — `app/scheduler.py`

| Job | Default cron | Function |
|---|---|---|
| `mp3_scan` | `*/15 * * * *` | `scan_library(force=True, resolve_images=True)` + new-MP3 notifications |
| `music_purge` | `0 4 1 * *` | Deletes tracks hidden for more than 30 days |
| `rss_poll` | `*/30 * * * *` | `sync_episodes(mark_new=True)` for all subscriptions |
| `db_backup` | `0 3 * * *` | Database + photos backup, cleanup according to retention |
| `cast_scan` | `*/5 * * * *` | Speaker discovery |
| `log_purge` | `30 4 * * *` | Deletes `Log` and `LoginHistory` rows older than 30 days |

Status (last run, result, error, running) is kept **in memory**: lost on restart. A failing job is logged without stopping the scheduler.

Adding a job: a `_run_<id>()` function returning `(translation_key, parameters)`, an entry in `JOBS`, the `JOB_<ID>_CRON/ENABLED` keys in `.env.example`, the labels in `en.json` and `fr.json`.

## 12. External integrations

| Service | Use | Authentication | Module |
|---|---|---|---|
| Apple iTunes Search | Podcast search; fallback covers | none | `podcast_service`, `cover_service` |
| Radio Browser | Radio search | none | `radio_service` |
| RSS feeds | Episodes | none | `podcast_service` |
| Deezer API | Covers and artist photos | none | `cover_service` |
| YouTube Music | Search (ytmusicapi), streams (yt-dlp + deno) | none | `youtube_music_service` |
| Radio APIs | Now on air (Radio France, RTL, RMC, NRJ, Radioplayer BE) | none (key possible per plug-in) | `radio_plugins/` |
| SMTP | Activation and reset emails, requests to the admin | `SMTP_*` | `email_utils` |
| GitHub | Checking and downloading updates | anonymous or deploy key | `update_service`, `install.sh` |
| Tailscale | Remote access | the admin's Tailscale account | `tailscale_service` |

These services come with no guarantee (undocumented APIs for most plug-ins, YouTube). Expect outages: every call is best-effort with a timeout, and the screen is displayed without the result.

## 13. Internationalization

- `app/translations/en.json` and `fr.json` files (flat keys), loaded once at startup.
- In templates: `t('key', param=…)`. In Python: `translate()`.
- Language selection (`current_locale`):
  1. on `/admin/*`, the admin's FR/EN choice (`session["admin_locale"]`);
  2. `Users.Langue` if signed in;
  3. preference in the session;
  4. the browser's `Accept-Language`, otherwise English.
- `flash` messages of a few routes (Podcast, Radio) are still hard-coded in French.

## 14. Security

| Topic | Implementation |
|---|---|
| Passwords | bcrypt; one rule shared by app/admin/install.sh (`admin/password_policy.py`) |
| Anti-brute-force | 5 failures → 15 min, per account (`Users.LockedUntil`) and per IP (`login_throttle`, computed from the last 24 hours of `LoginHistory`); admin: per IP and per email |
| Requests to the admin | Honeypot field + IP/email/global quotas; identical response (no email enumeration) |
| User session | Remember cookie, 30 days sliding, HttpOnly, SameSite=Lax; session cleared on every sign-in/sign-out |
| Admin session | Separate cookie, path `/admin`, 8 h, invalidated if the admin account changes |
| `Secure` cookie | Set per request (`SecureCookieMiddleware`): yes over HTTPS/proxy, no over local HTTP |
| Real IP | `TrustedProxyMiddleware`: `X-Forwarded-*` read only if the request comes from a trusted proxy (`TRUSTED_PROXY_COUNT`) |
| CSRF | Flask-WTF on every POST |
| Tokens | itsdangerous: activation 24 h, reset 1 h bound to the hash (single use), Cast 6 h bound to the type and item |
| Headers | `X-Content-Type-Options: nosniff`, `X-Frame-Options: DENY`, `Referrer-Policy`, minimal CSP (`frame-ancestors`, `object-src`, `base-uri`, `form-action`); no `script-src` CSP (inline scripts) |
| Cache | `private` everywhere, `no-store` on HTML/JSON/downloads |
| Files | Photos outside `static/`; MP3 paths confined (`_safe_path`); redirects limited to internal URLs (`safe_local_url`) |
| Admin DB | Sensitive columns masked |
| Plug-ins | Code executed on the server: admin only, trusted scripts only |
| Production | waitress, `FLASK_DEBUG=0` forced, dedicated system account, `.env` in mode 600 |
| Audit | `scripts/audit.sh` (pip-audit, bandit, semgrep, gitleaks, ZAP…); procedure in [Audit Tunoova](Audit%20Tunoova.md) (in French) |

## 15. Logging and operations

| Where | What |
|---|---|
| `Log` table | One row per displayed screen: user, `Frame`, date, device, IP, OS |
| `LoginHistory` table | Events: `success`, `invalid_user`, `invalid_password`, `user_locked`, `ip_locked`, `cookie_resume`, `logout`, `admin_success`, `admin_invalid`, `admin_locked`, `account_request`, `reset_request`, `request_blocked` |
| journald | App log: `sudo journalctl -u tunoova -n 100 --no-pager` |
| `/var/log/tunoova-update.log` | Updates started from the Admin area |

Useful commands:

```bash
sudo systemctl status tunoova
sudo systemctl restart tunoova
sudo ./install.sh update        # full update (backup, pip, .env, permissions)
sudo ./install.sh reset         # new admin password
.venv/bin/pip install -U yt-dlp # if YouTube playback stops working
```

Installation, updates, backup and manual restore: [Installation.md](Installation.md).

## 16. Development

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
cp .env.example .env            # fill in SESSION_SECRET_KEY, ADMIN_EMAIL, ADMIN_PASSWORD_HASH
FLASK_DEBUG=1 .venv/bin/python run.py -p 5000
```

- No automated test suite in the repository.
- Releasing a version: change `APP_VERSION` and `APP_RELEASE_DATE` in `app/version.py` (this is what the Update button compares).
- New configuration key: add it to `.env.example` (merged into `.env` by `install.sh update`).
- New interface text: a key in `en.json` **and** `fr.json`.
- Documentation: the README, Installation, Technical documentation and DB Structure exist in English (`*.md`) and French (`*.fr.md`, `Documentation technique.md`); keep both in sync. Screenshots: `Doc/Captures/` (French) and `Doc/Captures/en/` (English), same file names.

## 17. Points of attention

- **Single process**: scheduler, caches and Cast loop in memory; no multiple workers.
- **SQLite**: enough for family use; writes are serialized. Long audio streams hold waitress threads (32).
- **yt-dlp** breaks regularly when YouTube changes: update it first when playback fails.
- **Job status** lost on restart.
- **Hard-coded French flash messages** in `podcast/routes.py` and `radio/routes.py`.
- `EpisodeStatus = 'Read'` values are never used.
