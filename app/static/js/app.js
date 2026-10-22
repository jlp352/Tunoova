// Mini-player global (CDC §7) : demarrage de la lecture depuis n'importe quel
// ecran (Podcast/Radio/Library), reprise de position, et ouverture de l'ecran
// Play plein format en cliquant sur le mini-player.

function tunoovaCsrfToken() {
  const meta = document.querySelector('meta[name="csrf-token"]');
  return meta ? meta.content : "";
}

function tunoovaFormatTime(s) {
  if (!isFinite(s) || s < 0) return "0:00";
  const m = Math.floor(s / 60);
  const sec = Math.floor(s % 60).toString().padStart(2, "0");
  return `${m}:${sec}`;
}

// --------------------------------------------------------------------------
// Fonctionnalite Cast (CDC §7/§8/§10) : fenetre de choix d'une enceinte
// (#cast-modal, cf. app/templates/base.html) et bascule du mini-player/panneau
// Play entre lecture locale (<audio>) et diffusion sur un device distant
// (app/cast_service.py, routes app/cast/routes.py). #miniplayer porte l'etat
// ("data-casting"/"data-cast-playing") puisqu'il n'est, comme #play-overlay,
// jamais demonte par une navigation htmx (cf. commentaires plus bas) - les
// deux zones (mini-player, panneau Play) restent ainsi synchronisees sans
// dependre de quel ecran est affiche au moment ou l'utilisateur choisit ou
// arrete une enceinte.
// --------------------------------------------------------------------------

function tunoovaIsPlaying() {
  const miniplayer = document.getElementById("miniplayer");
  if (miniplayer && miniplayer.dataset.casting === "1") {
    return miniplayer.dataset.castPlaying === "1";
  }
  const audio = document.getElementById("miniplayer-audio");
  return audio ? !audio.paused : false;
}

function tunoovaSetCasting(label, playing, deviceId) {
  const miniplayer = document.getElementById("miniplayer");
  if (!miniplayer) return;
  miniplayer.dataset.casting = "1";
  miniplayer.dataset.castPlaying = playing ? "1" : "0";
  tunoovaReleaseNextTrack(); // en Cast, le serveur enchaine lui-meme
  if (deviceId !== undefined && deviceId !== null) miniplayer.dataset.castDeviceId = deviceId;

  const castRow = document.getElementById("miniplayer-cast-row");
  const badgeLabel = document.getElementById("miniplayer-cast-label");
  if (castRow && badgeLabel) {
    const deviceChanged = castRow.dataset.volumeDeviceId !== String(miniplayer.dataset.castDeviceId || "");
    badgeLabel.textContent = label;
    if (castRow.hidden || deviceChanged) tunoovaLoadCastVolume();
    castRow.hidden = false;
  }
  const playBadge = document.getElementById("play-cast-badge");
  const playBadgeLabel = document.getElementById("play-cast-device-label");
  if (playBadge && playBadgeLabel) {
    playBadgeLabel.textContent = label;
    playBadge.hidden = false;
  }

  const toggle = document.getElementById("miniplayer-toggle");
  if (toggle) tunoovaSetMiniToggle(toggle, playing);

  // La diffusion locale (<audio>) et la diffusion Cast ne doivent jamais
  // tourner en meme temps - on coupe le son local, sans toucher a son `src`
  // (reutilise tel quel si l'utilisateur arrete le Cast juste apres).
  const audio = document.getElementById("miniplayer-audio");
  if (audio && !audio.paused) audio.pause();

  tunoovaSyncPlayToggleIcon();
  tunoovaStartCastPolling();
}

function tunoovaClearCasting() {
  const miniplayer = document.getElementById("miniplayer");
  if (miniplayer) {
    delete miniplayer.dataset.casting;
    delete miniplayer.dataset.castPlaying;
    delete miniplayer.dataset.castDeviceId;
  }
  const castRow = document.getElementById("miniplayer-cast-row");
  if (castRow) {
    castRow.hidden = true;
    delete castRow.dataset.volumeDeviceId;
  }
  const playBadge = document.getElementById("play-cast-badge");
  if (playBadge) playBadge.hidden = true;
  tunoovaStopCastPolling();
}

var tunoovaCastPollTimer = null;

function tunoovaStartCastPolling() {
  tunoovaStopCastPolling();
  tunoovaPollCastStatus();
  tunoovaCastPollTimer = setInterval(tunoovaPollCastStatus, 4000);
}

function tunoovaStopCastPolling() {
  if (tunoovaCastPollTimer) {
    clearInterval(tunoovaCastPollTimer);
    tunoovaCastPollTimer = null;
  }
}

// Pendant une diffusion Cast, <audio> ne joue pas (cf. tunoovaSetCasting) :
// ses evenements natifs (timeupdate...) ne peuvent donc pas alimenter la
// barre de progression / l'icone Lecture-Pause du panneau Play - on va
// chercher l'etat reel aupres du device (app/cast_service.py, position
// suivie via GetPositionInfo) toutes les 4s le temps que le Cast est actif.
function tunoovaPollCastStatus() {
  const miniplayer = document.getElementById("miniplayer");
  if (!miniplayer || miniplayer.dataset.casting !== "1") {
    tunoovaStopCastPolling();
    return;
  }
  fetch("/cast/status", { headers: { Accept: "application/json" } })
    .then((r) => r.json())
    .then((data) => {
      if (!data.active) {
        tunoovaClearCasting();
        return;
      }
      miniplayer.dataset.castPlaying = data.playing ? "1" : "0";
      if (data.play_url && data.play_url !== miniplayer.dataset.playUrl) {
        // Enchainement automatique fait par le serveur sur l'enceinte : titre,
        // pochette et panneau Lecture (s'il est ouvert) suivent le nouvel item.
        const overlay = document.getElementById("play-overlay");
        if (overlay && !overlay.hidden) {
          history.replaceState({ tunoovaOverlay: true }, "", data.play_url);
          tunoovaLoadPlayOverlay(data.play_url);
        }
        fetch(data.play_url + "/data", { headers: { Accept: "application/json" } })
          .then((r) => r.json())
          .then((d) => tunoovaUpdateMiniplayerInfo(tunoovaOverlayDataToMiniplayer(d)))
          .catch(() => {});
      }
      const toggle = document.getElementById("miniplayer-toggle");
      if (toggle) tunoovaSetMiniToggle(toggle, data.playing);
      tunoovaSyncPlayToggleIcon();

      // Saut en cours (curseur deplace, flux relance) : ne pas ramener le
      // curseur a l'ancienne position le temps que l'enceinte reparte.
      if (tunoovaCastSeeking) return;
      const seek = document.getElementById("play-seek");
      const current = document.getElementById("play-time-current");
      const total = document.getElementById("play-time-total");
      if (seek && data.duration) {
        seek.max = data.duration;
        seek.value = data.position;
        if (total) total.textContent = tunoovaFormatTime(data.duration);
      }
      if (current) current.textContent = tunoovaFormatTime(data.position);
    })
    .catch(() => {});
}

// Deplacement dans la barre pendant un Cast : les enceintes (SoundTouch
// notamment) refusent le Seek AVTransport - on repousse donc le meme item
// via /cast/select avec la nouvelle position, et le serveur fournit a
// l'enceinte un flux qui demarre directement a cet endroit (start=).
var tunoovaCastSeeking = false;

function tunoovaCastSeekTo(position) {
  const miniplayer = document.getElementById("miniplayer");
  const deviceId = miniplayer && miniplayer.dataset.castDeviceId;
  if (!deviceId) {
    tunoovaCastSeeking = false;
    return;
  }
  fetch("/cast/select", {
    method: "POST",
    headers: {
      "X-CSRFToken": tunoovaCsrfToken(),
      "X-Requested-With": "XMLHttpRequest",
      "Content-Type": "application/x-www-form-urlencoded",
    },
    body: new URLSearchParams({ device_id: deviceId, position: position }),
  })
    .then((r) => r.json())
    .then((res) => {
      if (res.ok) tunoovaSetCasting(res.device_label, true, res.device_id);
    })
    .catch(() => {})
    .finally(() => { tunoovaCastSeeking = false; });
}

function tunoovaShowCastMessage(message) {
  const el = document.getElementById("cast-modal-message");
  if (!el) return;
  el.textContent = message || "";
  el.hidden = !message;
}

function tunoovaOpenCastModal() {
  const modal = document.getElementById("cast-modal");
  if (!modal) return;
  tunoovaShowCastMessage(null);
  modal.showModal();
  tunoovaLoadCastDevices();
}

function tunoovaLoadCastDevices() {
  const list = document.getElementById("cast-device-list");
  const empty = document.getElementById("cast-device-empty");
  const iconTemplate = document.getElementById("cast-device-icon-template");
  if (!list) return;

  list.querySelectorAll(".cast-device-row").forEach((el) => el.remove());

  fetch("/cast/devices", { headers: { Accept: "application/json" } })
    .then((r) => r.json())
    .then((devices) => {
      if (empty) empty.hidden = devices.length > 0;
      const labels = window.tunoovaCastLabels || {};
      devices.forEach((d) => {
        const row = document.createElement("button");
        row.type = "button";
        row.className = "cast-device-row" + (d.active ? " active" : "") + (d.online ? "" : " offline");
        row.dataset.deviceId = d.id;

        const iconSpan = document.createElement("span");
        iconSpan.className = "cast-device-icon";
        if (iconTemplate) iconSpan.innerHTML = iconTemplate.innerHTML;

        const info = document.createElement("span");
        info.className = "cast-device-info";
        const name = document.createElement("span");
        name.className = "cast-device-name";
        name.textContent = d.label; // nom d'un device reseau tiers - jamais via innerHTML
        const sub = document.createElement("span");
        sub.className = "cast-device-sub";
        const typeLabel = d.device_type === "Soundtouch" ? labels.soundtouch : labels.dlna;
        sub.textContent = d.online ? typeLabel : `${typeLabel} · ${labels.offline || ""}`;
        info.appendChild(name);
        info.appendChild(sub);

        row.appendChild(iconSpan);
        row.appendChild(info);

        if (d.active) {
          const activeBadge = document.createElement("span");
          activeBadge.className = "cast-device-active-badge";
          activeBadge.textContent = labels.active || "";
          row.appendChild(activeBadge);

          // Arret explicite a cote du badge (la ligne est deja un <button> :
          // pas de <button> imbrique, d'ou le span role="button").
          const stopBtn = document.createElement("span");
          stopBtn.className = "cast-device-stop-btn";
          stopBtn.setAttribute("role", "button");
          stopBtn.tabIndex = 0;
          stopBtn.textContent = labels.stop || "Stop";
          stopBtn.addEventListener("click", function (e) {
            e.stopPropagation();
            tunoovaStopCast();
          });
          stopBtn.addEventListener("keydown", function (e) {
            if (e.key === "Enter" || e.key === " ") {
              e.preventDefault();
              e.stopPropagation();
              tunoovaStopCast();
            }
          });
          row.appendChild(stopBtn);
        }

        row.addEventListener("click", function () {
          if (d.active) {
            tunoovaStopCast();
          } else {
            tunoovaSelectCastDevice(d.id);
          }
        });

        list.appendChild(row);
      });
    })
    .catch(() => tunoovaShowCastMessage(null));
}

// Curseurs de volume Cast (barre de lecture + ecran Play, classe
// .cast-volume-range) : initialises avec le volume reel de l'enceinte (GET
// /cast/volume) a chaque nouvelle diffusion - 50 par defaut s'il est
// illisible - et gardes synchronises entre eux. Envoi au relachement
// (change) pour ne pas inonder l'enceinte de commandes.
function tunoovaLoadCastVolume() {
  const castRow = document.getElementById("miniplayer-cast-row");
  const miniplayer = document.getElementById("miniplayer");
  const ranges = document.querySelectorAll(".cast-volume-range");
  if (!ranges.length) return;
  if (castRow) castRow.dataset.volumeDeviceId = (miniplayer && miniplayer.dataset.castDeviceId) || "";

  const setAll = (v) => ranges.forEach((r) => { r.value = v; r.title = v; });
  setAll(50);
  fetch("/cast/volume", { headers: { Accept: "application/json" } })
    .then((r) => r.json())
    .then((data) => {
      if (data.volume !== null && data.volume !== undefined) setAll(data.volume);
    })
    .catch(() => {});

  ranges.forEach((range) => {
    if (range.dataset.bound) return;
    range.dataset.bound = "1";
    range.addEventListener("click", (e) => e.stopPropagation());
    range.addEventListener("input", () => setAll(range.value));
    range.addEventListener("change", () => {
      setAll(range.value);
      fetch("/cast/volume", {
        method: "POST",
        headers: {
          "X-CSRFToken": tunoovaCsrfToken(),
          "X-Requested-With": "XMLHttpRequest",
          "Content-Type": "application/x-www-form-urlencoded",
        },
        body: new URLSearchParams({ value: range.value }),
      }).catch(() => {});
    });
  });
}

function tunoovaSelectCastDevice(deviceId) {
  // Position locale courante, pour que l'enceinte reprenne au meme endroit
  // (ignoree cote serveur pour une radio). Absente si on passe d'une enceinte
  // a une autre : le serveur retombe alors sur Play.PositionSeconds.
  const body = new URLSearchParams({ device_id: deviceId });
  const audio = document.getElementById("miniplayer-audio");
  const miniplayer = document.getElementById("miniplayer");
  if (audio && audio.currentTime && !(miniplayer && miniplayer.dataset.casting === "1")) {
    body.set("position", audio.currentTime);
  }
  fetch("/cast/select", {
    method: "POST",
    headers: {
      "X-CSRFToken": tunoovaCsrfToken(),
      "X-Requested-With": "XMLHttpRequest",
      "Content-Type": "application/x-www-form-urlencoded",
    },
    body: body,
  })
    .then((r) => r.json())
    .then((data) => {
      if (data.ok) {
        tunoovaSetCasting(data.device_label, true, data.device_id);
        const modal = document.getElementById("cast-modal");
        if (modal) modal.close();
      } else {
        tunoovaShowCastMessage(data.message);
      }
    })
    .catch(() => tunoovaShowCastMessage(null));
}

function tunoovaStopCast() {
  fetch("/cast/stop", {
    method: "POST",
    headers: {
      "X-CSRFToken": tunoovaCsrfToken(),
      "X-Requested-With": "XMLHttpRequest",
    },
  })
    .then((r) => r.json())
    .then((stopData) => {
      tunoovaClearCasting();
      const modal = document.getElementById("cast-modal");
      if (modal) modal.close();

      // Le Cast a pu enchainer automatiquement sur un autre item pendant la
      // diffusion (podcast/musique suivant - cf. app/cast_service.py::
      // _advance_or_stop) : on resynchronise le mini-player sur l'item
      // REELLEMENT courant plutot que de simplement reprendre l'ancien src.
      const miniplayer = document.getElementById("miniplayer");
      if (miniplayer && miniplayer.dataset.playUrl) {
        fetch(miniplayer.dataset.playUrl + "/data", { headers: { Accept: "application/json" } })
          .then((r) => r.json())
          .then((data) => tunoovaApplyMiniplayer(tunoovaOverlayDataToMiniplayer(data), stopData.position))
          .catch(() => {});
      }
    })
    .catch(() => tunoovaShowCastMessage(null));
}

// Appelee par les listes Podcast/Radio/Library/Home/Notifications : demarre
// la lecture d'un contenu et met a jour le mini-player persistant.
//
// Cast-aware : ces routes (library.play, podcast.play_episode, radio.play...)
// appellent toutes play_service.set_current_play cote serveur avant de
// repondre, quel que soit l'etat de diffusion - sans le garde-fou ci-dessous,
// choisir un nouveau contenu depuis une liste PENDANT une diffusion Cast
// demarrait la lecture localement (<audio>) tout en laissant Play.CastDeviceId
// et le badge "Diffusion sur ..." inchanges (constate par l'utilisateur :
// le son passait en local sans que l'UI ne cesse d'afficher le Cast comme actif).
function tunoovaStartPlayback(url) {
  // Lecture lancee explicitement (liste, "Les plus populaires"...) : le
  // suivant deja precharge l'a ete dans l'ancien contexte (autre file de
  // lecture), meme si le morceau lance est celui deja en cours.
  tunoovaReleaseNextTrack();
  return fetch(url, { method: "POST", headers: { "X-CSRFToken": tunoovaCsrfToken() } })
    .then((r) => r.json())
    .then((data) => {
      const miniplayer = document.getElementById("miniplayer");
      if (miniplayer && miniplayer.dataset.casting === "1") {
        tunoovaRecastToActiveDevice(data);
      } else {
        tunoovaApplyMiniplayer(data);
      }
    })
    .catch(() => {});
}

// A appeler au chargement d'un ecran atteint via ?autoplay=1 (CDC §8, ex.
// top_tracks.html/album.html). htmx peut re-executer un <script> de page
// deja visitee lors d'une restauration d'historique (bouton Retour) - cette
// garde (par onglet, via sessionStorage) empeche de relancer la lecture une
// seconde fois pour la meme URL si ca se reproduit.
function tunoovaAutoplayOnce(url) {
  const key = "tunoova-autoplayed:" + url;
  if (sessionStorage.getItem(key)) return;
  sessionStorage.setItem(key, "1");
  tunoovaStartPlayback(url);
}

// Les ecrans Podcast/Radio/Library portent hx-history="false" (cf.
// .library-screen/.podcast-screen/.radio-screen) : htmx ne prend alors plus
// aucun instantane de ces pages pour son cache d'historique, donc le bouton
// Retour y refait toujours une requete serveur fraiche (recherche/resultats
// jamais perimes, sans avoir a les vider nous-memes). On avait auparavant un
// ecouteur htmx:beforeHistorySave qui vidait #library-list/#podcast-list/
// #radio-list juste avant la prise de l'instantane - mais cet evenement se
// declenche pour TOUTE navigation quittant la page, y compris un clic sur un
// lien SITUE A L'INTERIEUR de cette meme liste (ex. chevron artiste/album) :
// vider la liste detachait alors du DOM l'element qu'on venait de cliquer, en
// plein milieu du traitement de sa propre requete - hx-select/hx-swap
// hérités de <body> ne pouvaient alors plus etre retrouves en remontant ses
// ancetres (devenus orphelins), et htmx retombait sur son swap par defaut
// pour les liens boostes (innerHTML, contenu non filtre) : la reponse
// entiere (nouveau #app-boost-root + #miniplayer + #play-overlay + <script>)
// se retrouvait alors inseree A L'INTERIEUR de l'ancien #app-boost-root
// (double lecture audio, barre du bas dupliquee - constate et trace via
// Playwright jusqu'a ce mecanisme exact).

// #miniplayer vit hors de #app-boost-root (cf. base.html) pour ne jamais
// etre detache/rattache au document par un swap - donc le serveur ne peut
// plus mettre a jour sa classe miniplayer-hidden en le re-rendant. On la
// resynchronise ici a partir de data-play-screen, rafraichi lui a chaque
// swap puisqu'il fait partie du contenu remplace.
document.body.addEventListener("htmx:afterSettle", function () {
  const root = document.getElementById("app-boost-root");
  const miniplayer = document.getElementById("miniplayer");
  // Arrivee par navigation boostee depuis un ecran sans chrome (connexion,
  // premier login...) : #miniplayer/#play-overlay, hors de #app-boost-root,
  // n'ont jamais ete crees - la barre de lecture n'apparaitrait qu'apres un
  // rafraichissement. On recharge alors la page une fois.
  if (root && !miniplayer && root.querySelector(".bottom-nav")) {
    window.location.reload();
    return;
  }
  if (!root || !miniplayer) return;
  miniplayer.classList.toggle("miniplayer-hidden", root.dataset.playScreen === "1");
});

// Metadonnees affichees (titre/sous-titre/pochette) + Media Session -
// commun aux deux destinations de lecture (<audio> local ou Cast). Separee
// de tunoovaApplyMiniplayer pour pouvoir mettre a jour l'affichage sans
// jamais toucher a <audio> pendant une diffusion Cast (cf. tunoovaRecastToActiveDevice).
function tunoovaUpdateMiniplayerInfo(data) {
  const title = document.querySelector(".miniplayer-title");
  const subtitle = document.querySelector(".miniplayer-subtitle");
  const thumb = document.getElementById("miniplayer-thumb");
  const miniplayer = document.getElementById("miniplayer");

  if (title) title.textContent = data.title || "";
  if (subtitle) subtitle.textContent = data.subtitle || "";
  if (thumb) {
    if (data.thumbnail) {
      thumb.src = data.thumbnail;
      thumb.hidden = false;
    } else {
      thumb.hidden = true;
    }
  }
  if (miniplayer && data.play_url) miniplayer.dataset.playUrl = data.play_url;

  if ("mediaSession" in navigator) {
    navigator.mediaSession.metadata = new MediaMetadata({
      title: data.title,
      artist: data.subtitle || "",
      artwork: data.thumbnail ? [{ src: new URL(data.thumbnail, location.href).href }] : [],
    });
  }

  if (data.play_url) {
    tunoovaPrefetchPlayData(data.play_url);
    tunoovaSchedulePreloadNext(data.play_url);
  }
  tunoovaRefreshRadioNowPlaying(); // radio : titre en cours sans attendre le prochain tour du minuteur
}

// --------------------------------------------------------------------------
// Prechargement du morceau suivant (cf. Doc/Documentation technique.md).
// Sur Android, ecran verrouille, la page passe en basse priorite des que
// l'<audio> s'arrete : les requetes lancees APRES "ended" (/data du suivant,
// resolution yt-dlp, debut du flux) restaient bloquees jusqu'au retour dans
// l'appli. On prepare donc tout pendant que le morceau courant joue encore :
// donnees du suivant (/data?prefetch=1, sans en faire l'item courant cote
// serveur) puis, si possible, le fichier entier en memoire (Blob), telecharge
// par blocs (reprise apres coupure reseau). Au "ended", le suivant demarre
// alors sans aucun appel reseau.
// --------------------------------------------------------------------------

var TUNOOVA_PRELOAD_DELAY_MS = 5000; // ne pas concurrencer le debut du morceau courant
var TUNOOVA_PRELOAD_MAX_BYTES = 30 * 1024 * 1024; // au-dela (podcasts...) : <audio> de prechargement
var TUNOOVA_PRELOAD_CHUNK_BYTES = 2 * 1024 * 1024;
var TUNOOVA_WARM_BEFORE_END_S = 15;
// Sans initialiseur : ce script peut etre rejoue (cf. tunoovaOverlayPushed),
// une redeclaration garde alors la valeur en cours au lieu de la perdre.
var tunoovaNextTrack; // { forPlayUrl, playUrl, data, blobUrl, state, abort, warmed }
var tunoovaCurrentBlobUrl; // Blob du morceau en cours de lecture, libere au changement suivant
var tunoovaPreloadTimer;

function tunoovaReleaseNextTrack() {
  clearTimeout(tunoovaPreloadTimer);
  const next = tunoovaNextTrack;
  tunoovaNextTrack = null;
  if (!next) return;
  next.abort.abort();
  if (next.blobUrl) URL.revokeObjectURL(next.blobUrl);
  const el = document.getElementById("miniplayer-audio-next");
  if (el && el.getAttribute("src")) {
    el.removeAttribute("src");
    el.load();
  }
}

// Appelee a chaque changement de morceau (tunoovaUpdateMiniplayerInfo) et au
// chargement de la page. Garde un prechargement deja lance pour ce morceau.
function tunoovaSchedulePreloadNext(playUrl) {
  if (tunoovaNextTrack && tunoovaNextTrack.forPlayUrl === playUrl && tunoovaNextTrack.state !== "failed") return;
  tunoovaReleaseNextTrack();
  if (!playUrl || /\/play\/radio\//.test(playUrl)) return;
  tunoovaPreloadTimer = setTimeout(() => tunoovaPreloadNext(playUrl), TUNOOVA_PRELOAD_DELAY_MS);
}

function tunoovaFetchJson(url, signal) {
  return fetch(url, { headers: { Accept: "application/json" }, signal }).then((r) => {
    if (!r.ok) throw new Error(url + " " + r.status);
    return r.json();
  });
}

function tunoovaSaveData() {
  return !!(navigator.connection && navigator.connection.saveData);
}

function tunoovaPreloadNext(forPlayUrl) {
  const miniplayer = document.getElementById("miniplayer");
  if (!miniplayer || miniplayer.dataset.playUrl !== forPlayUrl || miniplayer.dataset.casting === "1") return;

  const abort = new AbortController();
  const next = { forPlayUrl, state: "loading", abort };
  tunoovaNextTrack = next;

  const current = tunoovaPlayDataCache && tunoovaPlayDataCache.url === forPlayUrl
    ? Promise.resolve(tunoovaPlayDataCache.data)
    : tunoovaFetchJson(forPlayUrl + "/data?prefetch=1", abort.signal);

  current
    .then((cur) => {
      if (!cur.next_play_url) throw new Error("fin de liste");
      next.playUrl = cur.next_play_url;
      return tunoovaFetchJson(next.playUrl + "/data?prefetch=1", abort.signal);
    })
    .then((data) => {
      next.data = data;
      const streamUrl = data.stream_url;
      if (tunoovaSaveData()) {
        next.state = "ready"; // donnees seules : pas de telechargement complet
        return;
      }
      const sameOrigin = new URL(streamUrl, location.href).origin === location.origin;
      const download = sameOrigin
        ? tunoovaDownloadBlob(streamUrl, abort.signal)
        : Promise.reject(new Error("autre origine")); // podcast heberge ailleurs (CORS)
      return download
        .then((blob) => {
          next.blobUrl = URL.createObjectURL(blob);
          next.state = "ready";
        })
        .catch((err) => {
          if (abort.signal.aborted) throw err;
          console.warn("Tunoova : prechargement complet impossible, repli <audio>", err);
          tunoovaPreloadInElement(streamUrl);
          next.state = "ready";
        });
    })
    .catch((err) => {
      if (abort.signal.aborted) return;
      console.warn("Tunoova : pas de morceau suivant precharge pour " + forPlayUrl, err);
      if (next.state === "loading") next.state = next.data ? "ready" : "failed";
    });
}

// Telechargement complet par blocs (en-tete Range) : googlevideo coupe parfois
// les gros transferts d'un seul tenant, et une coupure du reseau mobile ne
// fait alors reprendre que le bloc en cours.
async function tunoovaDownloadBlob(url, signal) {
  const parts = [];
  let pos = 0;
  let total = null;
  let type = "";
  let failures = 0;
  const fatal = (msg) => Object.assign(new Error(msg), { fatal: true });

  while (total === null || pos < total) {
    try {
      const r = await fetch(url, {
        headers: { Range: `bytes=${pos}-${pos + TUNOOVA_PRELOAD_CHUNK_BYTES - 1}` },
        signal,
      });
      if (r.status === 200) {
        // Serveur sans prise en charge de Range : fichier entier d'un coup.
        if (Number(r.headers.get("Content-Length")) > TUNOOVA_PRELOAD_MAX_BYTES) throw fatal("trop gros");
        return await r.blob();
      }
      if (r.status !== 206) {
        if (r.status < 500) throw fatal("statut " + r.status);
        throw new Error("statut " + r.status);
      }
      const match = /\/(\d+)$/.exec(r.headers.get("Content-Range") || "");
      if (!match) throw fatal("Content-Range absent");
      total = Number(match[1]);
      if (total > TUNOOVA_PRELOAD_MAX_BYTES) throw fatal("trop gros");
      type = r.headers.get("Content-Type") || type;
      const buf = await r.arrayBuffer();
      if (!buf.byteLength) throw fatal("bloc vide");
      parts.push(buf);
      pos += buf.byteLength;
      failures = 0;
    } catch (err) {
      if (signal.aborted || err.fatal || ++failures > 5) throw err;
      await new Promise((resolve) => setTimeout(resolve, 2000 * failures));
    }
  }
  return new Blob(parts, { type });
}

// Repli pour ce qui ne passe pas en Blob (gros fichier, podcast d'une autre
// origine) : un second <audio> charge le debut du flux, que l'<audio>
// principal peut ensuite reprendre du cache HTTP.
function tunoovaPreloadInElement(streamUrl) {
  const el = document.getElementById("miniplayer-audio-next");
  if (!el) return;
  el.src = streamUrl;
  el.load();
}

// Resolution anticipee de l'URL googlevideo du suivant (yt-dlp, plusieurs
// secondes) quand il n'est pas deja en memoire - cf. media.ytmusic_warm.
function tunoovaWarmStream(streamUrl) {
  if (!/\/media\/ytmusic\/\d+$/.test(streamUrl)) return;
  fetch(streamUrl + "/warm", { method: "POST", headers: { "X-CSRFToken": tunoovaCsrfToken() } }).catch(() => {});
}

// Pont silencieux entre deux morceaux. A "ended", Chrome Android desactive
// la session media et arrete son service de premier plan ; le morceau
// suivant demarre bien, mais Android (ecran verrouille, appli en arriere-plan)
// interdit de relancer ce service et gele le processus quelques secondes plus
// tard - la lecture ne reprenait qu'au deverrouillage. Un <audio> de silence
// en boucle, lance juste avant la fin du morceau et arrete une fois le
// suivant bien parti, garde la session active pendant l'enchainement.
var TUNOOVA_BRIDGE_BEFORE_END_S = 4;
var TUNOOVA_BRIDGE_MAX_MS = 60 * 1000; // garde-fou si le suivant ne demarre jamais
var tunoovaSilentBridge;
var tunoovaBridgeTimer;

// WAV PCM 8 bits mono a 8 kHz rempli de silence (128). Plus de 5 s : Chrome
// ignore les medias plus courts pour la session media.
function tunoovaSilentWavUrl(seconds) {
  const rate = 8000;
  const n = rate * seconds;
  const view = new DataView(new ArrayBuffer(44 + n));
  const text = (offset, s) => { for (let i = 0; i < s.length; i++) view.setUint8(offset + i, s.charCodeAt(i)); };
  text(0, "RIFF"); view.setUint32(4, 36 + n, true); text(8, "WAVE");
  text(12, "fmt "); view.setUint32(16, 16, true); view.setUint16(20, 1, true); view.setUint16(22, 1, true);
  view.setUint32(24, rate, true); view.setUint32(28, rate, true); view.setUint16(32, 1, true); view.setUint16(34, 8, true);
  text(36, "data"); view.setUint32(40, n, true);
  new Uint8Array(view.buffer, 44).fill(128);
  return URL.createObjectURL(new Blob([view.buffer], { type: "audio/wav" }));
}

function tunoovaStartSilentBridge() {
  if (!tunoovaSilentBridge) {
    tunoovaSilentBridge = new Audio(tunoovaSilentWavUrl(10));
    tunoovaSilentBridge.loop = true;
  }
  clearTimeout(tunoovaBridgeTimer);
  tunoovaBridgeTimer = setTimeout(tunoovaStopSilentBridge, TUNOOVA_BRIDGE_MAX_MS);
  if (tunoovaSilentBridge.paused) tunoovaSilentBridge.play().catch(() => {});
}

function tunoovaStopSilentBridge() {
  clearTimeout(tunoovaBridgeTimer);
  if (tunoovaSilentBridge && !tunoovaSilentBridge.paused) tunoovaSilentBridge.pause();
}

// Change la source de #miniplayer-audio en liberant le Blob du morceau
// precedent (seule source de fuite memoire de ce mecanisme).
function tunoovaSetAudioSrc(audio, src, isBlob) {
  const previousBlob = tunoovaCurrentBlobUrl;
  tunoovaCurrentBlobUrl = isBlob ? src : null;
  audio.src = src;
  if (previousBlob && previousBlob !== src) URL.revokeObjectURL(previousBlob);
}

// play() refuse (reseau, politique de lecture) : jusqu'ici avale en silence,
// le bouton affichait "lecture" sur un lecteur muet. On le journalise, on
// remet le bouton a jour et on retente une fois au retour du reseau ou de
// l'appli au premier plan.
function tunoovaPlayWithRetry(audio) {
  return audio.play().catch((err) => {
    if (err && err.name === "AbortError") return; // source changee entre-temps
    console.warn("Tunoova : lecture refusee", err);
    const toggle = document.getElementById("miniplayer-toggle");
    if (toggle) tunoovaSetMiniToggle(toggle, false);
    const src = audio.getAttribute("src");
    const retry = () => {
      window.removeEventListener("online", retry);
      document.removeEventListener("visibilitychange", onVisible);
      if (audio.paused && audio.getAttribute("src") === src) {
        audio.play().catch((e) => console.warn("Tunoova : nouvel echec de lecture", e));
      }
    };
    const onVisible = () => {
      if (document.visibilityState === "visible") retry();
    };
    window.addEventListener("online", retry);
    document.addEventListener("visibilitychange", onVisible);
  });
}

// Morceau precharge correspondant a `playUrl`, s'il succede bien au morceau en
// cours (sinon null).
function tunoovaTakePreloaded(playUrl) {
  const miniplayer = document.getElementById("miniplayer");
  const next = tunoovaNextTrack;
  if (!miniplayer || miniplayer.dataset.casting === "1") return null;
  if (!next || !next.data || next.forPlayUrl !== miniplayer.dataset.playUrl) return null;
  if (playUrl && next.playUrl !== new URL(playUrl, location.href).pathname) return null;
  return next;
}

// Lance le morceau precharge : changement de source synchrone, sans requete
// prealable. Le GET /data fait par tunoovaUpdateMiniplayerInfo (via
// tunoovaPrefetchPlayData) en fait ensuite l'item courant cote serveur.
function tunoovaPlayPreloaded(next) {
  const audio = document.getElementById("miniplayer-audio");
  if (!audio) return;
  clearTimeout(tunoovaPreloadTimer);
  tunoovaNextTrack = null;
  next.abort.abort(); // telechargement pas encore fini : on lit le flux directement
  tunoovaSetAudioSrc(audio, next.blobUrl || next.data.stream_url, !!next.blobUrl);
  tunoovaPlayWithRetry(audio);

  const overlay = document.getElementById("play-overlay");
  if (overlay && !overlay.hidden) {
    history.replaceState({ tunoovaOverlay: true }, "", next.playUrl);
    tunoovaFillPlayOverlay(next.data, {});
  }
  tunoovaUpdateMiniplayerInfo(tunoovaOverlayDataToMiniplayer(next.data));
}

// Precedent/Suivant (boutons du panneau, notification Android) : morceau
// precharge si c'est lui, sinon meme chemin qu'avant (relecture de /data).
function tunoovaGoToTrack(playUrl) {
  const preloaded = tunoovaTakePreloaded(playUrl);
  if (preloaded) {
    tunoovaPlayPreloaded(preloaded);
    return;
  }
  const overlay = document.getElementById("play-overlay");
  if (overlay && !overlay.hidden) {
    // Panneau Lecture ouvert : il affiche aussi le nouveau morceau.
    history.replaceState({ tunoovaOverlay: true }, "", playUrl);
    tunoovaLoadPlayOverlay(playUrl, { switchTrack: true });
    return;
  }
  tunoovaFetchJson(playUrl + "/data")
    .then((data) => {
      const miniplayer = document.getElementById("miniplayer");
      const info = tunoovaOverlayDataToMiniplayer(data);
      if (miniplayer && miniplayer.dataset.casting === "1") tunoovaRecastToActiveDevice(info);
      else tunoovaApplyMiniplayer(info);
    })
    .catch(() => {});
}

// Precedent/Suivant depuis la notification : relit le contexte du morceau en
// cours (cache du panneau si a jour).
function tunoovaSkipTrack(direction) {
  const miniplayer = document.getElementById("miniplayer");
  if (!miniplayer || !miniplayer.dataset.playUrl) return;
  const playUrl = miniplayer.dataset.playUrl;
  if (direction === "next" && tunoovaTakePreloaded(null)) {
    tunoovaPlayPreloaded(tunoovaNextTrack);
    return;
  }
  const key = direction === "next" ? "next_play_url" : "prev_play_url";
  const current = tunoovaPlayDataCache && tunoovaPlayDataCache.url === playUrl
    ? Promise.resolve(tunoovaPlayDataCache.data)
    : tunoovaFetchJson(playUrl + "/data");
  current.then((data) => data[key] && tunoovaGoToTrack(data[key])).catch(() => {});
}

// Enchainement automatique en lecture locale (equivalent navigateur de
// cast_service._advance_or_stop) : morceau precharge si disponible, sinon
// ancien chemin - relit le contexte de l'item courant (/play/.../data) pour
// trouver le suivant (next_play_url, meme logique que le bouton Suivant -
// cf. play_service.get_context), puis le lance. Rien a faire pour une radio
// (jamais "ended") ou en Cast (le serveur enchaine lui-meme sur l'enceinte).
// `onEnd` : appele en fin de liste (aucun suivant).
function tunoovaPlayNextAfterEnded(onEnd) {
  const miniplayer = document.getElementById("miniplayer");
  if (!miniplayer || miniplayer.dataset.casting === "1" || !miniplayer.dataset.playUrl) return;

  const preloaded = tunoovaTakePreloaded(null);
  if (preloaded) {
    tunoovaPlayPreloaded(preloaded);
    return;
  }
  tunoovaFetchJson(miniplayer.dataset.playUrl + "/data")
    .then((data) => {
      if (data.next_play_url) tunoovaGoToTrack(data.next_play_url);
      else if (onEnd) onEnd();
    })
    .catch((err) => {
      console.warn("Tunoova : enchainement impossible", err);
      if (onEnd) onEnd(); // le bouton ne doit pas pretendre jouer
    });
}

// Reprise apres une longue pause (appli reduite, onglet en veille) : l'<audio>
// garde la connexion de la source, or l'URL googlevideo d'un morceau YouTube
// Music (relayee par /media/ytmusic/) expire au bout de quelques heures,
// et le navigateur/l'OS coupe de toute facon la connexion
// d'un media en pause depuis longtemps - play() ne reprenait alors plus rien
// jusqu'au rechargement de la page. On recharge la source d'origine (attribut
// src, l'URL Tunoova et non la redirection) a la meme position.
const TUNOOVA_STALE_PAUSE_MS = 2 * 60 * 1000;
let tunoovaPausedAt = 0;
let tunoovaLastReload = 0;

function tunoovaReloadAudio(audio) {
  const src = audio.getAttribute("src");
  if (!src) return;
  const live = !isFinite(audio.duration); // radio : repartir du direct
  const pos = audio.currentTime || 0;
  tunoovaLastReload = Date.now();
  audio.src = src;
  audio.load();
  if (!live && pos > 0) {
    audio.addEventListener("loadedmetadata", () => { audio.currentTime = pos; }, { once: true });
  }
}

function tunoovaResumeAudio(audio) {
  const stale = audio.error
    || audio.networkState === HTMLMediaElement.NETWORK_NO_SOURCE
    || (tunoovaPausedAt && Date.now() - tunoovaPausedAt > TUNOOVA_STALE_PAUSE_MS);
  if (stale) tunoovaReloadAudio(audio);
  tunoovaPausedAt = 0;
  return audio.play().catch(() => {
    if (stale) return;
    tunoovaReloadAudio(audio);
    return audio.play().catch(() => {});
  });
}

// `startPosition` (secondes, optionnel) : reprise a cet endroit plutot qu'au
// debut - retour Cast -> local, position remontee par l'enceinte (/cast/stop).
function tunoovaApplyMiniplayer(data, startPosition) {
  if (!data || !data.stream_url) return;
  const audio = document.getElementById("miniplayer-audio");
  const toggle = document.getElementById("miniplayer-toggle");
  if (!audio) return;

  tunoovaSetAudioSrc(audio, data.stream_url, false);
  if (startPosition > 0) {
    audio.addEventListener("loadedmetadata", () => { audio.currentTime = startPosition; }, { once: true });
  }
  tunoovaPlayWithRetry(audio);
  if (toggle) tunoovaSetMiniToggle(toggle, true);

  tunoovaUpdateMiniplayerInfo(data);
}

// data.stream_url (JSON de /play/<type>/<id>/data, cf. play_service.to_overlay_json)
// pointe vers la source LOCALE (navigateur) - jamais utilise pour le Cast, qui
// resout systematiquement vers /media/cast/... cote serveur (CDC §2, cf.
// app/cast/routes.py::select). Utilise pour mapper le JSON du panneau Play
// vers le format attendu par tunoovaApplyMiniplayer/tunoovaUpdateMiniplayerInfo.
function tunoovaOverlayDataToMiniplayer(data) {
  return {
    stream_url: data.stream_url,
    title: data.title,
    subtitle: data.subtitle,
    thumbnail: data.cover && data.cover.type !== "placeholder" ? data.cover.url : null,
    play_url: data.play_url,
  };
}

// Piste changee (Precedent/Suivant dans le panneau Play, ou nouveau contenu
// lance depuis une liste Podcast/Radio/Musique/Home/Notifications) alors
// qu'une diffusion Cast est active : le serveur a deja avance Play.ItemId
// (chaque route de demarrage de lecture appelle play_service.set_current_play
// avant de repondre) - on repousse donc simplement ce nouvel item courant
// vers le meme device (/cast/select le relit depuis la table Play), sans
// jamais demarrer l'<audio> local pendant que le Cast est actif.
// `infoData` est deja au format attendu par tunoovaUpdateMiniplayerInfo
// (stream_url/title/subtitle/thumbnail/play_url) - a l'appelant de le
// produire (tunoovaOverlayDataToMiniplayer pour le JSON du panneau Play, tel
// quel pour celui des routes .../play qui a deja cette forme).
function tunoovaRecastToActiveDevice(infoData) {
  const miniplayer = document.getElementById("miniplayer");
  const deviceId = miniplayer && miniplayer.dataset.castDeviceId;
  tunoovaUpdateMiniplayerInfo(infoData);
  if (!deviceId) return;

  fetch("/cast/select", {
    method: "POST",
    headers: {
      "X-CSRFToken": tunoovaCsrfToken(),
      "X-Requested-With": "XMLHttpRequest",
      "Content-Type": "application/x-www-form-urlencoded",
    },
    body: new URLSearchParams({ device_id: deviceId }),
  })
    .then((r) => r.json())
    .then((res) => {
      if (res.ok) tunoovaSetCasting(res.device_label, true, res.device_id);
    })
    .catch(() => {});
}

// Cache local (un seul morceau a la fois) des donnees du panneau "Lecture en
// cours" pour l'item actuellement en lecture. But : quand l'utilisateur clique
// le mixer, remplir le panneau instantanement depuis ce cache au lieu
// d'attendre une requete - une coupure audible restait sensible sur un
// appareil/reseau reel a l'ouverture (constate par l'utilisateur) meme sans
// aucun reset de position, le simple aller-retour reseau + travail de DOM
// suffisant a saccader la lecture. Sans risque pour la radio en direct : on
// ne precharge ici que les metadonnees (titre, pochette, compteurs...),
// jamais le flux audio lui-meme.
// --- Fenetre Commentaires de l'ecran Play (cf. play/_screen_body.html) ---
// Musique : fil de commentaires de tous les utilisateurs, recharge a chaque
// ouverture ; podcast/radio : simple commentaire personnel (textarea).
function tunoovaOpenComments() {
  const modal = document.getElementById("play-comment-modal");
  const list = document.getElementById("play-comment-list");
  const empty = document.getElementById("play-comment-empty");
  list.hidden = true;
  empty.hidden = true;
  modal.showModal();
  if (!modal.dataset.thread || !modal.dataset.commentsUrl) return;
  fetch(modal.dataset.commentsUrl, { headers: { "X-Requested-With": "XMLHttpRequest" } })
    .then((r) => r.json())
    .then(tunoovaRenderComments)
    .catch(() => {});
}

function tunoovaPostComment(url, body) {
  return fetch(url, {
    method: "POST",
    headers: {
      "X-CSRFToken": tunoovaCsrfToken(),
      "X-Requested-With": "XMLHttpRequest",
      "Content-Type": "application/x-www-form-urlencoded",
    },
    body,
  })
    .then((r) => r.json())
    .then((data) => {
      if (data.comments) tunoovaRenderComments(data);
      else tunoovaSetCommentState(data);
      return data;
    })
    .catch(() => {});
}

function tunoovaRenderComments(data) {
  const modal = document.getElementById("play-comment-modal");
  const list = document.getElementById("play-comment-list");
  const deleteIcon = document.getElementById("play-comment-delete-icon");
  const dateFormat = new Intl.DateTimeFormat(document.documentElement.lang || undefined, {
    dateStyle: "short",
    timeStyle: "short",
  });
  list.replaceChildren();
  (data.comments || []).forEach((c) => {
    const li = document.createElement("li");
    li.className = "play-comment-item";
    const meta = document.createElement("div");
    meta.className = "play-comment-meta";
    const author = document.createElement("span");
    author.className = "play-comment-author";
    author.textContent = c.author;
    const date = document.createElement("span");
    date.textContent = dateFormat.format(new Date(c.created_at));
    meta.append(author, date);
    if (c.delete_url) {
      const del = document.createElement("button");
      del.type = "button";
      del.className = "play-comment-delete";
      del.dataset.deleteUrl = c.delete_url;
      del.title = modal.dataset.labelDelete;
      del.setAttribute("aria-label", modal.dataset.labelDelete);
      del.append(deleteIcon.content.cloneNode(true));
      meta.append(del);
    }
    const body = document.createElement("p");
    body.className = "play-comment-body";
    body.textContent = c.text;
    li.append(meta, body);
    list.append(li);
  });
  list.hidden = !list.children.length;
  document.getElementById("play-comment-empty").hidden = !!list.children.length;
  tunoovaSetCommentState(data);
}

function tunoovaSetCommentState(data) {
  if (data.comment_count === undefined) return;
  document.getElementById("play-comment-count").textContent = data.comment_count || 0;
  document.getElementById("play-comment-btn").classList.toggle("active", !!data.comment_active);
}

// --- Fenetre Partager : Envoyer actif des qu'un utilisateur est coche ---
function tunoovaUpdateShareSubmit() {
  const form = document.getElementById("play-share-form");
  if (!form) return;
  document.getElementById("play-share-submit").disabled = !form.querySelector('input[name="to_users_id"]:checked');
}

function tunoovaResetShareForm() {
  const form = document.getElementById("play-share-form");
  if (!form) return;
  form.querySelectorAll('input[name="to_users_id"]').forEach((cb) => (cb.checked = false));
  document.getElementById("play-share-message").hidden = true;
  tunoovaUpdateShareSubmit();
}

var tunoovaPlayDataCache = null; // { url, data } - var, cf. commentaire sur tunoovaOverlayPushed plus bas

function tunoovaPrefetchPlayData(playUrl) {
  fetch(playUrl + "/data", { headers: { Accept: "application/json" } })
    .then((r) => (r.ok ? r.json() : null))
    .then((data) => {
      if (!data) return;
      tunoovaPlayDataCache = { url: playUrl, data };
      // Resynchronise la barre du bas depuis cette meme source fiable (celle
      // qu'utilise deja le panneau "Lecture en cours", jamais prise en
      // defaut) - filet de securite au cas ou la mise a jour faite directement
      // dans tunoovaApplyMiniplayer() n'aurait pas ete appliquee.
      const miniplayer = document.getElementById("miniplayer");
      if (!miniplayer || miniplayer.dataset.playUrl !== playUrl) {
        return; // un autre morceau a demarre entre-temps
      }
      const title = document.querySelector(".miniplayer-title");
      const subtitle = document.querySelector(".miniplayer-subtitle");
      const thumb = document.getElementById("miniplayer-thumb");
      if (title) title.textContent = data.title || "";
      if (subtitle) subtitle.textContent = data.subtitle || "";
      if (thumb) {
        if (data.cover && data.cover.type !== "placeholder") {
          thumb.src = data.cover.url;
          thumb.hidden = false;
        } else {
          thumb.hidden = true;
        }
      }
    })
    .catch(() => {});
}

// Titre en cours d'une radio (metadonnees ICY, cf. radio_service.now_playing) :
// relu toutes les 30 s tant qu'une radio joue (en local ou en Cast), affiche
// en sous-titre de la barre du bas et du panneau Lecture.
function tunoovaRefreshRadioNowPlaying() {
  const miniplayer = document.getElementById("miniplayer");
  const audio = document.getElementById("miniplayer-audio");
  if (!miniplayer) return;
  const playUrl = miniplayer.dataset.playUrl || "";
  const match = /\/play\/radio\/(\d+)$/.exec(playUrl);
  if (!match) return;
  const playing = miniplayer.dataset.casting === "1" ? miniplayer.dataset.castPlaying === "1" : audio && !audio.paused;
  if (!playing) return;
  fetch("/radio/" + match[1] + "/now-playing", { headers: { Accept: "application/json" } })
    .then((r) => (r.ok ? r.json() : null))
    .then((data) => {
      if (!data || miniplayer.dataset.playUrl !== playUrl) return; // une autre lecture a demarre entre-temps
      const subtitle = document.querySelector(".miniplayer-subtitle");
      if (subtitle) subtitle.textContent = data.subtitle;
      const screen = document.getElementById("play-screen");
      const playSubtitle = document.getElementById("play-subtitle");
      if (screen && playSubtitle && screen.dataset.contentType === "radio" && screen.dataset.itemId === match[1]) {
        playSubtitle.textContent = data.subtitle;
      }
      if ("mediaSession" in navigator && navigator.mediaSession.metadata) {
        navigator.mediaSession.metadata.artist = data.subtitle;
      }
    })
    .catch(() => {});
}

// window.* : ce script est rejoue a chaque swap htmx, un seul minuteur suffit.
if (!window.tunoovaNowPlayingTimer) {
  window.tunoovaNowPlayingTimer = setInterval(tunoovaRefreshRadioNowPlaying, 30000);
}

// Ouvre le panneau "Lecture en cours" pour le contenu en cours (clic sur la
// miniature/les details ou l'icone mixer du mini-player, CDC §7).
//
// Historique : ceci passait d'abord par un window.location.href (vrai
// rechargement -> coupure + redemarrage constates), puis par une navigation
// htmx boostee simulee (plus de redemarrage, mais coupure audible persistante
// signalee par l'utilisateur meme sans reset de position - le simple fait de
// faire une requete de page + remplacer #app-boost-root suffit a saccader le
// flux sur un appareil/reseau reel). Solution retenue : aucune navigation du
// tout. #play-overlay (cf. base.html) est affiche par-dessus l'ecran courant
// et rempli via GET /play/<type>/<id>/data - #miniplayer-audio n'est jamais
// touche pour l'ouverture/fermeture d'un item deja en cours.
function tunoovaOpenPlayer() {
  const miniplayer = document.getElementById("miniplayer");
  if (!miniplayer || !miniplayer.dataset.playUrl) return;
  tunoovaShowPlayOverlay(miniplayer.dataset.playUrl);
}

// var (pas let/const) : htmx rejoue ce script au complet lors d'une
// restauration d'historique (bouton precedent/suivant du navigateur, qui
// passe par le cache d'historique interne de htmx plutot que par une vraie
// requete/swap - deja documente dans app/__init__.py::inject_current_play)
// - une redeclaration via let/const y leverait une SyntaxError a chaque fois.
var tunoovaOverlayPushed = false;

function tunoovaShowPlayOverlay(playUrl) {
  const overlay = document.getElementById("play-overlay");
  if (!overlay) {
    // Repli (ex. page /play/... complete deja affichee, panneau absent) :
    // comportement d'origine.
    console.error("tunoovaShowPlayOverlay: #play-overlay introuvable, repli sur navigation complete", playUrl);
    window.location.href = playUrl;
    return;
  }
  overlay.hidden = false;
  const miniplayer = document.getElementById("miniplayer");
  if (miniplayer) miniplayer.classList.add("miniplayer-hidden");
  history.pushState({ tunoovaOverlay: true }, "", playUrl);
  tunoovaOverlayPushed = true;

  // Remplissage (textes, pochette...) reporte a la frame suivante : rendre le
  // panneau visible (overlay.hidden = false ci-dessus) force deja un reflow
  // de tout son contenu - y ajouter la MEME frame ce travail supplementaire
  // (plusieurs ecritures DOM + decodage d'image) pouvait, sur un appareil
  // moins puissant, saccader brievement la lecture en cours (signale par
  // l'utilisateur : pas de coupure nette ni de redemarrage, donc contention
  // du thread principal plutot qu'un probleme sur <audio> lui-meme).
  requestAnimationFrame(function () {
    // Remplissage instantane depuis le cache si on l'a deja (cf.
    // tunoovaPrefetchPlayData) - la requete refaite juste apres ne fait que
    // rafraichir des donnees potentiellement legerement perimees (compteurs
    // de like, etc.), sans jamais faire attendre l'ouverture elle-meme.
    if (tunoovaPlayDataCache && tunoovaPlayDataCache.url === playUrl) {
      tunoovaFillPlayOverlay(tunoovaPlayDataCache.data, {});
    }
    tunoovaLoadPlayOverlay(playUrl, {});
  });
}

function tunoovaLoadPlayOverlay(playUrl, opts) {
  fetch(playUrl + "/data", { headers: { Accept: "application/json" } })
    .then((r) => {
      if (!r.ok) throw new Error("play data " + r.status);
      return r.json();
    })
    .then((data) => {
      tunoovaPlayDataCache = { url: playUrl, data };
      tunoovaFillPlayOverlay(data, opts);
    })
    .catch((err) => {
      if (!(tunoovaPlayDataCache && tunoovaPlayDataCache.url === playUrl)) {
        // Ce repli recharge une vraie page (audio recree, coupure possible) -
        // journalise pour comprendre pourquoi le panneau n'a pas pu se
        // remplir sans naviguer (l'echec lui-meme etant sinon invisible,
        // avale par ce catch).
        console.error("tunoovaLoadPlayOverlay: echec du panneau, repli sur navigation complete", playUrl, err);
        window.location.href = playUrl;
      }
    });
}

function tunoovaFillPlayOverlay(data, opts) {
  opts = opts || {};
  const screen = document.getElementById("play-screen");
  if (!screen) return;
  screen.dataset.contentType = data.content_type;
  screen.dataset.itemId = data.item_id;

  // Resynchronise le badge Cast (mini-player + panneau) sur l'etat REEL cote
  // serveur a chaque ouverture/rafraichissement du panneau, plutot que de se
  // fier a un etat JS qui a pu diverger entre-temps (ex. diffusion arretee
  // depuis un autre onglet/appareil) - "diffuse toujours indique comme actif
  // en revenant sur l'ecran alors que la lecture est repassee en local"
  // corrige ici en reancrant systematiquement sur la verite serveur.
  const miniplayerEl = document.getElementById("miniplayer");
  if (data.cast_device_label) {
    tunoovaSetCasting(data.cast_device_label, data.playing, miniplayerEl ? miniplayerEl.dataset.castDeviceId : undefined);
  } else if (miniplayerEl && miniplayerEl.dataset.casting === "1") {
    tunoovaClearCasting();
  }

  const titleEl = document.getElementById("play-title");
  const subtitleEl = document.getElementById("play-subtitle");
  titleEl.textContent = data.overlay_title;
  titleEl.classList.toggle("play-title-podcast", !!data.podcast_style);
  subtitleEl.textContent = data.overlay_subtitle;
  subtitleEl.classList.toggle("play-subtitle-episode", !!data.podcast_style);

  const coverContainer = document.getElementById("play-cover");
  coverContainer.innerHTML = "";
  if (data.cover && data.cover.type !== "placeholder") {
    const img = document.createElement("img");
    img.id = "play-cover-img";
    img.alt = "";
    img.src = data.cover.url;
    if (data.cover.type === "cover") {
      img.onerror = function () {
        const span = document.createElement("span");
        span.className = "play-cover-placeholder";
        span.id = "play-cover-placeholder";
        span.textContent = data.cover.placeholder || "";
        img.replaceWith(span);
      };
    }
    coverContainer.appendChild(img);
  } else {
    const span = document.createElement("span");
    span.className = "play-cover-placeholder";
    span.id = "play-cover-placeholder";
    span.textContent = (data.cover && data.cover.placeholder) || "";
    coverContainer.appendChild(span);
  }

  document.getElementById("play-like-btn").classList.toggle("active", !!data.like_active);
  document.getElementById("play-not-like-btn").classList.toggle("active", !!data.not_like_active);
  document.getElementById("play-like-count").textContent = data.like_count;
  document.getElementById("play-not-like-count").textContent = data.not_like_count;
  document.getElementById("play-comment-text").value = data.comment_text || "";
  tunoovaSetCommentState(data);
  document.getElementById("play-listen-count").hidden = data.listen_count == null;
  document.getElementById("play-listen-count-value").textContent = data.listen_count ?? "";
  const commentModal = document.getElementById("play-comment-modal");
  commentModal.dataset.thread = data.comment_thread ? "1" : "";
  commentModal.dataset.commentsUrl = data.comments_url || "";
  document.getElementById("play-comment-submit").textContent = data.comment_thread
    ? commentModal.dataset.labelPublish
    : commentModal.dataset.labelSave;

  document.getElementById("play-like-form").action = data.like_url;
  document.getElementById("play-comment-form").action = data.comment_url;
  const shareForm = document.getElementById("play-share-form");
  if (shareForm) shareForm.action = data.share_url;
  tunoovaResetShareForm();
  const sharePrompt = document.getElementById("play-share-prompt");
  if (sharePrompt) sharePrompt.textContent = sharePrompt.dataset[data.content_type] || "";

  document.getElementById("play-progress").hidden = !data.show_progress;
  document.getElementById("play-transport").hidden = !data.show_progress;

  const prevBtn = document.getElementById("play-prev-btn");
  // Lien masque par le survol (cf. tunoovaRestoreHref) : l'ancienne URL
  // memorisee serait sinon remise a la sortie de la souris.
  prevBtn.removeAttribute("data-tunoova-href");
  if (data.prev_play_url) {
    prevBtn.href = data.prev_play_url;
    prevBtn.dataset.itemId = data.prev_id;
    prevBtn.classList.remove("disabled");
  } else {
    prevBtn.removeAttribute("href");
    delete prevBtn.dataset.itemId;
    prevBtn.classList.add("disabled");
  }
  const nextBtn = document.getElementById("play-next-btn");
  nextBtn.removeAttribute("data-tunoova-href");
  if (data.next_play_url) {
    nextBtn.href = data.next_play_url;
    nextBtn.dataset.itemId = data.next_id;
    nextBtn.classList.remove("disabled");
  } else {
    nextBtn.removeAttribute("href");
    delete nextBtn.dataset.itemId;
    nextBtn.classList.add("disabled");
  }

  // Ouverture normale du panneau pour l'item deja en cours : ne jamais
  // toucher a l'element <audio> (c'est tout le sens de ce panneau). Seul un
  // vrai changement de morceau (Precedent/Suivant) doit (re)lancer la lecture -
  // en Cast, sur le device actif plutot que localement (cf. tunoovaRecastToActiveDevice).
  if (opts.switchTrack) {
    const miniplayer = document.getElementById("miniplayer");
    if (miniplayer && miniplayer.dataset.casting === "1") {
      tunoovaRecastToActiveDevice(tunoovaOverlayDataToMiniplayer(data));
    } else {
      tunoovaApplyMiniplayer(tunoovaOverlayDataToMiniplayer(data));
    }
  }
  tunoovaSyncPlayToggleIcon();
}

// Reflete l'etat reel de #miniplayer-audio sur l'icone Lecture/Pause du
// Icone play/pause du mini-lecteur : SVG (cf. base.html) plutot que les
// caracteres ⏸/▶, qu'Android affiche en emoji couleur (orange) au lieu de
// suivre la couleur du texte.
function tunoovaSetMiniToggle(toggle, playing) {
  toggle.classList.toggle("is-playing", !!playing);
}

// panneau - appelee a la fois par les ecouteurs play/pause (changements en
// cours d'affichage) et par tunoovaFillPlayOverlay (etat au moment de
// l'ouverture, qui peut deja etre "en lecture" sans qu'aucun evenement ne
// se declenche pendant que le panneau est ouvert).
function tunoovaSyncPlayToggleIcon() {
  const playIcon = document.getElementById("play-toggle-play-icon");
  const pauseIcon = document.getElementById("play-toggle-pause-icon");
  if (!playIcon || !pauseIcon) return;
  const playing = tunoovaIsPlaying();
  playIcon.hidden = playing;
  pauseIcon.hidden = !playing;
}

function tunoovaClosePlayOverlay() {
  const overlay = document.getElementById("play-overlay");
  if (overlay) overlay.hidden = true;
  const miniplayer = document.getElementById("miniplayer");
  if (miniplayer) miniplayer.classList.remove("miniplayer-hidden");
  // Deux cas retombent sur un vrai retour navigateur : le panneau a ete
  // ouvert via JS et l'etat d'historique correspond bien (cas normal), OU il
  // n'a jamais ete ouvert via JS du tout (!tunoovaOverlayPushed) - c'est
  // alors la page complete /play/... chargee directement (lien, partage,
  // rafraichissement, cf. app/templates/play/index.html) qui s'affiche ici
  // sans jamais avoir fait de history.pushState, et un bouton retour dessus
  // doit se comporter comme sur n'importe quel autre ecran.
  if (!tunoovaOverlayPushed || (history.state && history.state.tunoovaOverlay)) {
    history.back();
  }
  tunoovaOverlayPushed = false;
}

window.addEventListener("popstate", function (e) {
  if (!(e.state && e.state.tunoovaOverlay)) {
    const overlay = document.getElementById("play-overlay");
    if (overlay && !overlay.hidden) {
      overlay.hidden = true;
      const miniplayer = document.getElementById("miniplayer");
      if (miniplayer) miniplayer.classList.remove("miniplayer-hidden");
    }
    tunoovaOverlayPushed = false;
  }
});

// Ecrase la restauration d'historique par defaut de htmx (bouton Precedent/
// Suivant du navigateur) : elle remplace TOUT le contenu de <body> par un
// instantane en cache (fonction interne Ue() sur document.body, verifie en
// lisant htmx.min.js) - un mecanisme totalement distinct des navigations
// normales (qui ciblent #app-boost-root via hx-target/hx-select sur <body>),
// et qui echappe donc entierement a la protection voulue pour #miniplayer/
// #play-overlay (voir commentaire plus haut : "vit hors de #app-boost-root
// pour ne jamais etre detache/rattache par un swap"). Constate (Playwright) :
// une simple navigation Retour pendant une lecture recreait #miniplayer-audio
// depuis l'instantane cache, coupant et remettant la lecture a 0 - meme sans
// jamais toucher au panneau "Lecture en cours".
//
// htmx assigne son propre gestionnaire via la PROPRIETE window.onpopstate
// (pas addEventListener), mais seulement lors de son initialisation interne,
// differee elle-meme jusqu'a DOMContentLoaded (verifie en lisant
// htmx.min.js : jr() n'execute la fonction tout de suite que si le document
// est deja pret, sinon elle attend cet evenement). Ecraser window.onpopstate
// directement ici (au niveau racine du script, execute AVANT
// DOMContentLoaded puisque ce script est en fin de <body>) se faisait donc
// ecraser en retour par l'assignation - plus tardive - de htmx elle-meme
// (constate via Playwright : la coupure persistait malgre cette premiere
// tentative). Attendre nous aussi DOMContentLoaded - htmx s'etant enregistre
// en premier (son <script> est dans <head>, donc DOMContentLoaded declenche
// d'abord son propre gestionnaire) - garantit que notre reaffectation
// s'execute en dernier et l'emporte durablement.
function tunoovaInstallPopstateOverride() {
  window.onpopstate = function (e) {
    if (e.state && e.state.htmx) {
      // htmx.ajax() reste l'API publique normale, ciblee/filtree exactement
      // comme une navigation boostee classique (jamais de cache local, donc
      // toujours une requete reseau fraiche - meme compromis que
      // hx-history="false" deja utilise ailleurs, applique ici partout et
      // sans jamais toucher a #miniplayer/#play-overlay).
      htmx.ajax("GET", location.pathname + location.search, {
        target: "#app-boost-root",
        select: "#app-boost-root",
        swap: "outerHTML",
      });
    }
  };
}
if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", tunoovaInstallPopstateOverride);
} else {
  tunoovaInstallPopstateOverride();
}

// L'utilisateur peut cliquer un lien de la barre de nav (reste visible/
// cliquable sous le panneau, cf. CSS #play-overlay) pendant que le panneau
// est ouvert : la navigation normale doit alors se derouler sans que le
// panneau ne reste affiche par-dessus.
document.body.addEventListener("htmx:beforeRequest", function (e) {
  if (!(e.detail && e.detail.target && e.detail.target.id === "app-boost-root")) return;
  const overlay = document.getElementById("play-overlay");
  if (overlay && !overlay.hidden) {
    overlay.hidden = true;
    const miniplayer = document.getElementById("miniplayer");
    if (miniplayer) miniplayer.classList.remove("miniplayer-hidden");
    tunoovaOverlayPushed = false;
  }
});

// Cablage du contenu du panneau (identique a app/templates/play/_screen_body.html,
// present sur toutes les pages sauf la page complete /play/... elle-meme -
// cf. base.html). Fonctions ci-dessus, ce IIFE ne fait que brancher les
// gestionnaires une seule fois (le panneau n'est jamais recree).
(function () {
  const overlay = document.getElementById("play-overlay");
  if (!overlay) return;

  // Meme garde que pour #miniplayer-toggle plus haut : ce script peut etre
  // rejoue par htmx (restauration d'historique) sur ce meme noeud, jamais
  // recree - eviter d'empiler des gestionnaires en double sur ses formulaires.
  if (overlay.dataset.bound) return;
  overlay.dataset.bound = "1";

  const likeForm = document.getElementById("play-like-form");
  if (likeForm) {
    likeForm.addEventListener("submit", function (e) {
      // stopImmediatePropagation (pas seulement preventDefault) : ce
      // formulaire est boostee par hx-boost="true" sur <body> (tout <form>
      // l'est, meme sans hx-post explicite) - htmx attache son PROPRE
      // ecouteur "submit" sur ce meme element (a l'initialisation, differee
      // a DOMContentLoaded, donc APRES celui-ci qui s'execute plus tot).
      // preventDefault seul bloque la soumission navigateur mais n'empeche
      // pas cet AUTRE ecouteur de s'executer a son tour et de soumettre lui
      // aussi une requete boostee en double - constate (Playwright) : deux
      // POST /like partaient par clic, le second recevant une redirection
      // (repli non-JS de la route) que htmx suivait en GET boostee, vidant
      // #app-boost-root (vide sur cette page, cf. play/index.html) et
      // masquant le panneau au passage (page blanche, juste la barre de
      // nav - signale par l'utilisateur).
      e.stopImmediatePropagation();
      e.preventDefault();
      const value = e.submitter ? e.submitter.value : "Like";
      fetch(likeForm.action, {
        method: "POST",
        headers: {
          "X-CSRFToken": tunoovaCsrfToken(),
          "X-Requested-With": "XMLHttpRequest",
          "Content-Type": "application/x-www-form-urlencoded",
        },
        body: new URLSearchParams({ value }),
      })
        .then((r) => r.json())
        .then((data) => {
          document.getElementById("play-like-btn").classList.toggle("active", !!data.like_active);
          document.getElementById("play-not-like-btn").classList.toggle("active", !!data.not_like_active);
          document.getElementById("play-like-count").textContent = data.like_count;
          document.getElementById("play-not-like-count").textContent = data.not_like_count;
        })
        .catch(() => {});
    });
  }

  const commentForm = document.getElementById("play-comment-form");
  if (commentForm) {
    commentForm.addEventListener("submit", function (e) {
      e.stopImmediatePropagation();
      e.preventDefault();
      const textarea = document.getElementById("play-comment-text");
      const thread = !!document.getElementById("play-comment-modal").dataset.thread;
      if (thread && !textarea.value.trim()) return;
      tunoovaPostComment(commentForm.action, new URLSearchParams({ text: textarea.value })).then(() => {
        if (thread) textarea.value = "";
        document.getElementById("play-comment-modal").close();
      });
    });
  }

  const commentList = document.getElementById("play-comment-list");
  if (commentList) {
    commentList.addEventListener("click", function (e) {
      const btn = e.target.closest("[data-delete-url]");
      if (!btn) return;
      tunoovaPostComment(btn.dataset.deleteUrl, new URLSearchParams());
    });
  }

  const shareForm = document.getElementById("play-share-form");
  if (shareForm) {
    shareForm.addEventListener("submit", function (e) {
      e.stopImmediatePropagation();
      e.preventDefault();
      const body = new URLSearchParams();
      new FormData(shareForm).getAll("to_users_id").forEach((id) => body.append("to_users_id", id));
      if (!body.has("to_users_id")) return;
      document.getElementById("play-share-submit").disabled = true;
      fetch(shareForm.action, {
        method: "POST",
        headers: {
          "X-CSRFToken": tunoovaCsrfToken(),
          "X-Requested-With": "XMLHttpRequest",
          "Content-Type": "application/x-www-form-urlencoded",
        },
        body,
      })
        .then((r) => r.json())
        .then((data) => {
          const msg = document.getElementById("play-share-message");
          msg.textContent = data.message;
          msg.hidden = false;
          if (!data.ok) {
            tunoovaUpdateShareSubmit();
            return;
          }
          // Confirmation visible un instant, puis retour a l'ecran Play.
          setTimeout(() => {
            document.getElementById("play-share-modal").close();
            tunoovaResetShareForm();
          }, 1200);
        })
        .catch(() => tunoovaUpdateShareSubmit());
    });
    shareForm.addEventListener("change", tunoovaUpdateShareSubmit);
  }

  // Ecouteur en phase de capture sur document (pas sur les boutons) : il
  // passe avant celui que hx-boost pose sur chaque lien, quel que soit
  // l'ordre d'initialisation. Sur la page complete /play/..., l'ecouteur
  // htmx passait sinon le premier : navigation boostee, panneau masque par
  // htmx:beforeRequest et #app-boost-root vide -> page blanche.
  document.addEventListener("click", function (e) {
    const btn = e.target.closest && e.target.closest("#play-prev-btn, #play-next-btn");
    if (!btn) return;
    e.stopPropagation();
    e.preventDefault();
    if (btn.classList.contains("disabled") || !btn.href) return;
    tunoovaGoToTrack(btn.href);
  }, true);

  // Icone lecture/pause + barre d'ecoute du panneau : memes elements/id que
  // app/templates/play/index.html (page complete), meme logique, mais
  // branchee une seule fois ici puisque le panneau n'est jamais recree.
  //
  // tunoovaSyncPlayToggleIcon est aussi appelee depuis tunoovaFillPlayOverlay :
  // les ecouteurs play/pause ci-dessous ne rattrapent que les changements
  // *posterieurs* a l'ouverture - sans cet appel explicite au moment de
  // remplir le panneau, l'icone restait figee sur son etat par defaut (Lecture)
  // si le morceau jouait deja avant l'ouverture et n'a pas ete rebascule depuis.
  const audio = document.getElementById("miniplayer-audio");
  if (audio) {
    audio.addEventListener("play", tunoovaSyncPlayToggleIcon);
    audio.addEventListener("pause", tunoovaSyncPlayToggleIcon);
  }

  const seek = document.getElementById("play-seek");
  const current = document.getElementById("play-time-current");
  const total = document.getElementById("play-time-total");
  if (audio && seek) {
    const syncFromAudio = function () {
      // Pendant une diffusion Cast, <audio> ne joue pas et ne reflete rien
      // (cf. tunoovaSetCasting) - la barre est alors alimentee par
      // tunoovaPollCastStatus, jamais par cet <audio> local muet.
      const miniplayer = document.getElementById("miniplayer");
      if (miniplayer && miniplayer.dataset.casting === "1") return;
      if (audio.duration) {
        seek.max = audio.duration;
        seek.value = audio.currentTime;
        if (total) total.textContent = tunoovaFormatTime(audio.duration);
      }
      if (current) current.textContent = tunoovaFormatTime(audio.currentTime);
    };
    audio.addEventListener("timeupdate", syncFromAudio);
    audio.addEventListener("loadedmetadata", syncFromAudio);
    seek.addEventListener("input", function () {
      // En Cast, rien pendant le glisser : le saut part au relachement
      // (evenement "change" ci-dessous), une seule fois.
      const miniplayer = document.getElementById("miniplayer");
      if (miniplayer && miniplayer.dataset.casting === "1") {
        tunoovaCastSeeking = true;
        if (current) current.textContent = tunoovaFormatTime(seek.value);
        return;
      }
      audio.currentTime = seek.value;
    });
    seek.addEventListener("change", function () {
      const miniplayer = document.getElementById("miniplayer");
      if (!miniplayer || miniplayer.dataset.casting !== "1") return;
      tunoovaCastSeekTo(seek.value);
    });
    // Etat initial immediat (ex. page /play/... chargee directement avec une
    // lecture deja en cours) : sans cet appel, la barre affichait 0 jusqu'au
    // prochain timeupdate natif (jusqu'a ~250ms plus tard).
    syncFromAudio();
  }
})();

(function () {
  const toggle = document.getElementById("miniplayer-toggle");
  const audio = document.getElementById("miniplayer-audio");
  const miniplayer = document.getElementById("miniplayer");
  const info = document.querySelector(".miniplayer-info");
  if (!toggle || !audio || !miniplayer) return;

  // Le mini-player est preserve (hx-preserve) d'une navigation htmx a l'autre,
  // mais ce script est reinjecte a chaque swap : evite d'empiler les listeners
  // (et de relancer la restauration ci-dessous) sur un noeud deja initialise.
  if (toggle.dataset.bound) return;
  toggle.dataset.bound = "1";

  // Chargement de page avec une lecture deja en cours (cf. current_play,
  // app/__init__.py) : precharger tout de suite les donnees du panneau, sans
  // attendre que l'utilisateur clique le mixer (cf. tunoovaPrefetchPlayData).
  if (miniplayer.dataset.playUrl) {
    tunoovaPrefetchPlayData(miniplayer.dataset.playUrl);
    tunoovaSchedulePreloadNext(miniplayer.dataset.playUrl);
  }

  // Reprise de lecture (CDC §2) : ce noeud vient d'etre rendu par le serveur
  // (chargement de page, ou restauration d'historique ou hx-preserve ne
  // s'applique pas, cf. app/__init__.py::inject_current_play). En Cast
  // (data-casting, rendu par current_play.cast_device_label), l'<audio>
  // local ne doit jamais etre relance - seul le suivi de statut du device
  // (tunoovaStartCastPolling) reprend. Sinon, reprend a la position
  // enregistree et relance la lecture locale si elle etait en cours.
  if (miniplayer.dataset.casting === "1") {
    tunoovaLoadCastVolume();
    tunoovaStartCastPolling();
  } else if (audio.src && miniplayer.dataset.position) {
    const pos = parseFloat(miniplayer.dataset.position);
    if (pos > 0) {
      audio.addEventListener(
        "loadedmetadata",
        function () {
          audio.currentTime = pos;
        },
        { once: true }
      );
    }
    if (miniplayer.dataset.playing) {
      audio.play().catch(() => {
        tunoovaSetMiniToggle(toggle, false); // lecture auto bloquee par le navigateur
      });
    }
  }

  function tunoovaTogglePlayback() {
    if (miniplayer.dataset.casting === "1") {
      fetch("/cast/playpause", {
        method: "POST",
        headers: { "X-CSRFToken": tunoovaCsrfToken(), "X-Requested-With": "XMLHttpRequest" },
      })
        .then((r) => r.json())
        .then((data) => {
          if (!data.ok) return;
          miniplayer.dataset.castPlaying = data.status === "Play" ? "1" : "0";
          if ("mediaSession" in navigator) navigator.mediaSession.playbackState = data.status === "Play" ? "playing" : "paused";
          tunoovaSetMiniToggle(toggle, data.status === "Play");
          tunoovaSyncPlayToggleIcon();
        })
        .catch(() => {});
      return;
    }
    if (audio.paused) {
      tunoovaResumeAudio(audio);
      tunoovaSetMiniToggle(toggle, true);
    } else {
      audio.pause();
      tunoovaSetMiniToggle(toggle, false);
    }
  }

  toggle.addEventListener("click", tunoovaTogglePlayback);

  // Clic sur la miniature/les details du mini-player -> ouvre l'ecran Play (CDC §7).
  if (info) {
    info.style.cursor = "pointer";
    info.addEventListener("click", tunoovaOpenPlayer);
  }

  // Reprise multi-appareils (CDC §2) : une appli remise au premier plan (PWA
  // mobile, onglet en arriere-plan) n'est pas rechargee - #miniplayer garde
  // ce qu'il jouait avant, meme si un autre appareil a joue autre chose
  // depuis. On relit alors la table Play, sauf si cet appareil joue
  // lui-meme (c'est lui la reference) ou diffuse en Cast.
  function tunoovaSyncFromServer() {
    if (miniplayer.dataset.casting === "1" || !audio.paused) return;
    fetch("/play/current", { headers: { Accept: "application/json" } })
      .then((r) => (r.ok ? r.json() : null))
      .then((cur) => {
        if (!cur || cur.cast_device_label || !audio.paused) return;
        const sameItem = cur.play_url === miniplayer.dataset.playUrl;
        if (sameItem && Math.abs((audio.currentTime || 0) - cur.position) < 5) return;
        tunoovaSetAudioSrc(audio, cur.stream_url, false);
        if (cur.position > 0) {
          audio.addEventListener("loadedmetadata", () => { audio.currentTime = cur.position; }, { once: true });
        }
        tunoovaUpdateMiniplayerInfo(cur);
        tunoovaSetMiniToggle(toggle, false);
        if (cur.playing) {
          audio.play().then(() => { tunoovaSetMiniToggle(toggle, true); }).catch(() => {}); // lecture auto peut etre bloquee
        }
      })
      .catch(() => {});
  }
  document.addEventListener("visibilitychange", () => {
    if (document.visibilityState === "visible") tunoovaSyncFromServer();
  });
  window.addEventListener("pageshow", (e) => {
    if (e.persisted) tunoovaSyncFromServer();
  });

  // Sauvegarde periodique de la position (CDC §2, reprise de lecture), throttlee a 5s.
  let lastReport = 0;
  function reportState(status) {
    const body = new URLSearchParams({ status: status, position: audio.currentTime || 0 });
    fetch("/play/state", {
      method: "POST",
      headers: { "X-CSRFToken": tunoovaCsrfToken(), "Content-Type": "application/x-www-form-urlencoded" },
      body,
      keepalive: true,
    }).catch(() => {});
  }
  // Barre de progression de la notification Android (Media Session).
  function updatePositionState() {
    if (!("mediaSession" in navigator) || !navigator.mediaSession.setPositionState) return;
    if (!isFinite(audio.duration) || !(audio.duration > 0)) return; // radio en direct
    try {
      navigator.mediaSession.setPositionState({
        duration: audio.duration,
        position: Math.min(audio.currentTime, audio.duration),
        playbackRate: audio.playbackRate || 1,
      });
    } catch (e) { /* valeurs transitoires invalides pendant un changement de source */ }
  }
  audio.addEventListener("loadedmetadata", updatePositionState);

  audio.addEventListener("timeupdate", function () {
    // En Cast, cet <audio> est deliberement muet/en pause (cf. tunoovaSetCasting) -
    // son statut ne doit jamais ecraser celui, reel, de la diffusion en cours.
    if (miniplayer.dataset.casting === "1") return;
    // Suivant ni en memoire ni deja resolu : URL googlevideo resolue
    // ~15 s avant la fin pour que l'enchainement n'attende pas yt-dlp.
    const next = tunoovaNextTrack;
    if (next && next.data && !next.blobUrl && !next.warmed
        && isFinite(audio.duration) && audio.duration - audio.currentTime < TUNOOVA_WARM_BEFORE_END_S) {
      next.warmed = true;
      tunoovaWarmStream(next.data.stream_url);
    }
    // Pont silencieux (cf. tunoovaStartSilentBridge) : lance avant la fin,
    // arrete une fois le morceau suivant (ou un retour en arriere) bien parti.
    if (!audio.paused && isFinite(audio.duration)) {
      const remaining = audio.duration - audio.currentTime;
      if (remaining < TUNOOVA_BRIDGE_BEFORE_END_S) tunoovaStartSilentBridge();
      else if (audio.currentTime > 5 && remaining > TUNOOVA_BRIDGE_BEFORE_END_S + 1) tunoovaStopSilentBridge();
    }
    const now = Date.now();
    if (now - lastReport > 5000) {
      lastReport = now;
      updatePositionState();
      // audio.paused, pas "Play" en dur : positionner audio.currentTime (ex.
      // reprise de position au chargement, juste en dessous) declenche aussi
      // un evenement timeupdate meme piste en pause - ca ecrasait le statut
      // "Pause" en base des le premier chargement de page, et une lecture
      // volontairement mise en pause repartait toute seule au chargement
      // suivant (constate par l'utilisateur).
      reportState(audio.paused ? "Pause" : "Play");
    }
  });
  audio.addEventListener("pause", () => {
    if (!audio.ended) tunoovaStopSilentBridge(); // vraie pause (utilisateur, Cast) : plus d'enchainement a couvrir
    if (miniplayer.dataset.casting === "1") return; // pause programmatique au demarrage d'un Cast (tunoovaSetCasting), pas une vraie pause utilisateur
    // Fin de morceau : le navigateur declenche "pause" juste avant "ended".
    // Ce n'est pas une pause utilisateur - ni statut "Pause" en base, ni
    // tunoovaPausedAt, ni notification en pause (Android y couperait le
    // service media pendant l'enchainement). Fin de liste : cf. "ended".
    if (audio.ended) return;
    if ("mediaSession" in navigator) navigator.mediaSession.playbackState = "paused";
    tunoovaSetMiniToggle(toggle, false);
    tunoovaPausedAt = Date.now();
    reportState("Pause");
  });
  // Source devenue illisible en cours de lecture (URL expiree, connexion
  // coupee en arriere-plan) : un rechargement automatique, pas en boucle.
  audio.addEventListener("error", () => {
    if (miniplayer.dataset.casting === "1" || Date.now() - tunoovaLastReload < 15000) return;
    tunoovaReloadAudio(audio);
    audio.play().catch(() => {});
  });
  audio.addEventListener("play", () => {
    if (miniplayer.dataset.casting === "1") return;
    if ("mediaSession" in navigator) navigator.mediaSession.playbackState = "playing";
    tunoovaSetMiniToggle(toggle, true);
    reportState("Play");
  });
  // Fin de piste en lecture locale : enchainement automatique (podcast /
  // musique), equivalent navigateur de cast_service._advance_or_stop.
  audio.addEventListener("ended", () => {
    tunoovaPlayNextAfterEnded(() => {
      // Fin de liste : vraie fin de lecture.
      tunoovaStopSilentBridge();
      tunoovaSetMiniToggle(toggle, false);
      if ("mediaSession" in navigator) navigator.mediaSession.playbackState = "paused";
      reportState("Pause");
    });
  });

  // Media Session API (CDC §2) : controles sur ecran de verrouillage / notif
  // systeme - memes actions que le bouton du mini-player (Cast ou local).
  if ("mediaSession" in navigator) {
    navigator.mediaSession.setActionHandler("play", tunoovaTogglePlayback);
    navigator.mediaSession.setActionHandler("pause", tunoovaTogglePlayback);
    try {
      navigator.mediaSession.setActionHandler("nexttrack", () => tunoovaSkipTrack("next"));
      navigator.mediaSession.setActionHandler("previoustrack", () => tunoovaSkipTrack("prev"));
    } catch (e) { /* action non prise en charge par ce navigateur */ }
  }
})();

// --------------------------------------------------------------------------
// Pas d'URL en bas de l'ecran au survol d'un lien : le navigateur affiche
// l'adresse de tout <a href> survole (barre d'etat, impossible a masquer en
// CSS). Le href est retire pendant le survol (data-tunoova-href) puis remis
// en sortie, et remis le temps du clic (cf. ecouteur "click" ci-dessous).
// --------------------------------------------------------------------------

function tunoovaRestoreHref(link) {
  if (link.hasAttribute("data-tunoova-href")) {
    link.setAttribute("href", link.getAttribute("data-tunoova-href"));
    link.removeAttribute("data-tunoova-href");
  }
}

document.addEventListener("mouseover", function (e) {
  const link = e.target.closest && e.target.closest("a[href]");
  if (!link || !link.matches(":hover")) return;
  link.setAttribute("data-tunoova-href", link.getAttribute("href"));
  link.removeAttribute("href");
});

document.addEventListener("mouseout", function (e) {
  const link = e.target.closest && e.target.closest("a[data-tunoova-href]");
  if (link && !link.contains(e.relatedTarget)) tunoovaRestoreHref(link);
});

// Au clic, le href est remis AVANT tout autre gestionnaire (capture sur
// window, qui passe avant ceux poses sur document ou sur les liens) : ils
// lisent ainsi le vrai lien (ex. Precedent/Suivant de l'ecran Play, qui
// sinon ne trouvait pas de href et laissait htmx naviguer vers une page
// blanche), et le navigateur suit normalement les liens non boostes. Le
// href est retire de nouveau si la souris est toujours sur le lien.
window.addEventListener("click", function (e) {
  const link = e.target.closest && e.target.closest("a[data-tunoova-href]");
  if (!link) return;
  tunoovaRestoreHref(link);
  setTimeout(function () {
    if (link.isConnected && link.hasAttribute("href") && link.matches(":hover")) {
      link.setAttribute("data-tunoova-href", link.getAttribute("href"));
      link.removeAttribute("href");
    }
  }, 0);
}, true);

// --------------------------------------------------------------------------
// Champs de recherche : ils ne sont pas dans un <form>, donc la touche
// "Rechercher" du clavier virtuel ne fait rien d'autre que declencher
// l'evenement "search" (ecoute par htmx) et le champ garde le focus - le
// clavier reste affiche sur mobile. On retire le focus pour le fermer, au
// keyup pour ne pas empecher l'evenement "search" (emis apres le keydown).
// --------------------------------------------------------------------------

document.addEventListener("keyup", function (e) {
  if (e.key === "Enter" && e.target.matches && e.target.matches('input[type="search"]')) {
    e.target.blur();
  }
});
