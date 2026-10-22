"""Banc d'essai d'un plug-in radio Tunoova, autonome (seul requests est requis).

Charge le script comme Tunoova (app/now_playing.py : _load_file, normalize),
verifie le contrat, puis appelle detect/fetch.

    python test_plugin.py mon_plugin.py --stream URL --name "Nom"
    python test_plugin.py mon_plugin.py --ref ID
    python test_plugin.py mon_plugin.py --other URL "Nom" --other URL2 "Nom2"
    python test_plugin.py mon_plugin.py            # chaque SUPPORTED_STATIONS
"""

import argparse
import importlib.util
import os
import re
import sys
import time
import traceback
from datetime import datetime

SCRIPT_RE = re.compile(r"^[a-z][a-z0-9_]{0,60}\.py$")
MAX_SCRIPT_SIZE = 256 * 1024

failures = 0


def report(ok: bool, message: str) -> None:
    global failures
    if not ok:
        failures += 1
    print(f"[{'OK' if ok else 'ECHEC'}] {message}")


def normalize(result):
    """Copie de app/now_playing.normalize."""
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


def fmt_epoch(value):
    return datetime.fromtimestamp(value).strftime("%d/%m %H:%M:%S") if value else "-"


def load(path: str):
    name = os.path.basename(path)
    report(bool(SCRIPT_RE.match(name)), f"nom de fichier {name} (minuscules, chiffres, _)")
    report(os.path.getsize(path) <= MAX_SCRIPT_SIZE, f"taille {os.path.getsize(path)} octets (max {MAX_SCRIPT_SIZE})")
    spec = importlib.util.spec_from_file_location("tunoova_radio_plugin_test", path)
    module = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(module)
    except Exception:
        report(False, "chargement du script")
        traceback.print_exc()
        sys.exit(1)
    for func in ("detect", "fetch"):
        report(callable(getattr(module, func, None)), f"fonction {func}()")
    report(bool(getattr(module, "NAME", None)), f"NAME = {getattr(module, 'NAME', None)!r}")
    stations = getattr(module, "SUPPORTED_STATIONS", None)
    if stations is not None:
        valid = all(isinstance(s, (tuple, list)) and len(s) == 2 for s in stations)
        report(valid, f"SUPPORTED_STATIONS : {len(stations)} radio(s) (nom affiche, Station ID)")
    return module


def run_fetch(module, ref: str, stream_url: str, api_key) -> None:
    started = time.time()
    try:
        raw = module.fetch(ref, stream_url, api_key)
    except Exception as exc:
        report(False, f"fetch({ref!r}) a leve {type(exc).__name__}: {exc}")
        traceback.print_exc()
        return
    elapsed = time.time() - started
    result = normalize(raw)
    if result is None:
        report(raw is None, f"fetch({ref!r}) : rien a l'antenne ({elapsed:.1f} s)"
               + ("" if raw is None else f" - sortie ignoree par Tunoova : {raw!r}"))
        return
    report(elapsed < 10, f"fetch({ref!r}) en {elapsed:.1f} s")
    print(f"       {result['kind']:4} : {result['show']}" + (f" — {result['detail']}" if result["detail"] else ""))
    print(f"       image : {result['image_url'] or '-'}")
    print(f"       debut : {fmt_epoch(result['start'])}  fin : {fmt_epoch(result['end'])}")
    now = time.time()
    if result["start"] and result["start"] > 1e11 or result["end"] and result["end"] > 1e11:
        report(False, "start/end semblent en millisecondes : attendu epoch en secondes")
    elif result["start"] and result["end"]:
        report(result["start"] <= now < result["end"], "l'heure actuelle est entre debut et fin")
    elif result["end"]:
        report(result["end"] > now - 60, "fin dans le futur")
    report(module.fetch("__inconnu__", stream_url, api_key) is None, "fetch() renvoie None pour un Station ID inconnu")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("script")
    parser.add_argument("--stream", help="URL du flux d'une radio geree par le plug-in")
    parser.add_argument("--name", default="", help="nom de cette radio dans Tunoova")
    parser.add_argument("--ref", help="Station ID a passer directement a fetch()")
    parser.add_argument("--api-key")
    parser.add_argument("--other", nargs=2, action="append", default=[], metavar=("URL", "NOM"),
                        help="radio d'un autre fournisseur : detect() doit renvoyer None")
    args = parser.parse_args()

    module = load(args.script)

    for url, name in args.other:
        try:
            ref = module.detect(url, name)
            report(ref is None, f"detect({name!r}) = {ref!r} (attendu None)")
        except Exception as exc:
            report(False, f"detect({name!r}) a leve {type(exc).__name__}: {exc}")

    if args.stream:
        try:
            ref = module.detect(args.stream, args.name)
        except Exception as exc:
            report(False, f"detect({args.name!r}) a leve {type(exc).__name__}: {exc}")
            ref = None
        report(ref is not None, f"detect({args.name!r}) = {ref!r}")
        if ref is not None:
            run_fetch(module, args.ref if args.ref is not None else ref, args.stream, args.api_key)
    elif args.ref is not None:
        run_fetch(module, args.ref, "", args.api_key)
    else:
        for name, ref in getattr(module, "SUPPORTED_STATIONS", None) or []:
            print(f"--- {name}")
            run_fetch(module, str(ref), "", args.api_key)

    print(f"\n{'Tout est OK' if not failures else f'{failures} echec(s)'}")
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()
