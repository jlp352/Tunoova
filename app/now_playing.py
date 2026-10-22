"""Titre en cours d'une radio (emission ou morceau), via des plug-ins.

Un plug-in = un script Python du dossier radio_plugins/ (une ligne
RadioPlugIn par script). Contrat :

    NAME = "Radio France"                      # nom affiche dans l'Admin
    def detect(stream_url, name) -> str | None
        # identifiant de la station chez ce fournisseur si le plug-in gere
        # cette radio ("" s'il n'en a pas besoin), None sinon
    def fetch(ref, stream_url, api_key) -> dict | None
        # ce qui passe a l'antenne, au format standard :
        # {"kind": "show" | "song", "show": "...", "detail": "..." | None,
        #  "image_url": "..." | None, "start": epoch | None, "end": epoch | None}
    SUPPORTED_STATIONS = [("France Inter", "1"), ...]   # facultatif : radios
        # prises en charge (nom affiche, Station ID), listees dans l'Admin

Chaque radio du catalogue (Radio) pointe directement vers son plug-in
(RadioPlugInId) : a l'ecoute, un seul fournisseur est interroge. La detection
(qui essaie les plug-ins un par un) a lieu a la premiere ecoute de la radio,
puis pour toutes les radios sans plug-in des qu'un plug-in est installe,
reactive ou remis a une version precedente.

Radio sans plug-in, ou plug-in en echec : repli sur les metadonnees ICY du
flux (app/icy.py), affichees seulement si le flux en envoie.
"""

import importlib.util
import logging
import os
import re
import shutil
import time
from datetime import datetime

from app import icy
from app.config import BASE_DIR
from app.extensions import db
from app.i18n import translate
from app.models import Radio, RadioPlugIn

logger = logging.getLogger(__name__)

PLUGINS_DIR = os.path.join(BASE_DIR, "radio_plugins")
OLD_VERSIONS_DIR = os.path.join(PLUGINS_DIR, "old")
# Plug-ins supprimes depuis l'Admin : un fichier <script>.removed dans
# radio_plugins/old/ (ignore par git). Sans lui, un plug-in fourni avec
# l'appli reviendrait a la mise a jour suivante (git reset --hard).
REMOVED_SUFFIX = ".removed"
CACHE_TTL = 30
EMPTY_CACHE_TTL = 120  # rien a l'antenne / plug-in en panne : inutile de reessayer a chaque rafraichissement
SCRIPT_RE = re.compile(r"^[a-z][a-z0-9_]{0,60}\.py$")

_modules: dict[str, tuple[float, object]] = {}  # script -> (mtime, module)
_cache: dict[str, tuple[dict | None, float]] = {}  # stream_url -> (resultat, expiration)


class PlugInError(Exception):
    pass


# --- Chargement des scripts -----------------------------------------------------

def _load_file(path: str, module_name: str):
    spec = importlib.util.spec_from_file_location(module_name, path)
    module = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(module)
    except Exception as exc:
        raise PlugInError(f"{type(exc).__name__}: {exc}") from exc
    for func in ("detect", "fetch"):
        if not callable(getattr(module, func, None)):
            raise PlugInError(translate("admin_plugin_missing_func", func=func))
    return module


def load_module(script_name: str):
    """Module du plug-in, recharge si le fichier a change (upload Admin)."""
    path = os.path.join(PLUGINS_DIR, script_name)
    mtime = os.path.getmtime(path)
    cached = _modules.get(script_name)
    if cached and cached[0] == mtime:
        return cached[1]
    module = _load_file(path, f"tunoova_radio_plugin_{script_name[:-3]}")
    _modules[script_name] = (mtime, module)
    return module


def supported_stations(plugin: RadioPlugIn) -> list[tuple[str, str]]:
    """SUPPORTED_STATIONS du script (vide si absent ou illisible)."""
    try:
        stations = getattr(load_module(plugin.ScriptName), "SUPPORTED_STATIONS", None) or []
        return [(str(name), str(ref)) for name, ref in stations]
    except Exception:
        return []


def validate_script(path: str):
    """Charge un script uploade (hors dossier des plug-ins) pour verifier le
    contrat avant de l'installer. Leve PlugInError."""
    return _load_file(path, "tunoova_radio_plugin_upload")


def sync_plugins() -> None:
    """Une ligne RadioPlugIn par script du dossier (les scripts deposes a la
    main sont ainsi pris en compte sans passer par l'Admin)."""
    known = {p.ScriptName: p for p in RadioPlugIn.query.all()}
    added = False
    for script in sorted(os.listdir(PLUGINS_DIR)):
        if not SCRIPT_RE.match(script) or _is_removed(script):
            continue
        if script in known:
            # NAME modifie dans le script : nom affiche mis a jour
            try:
                name = getattr(load_module(script), "NAME", None)
            except PlugInError:
                name = None
            if name and known[script].PlugInName != name:
                known[script].PlugInName = name
            continue
        try:
            name = getattr(load_module(script), "NAME", None) or script[:-3]
        except PlugInError:
            logger.exception("Plug-in radio %s illisible", script)
            name = script[:-3]
        db.session.add(RadioPlugIn(PlugInName=name, ScriptName=script, Enabled=True))
        added = True
    db.session.commit()
    if added:
        redetect_unlinked()


def install_script(upload_path: str, script_name: str, api_key: str | None) -> RadioPlugIn:
    """Installe un script deja valide (validate_script). Une version existante
    est conservee dans radio_plugins/old/ pour pouvoir revenir en arriere."""
    module = validate_script(upload_path)
    target = os.path.join(PLUGINS_DIR, script_name)
    plugin = RadioPlugIn.query.filter_by(ScriptName=script_name).first()
    if plugin and os.path.exists(target):
        os.makedirs(OLD_VERSIONS_DIR, exist_ok=True)
        shutil.copy2(target, os.path.join(OLD_VERSIONS_DIR, f"{script_name[:-3]}.v{plugin.Version}.py"))
    shutil.copyfile(upload_path, target)
    _modules.pop(script_name, None)
    # Script supprime puis remis par l'admin : de nouveau pris en compte.
    if _is_removed(script_name):
        os.remove(_removed_marker(script_name))
    name = getattr(module, "NAME", None) or script_name[:-3]
    if plugin:
        plugin.Version = (plugin.Version or 1) + 1
        plugin.PlugInName = name
        plugin.Status = None
        plugin.StatusDetail = None
    else:
        plugin = RadioPlugIn(PlugInName=name, ScriptName=script_name, Enabled=True, Version=1)
        db.session.add(plugin)
    if api_key:
        plugin.ApiKey = api_key
    db.session.commit()
    _cache.clear()
    redetect_unlinked()
    return plugin


def _removed_marker(script_name: str) -> str:
    return os.path.join(OLD_VERSIONS_DIR, script_name + REMOVED_SUFFIX)


def _is_removed(script_name: str) -> bool:
    return os.path.exists(_removed_marker(script_name))


def remove(plugin: RadioPlugIn) -> None:
    """Supprime le plug-in : script et versions precedentes effaces, radios
    liees detachees puis redetectees (un autre plug-in peut les gerer)."""
    script = plugin.ScriptName
    stem = script[:-3]
    os.makedirs(OLD_VERSIONS_DIR, exist_ok=True)
    with open(_removed_marker(script), "w", encoding="utf-8"):
        pass
    for name in os.listdir(OLD_VERSIONS_DIR):
        if re.fullmatch(re.escape(stem) + r"\.v\d+\.py", name):
            os.remove(os.path.join(OLD_VERSIONS_DIR, name))
    path = os.path.join(PLUGINS_DIR, script)
    if os.path.exists(path):
        os.remove(path)
    _modules.pop(script, None)
    Radio.query.filter_by(RadioPlugInId=plugin.RadioPlugInId).update({
        Radio.RadioPlugInId: None, Radio.PlugInRef: None, Radio.PlugInStatus: None,
        Radio.PlugInStatusDetail: None, Radio.PlugInLastTest: None,
    })
    db.session.delete(plugin)
    db.session.commit()
    _cache.clear()
    redetect_unlinked()


def previous_version_path(plugin: RadioPlugIn) -> str | None:
    if (plugin.Version or 1) <= 1:
        return None
    path = os.path.join(OLD_VERSIONS_DIR, f"{plugin.ScriptName[:-3]}.v{plugin.Version - 1}.py")
    return path if os.path.exists(path) else None


def rollback(plugin: RadioPlugIn) -> bool:
    previous = previous_version_path(plugin)
    if not previous:
        return False
    shutil.copyfile(previous, os.path.join(PLUGINS_DIR, plugin.ScriptName))
    os.remove(previous)
    _modules.pop(plugin.ScriptName, None)
    plugin.Version -= 1
    plugin.Status = None
    plugin.StatusDetail = None
    db.session.commit()
    _cache.clear()
    redetect_unlinked()
    return True


# --- Detection et appel ---------------------------------------------------------

def detect(radio: Radio, commit: bool = True) -> RadioPlugIn | None:
    """Associe la radio au premier plug-in qui la reconnait (aucun = ICY seul)."""
    found, ref = None, None
    for plugin in RadioPlugIn.query.filter_by(Enabled=True).order_by(RadioPlugIn.RadioPlugInId).all():
        try:
            ref = load_module(plugin.ScriptName).detect(radio.LinkURL, radio.RadioName)
        except Exception:  # un plug-in defaillant ne doit pas bloquer les suivants
            logger.exception("Detection du plug-in radio %s en echec", plugin.ScriptName)
            continue
        if ref is not None:
            found = plugin
            break
    radio.RadioPlugInId = found.RadioPlugInId if found else None
    radio.PlugInRef = ref if found else None
    radio.PlugInCheckedAt = datetime.utcnow()
    if commit:
        db.session.commit()
    return found


def redetect_unlinked() -> int:
    """Nouvelle detection des radios sans plug-in (un plug-in vient d'etre
    installe ou reactive). Les radios deja associees ne sont jamais
    reaffectees : leur plug-in a pu etre choisi a la main dans l'Admin."""
    radios = Radio.query.filter(Radio.RadioPlugInId.is_(None)).all()
    linked = sum(1 for radio in radios if detect(radio, commit=False))
    db.session.commit()
    _cache.clear()
    return linked


def normalize(result) -> dict | None:
    """Sortie d'un plug-in ramenee au format standard (champs inconnus ignores)."""
    if not isinstance(result, dict) or not str(result.get("show") or "").strip():
        return None

    def text(key):
        value = result.get(key)
        return str(value).strip() or None if value is not None else None

    def epoch(key):
        try:
            return int(result[key]) if result.get(key) is not None else None
        except (TypeError, ValueError):
            return None

    return {
        "kind": "song" if result.get("kind") == "song" else "show",
        "show": text("show"),
        "detail": text("detail"),
        "image_url": text("image_url"),
        "start": epoch("start"),
        "end": epoch("end"),
    }


def run_plugin(plugin: RadioPlugIn, ref: str | None, stream_url: str) -> dict | None:
    """Appel brut d'un plug-in (leve l'exception du plug-in) - aussi utilise
    par le bouton Tester de l'Admin."""
    return normalize(load_module(plugin.ScriptName).fetch(ref or "", stream_url, plugin.ApiKey or None))


def _set_status(plugin: RadioPlugIn, status: str, detail: str | None) -> None:
    """Statut affiche dans l'Admin, mis a jour par les ecoutes elles-memes -
    ecrit en base seulement quand il change."""
    if plugin.Status == status and (status == "ok" or plugin.StatusDetail == detail):
        return
    plugin.Status, plugin.StatusDetail = status, detail[:1000] if detail else None
    plugin.LastCheck = datetime.utcnow()
    db.session.commit()


def _fetch(radio: Radio) -> dict | None:
    if radio.RadioPlugInId is None and radio.PlugInCheckedAt is None:
        detect(radio)
    plugin = radio.plugin
    if plugin is not None and plugin.Enabled:
        try:
            result = run_plugin(plugin, radio.PlugInRef, radio.LinkURL)
            _set_status(plugin, "ok", label(result))
            return result
        except Exception as exc:
            logger.warning("Plug-in radio %s en echec pour %s : %s", plugin.ScriptName, radio.RadioName, exc)
            _set_status(plugin, "error", f"{radio.RadioName} : {type(exc).__name__}: {exc}")
    return normalize(icy.now_playing(radio.LinkURL, radio.RadioName))


def now_playing(radio: Radio | None) -> dict | None:
    """Ce qui passe a l'antenne (format standard), None si inconnu. Mis en
    cache par flux : plusieurs auditeurs/onglets de la meme radio ne
    declenchent qu'un appel par periode."""
    if radio is None or not radio.LinkURL:
        return None
    now = time.time()
    cached = _cache.get(radio.LinkURL)
    if cached is not None and now < cached[1]:
        return cached[0]
    result = _fetch(radio)
    ttl = CACHE_TTL if result else EMPTY_CACHE_TTL
    if result and result.get("end") and result["end"] > now:
        ttl = max(10, min(ttl, result["end"] - now))  # rafraichi des la fin de l'emission/du morceau
    _cache[radio.LinkURL] = (result, now + ttl)
    return result


def clear_cache(stream_url: str | None = None) -> None:
    if stream_url is None:
        _cache.clear()
    else:
        _cache.pop(stream_url, None)


def label(result: dict | None) -> str | None:
    if not result:
        return None
    return f"{result['show']} — {result['detail']}" if result.get("detail") else result["show"]


def live_subtitle(stream_url: str | None) -> str:
    """Sous-titre affiche pour une radio : titre en cours s'il est deja connu
    (cache uniquement - jamais d'acces reseau ici, cf. now_playing), seul et
    sans prefixe "En direct", sinon "Radio en direct"."""
    cached = _cache.get(stream_url) if stream_url else None
    if cached and cached[0] and time.time() < cached[1]:
        return label(cached[0])
    return translate("radio_live")


# --- Admin ----------------------------------------------------------------------

def test_radio(radio: Radio) -> None:
    """Bouton Tester d'une radio : appel de son plug-in avec son Station ID,
    resultat garde sur la radio (colonne "Dernier test" de l'Admin). Une
    reponse vide n'est pas une erreur (rien a l'antenne)."""
    try:
        result = run_plugin(radio.plugin, radio.PlugInRef, radio.LinkURL)
        radio.PlugInStatus, radio.PlugInStatusDetail = "ok", label(result)
    except Exception as exc:
        radio.PlugInStatus, radio.PlugInStatusDetail = "error", f"{type(exc).__name__}: {exc}"[:1000]
    radio.PlugInLastTest = datetime.utcnow()
    db.session.commit()


def check_plugin(plugin: RadioPlugIn) -> None:
    """Bouton Tester d'un plug-in : test d'une radio qui lui est liee (aucune =
    statut inchange), reporte sur le plug-in."""
    radio = Radio.query.filter_by(RadioPlugInId=plugin.RadioPlugInId).first()
    if radio is None:
        return
    test_radio(radio)
    plugin.Status = radio.PlugInStatus
    plugin.StatusDetail = (radio.PlugInStatusDetail if radio.PlugInStatus == "ok"
                           else f"{radio.RadioName} : {radio.PlugInStatusDetail}"[:1000])
    plugin.LastCheck = radio.PlugInLastTest
    db.session.commit()
