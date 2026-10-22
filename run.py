#!/usr/bin/env python3
import argparse
import glob

from dotenv import load_dotenv

load_dotenv()

from app import create_app
from app.extensions import db
from db.seed import ensure_schema_upgrades, seed_reference_data

app = create_app()

with app.app_context():
    db.create_all()
    ensure_schema_upgrades()
    seed_reference_data()

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("-p", "--port", type=int, default=5000)
    args = parser.parse_args()
    if not app.config["DEBUG"]:
        # Production : waitress plutot que le serveur de developpement de
        # Werkzeug, deconseille en exposition Internet (Tailscale Funnel) -
        # cf. Doc/Audit Tunoova.md, scan OWASP ZAP.
        # - threads : chaque flux audio (MP3, relais YouTube/radio, Cast)
        #   occupe un thread pendant toute l'ecoute, le defaut (4) saturait ;
        # - channel_timeout : un lecteur en pause cesse de lire le flux, la
        #   connexion ne doit pas etre coupee au bout des 120 s par defaut ;
        # - ident="" : pas d'en-tete Server (version du serveur) ;
        # - clear_untrusted_proxy_headers=False : waitress supprime sinon les
        #   X-Forwarded-* ; c'est TrustedProxyMiddleware (app/remote_access.py)
        #   qui decide de leur faire confiance selon Admin > Reseau.
        from waitress import serve

        serve(
            app, host="0.0.0.0", port=args.port, threads=32, channel_timeout=900, ident="",
            clear_untrusted_proxy_headers=False,
        )
        raise SystemExit
    # reloader_type="stat" force le rechargeur a ne surveiller que les fichiers
    # .py importes (comportement par defaut historique de Werkzeug). Sans ce
    # choix explicite, Werkzeug bascule automatiquement sur le rechargeur
    # "watchdog" si ce paquet est installe, lequel surveille TOUT le dossier
    # du projet - y compris .env, que l'Espace Admin reecrit (SMTP, backup,
    # compte admin...). Une sauvegarde .env declenchait alors un redemarrage
    # du serveur en pleine requete, coupant la reponse HTTP alors meme que le
    # changement avait deja ete applique cote serveur.
    app.run(
        debug=app.config["DEBUG"],
        host="0.0.0.0",
        port=args.port,
        threaded=True,
        reloader_type="stat",
        # Les traductions sont chargees une seule fois (app/i18n.py) : sans
        # cela, une cle ajoutee dans un .json n'apparait qu'apres redemarrage.
        extra_files=glob.glob("app/translations/*.json"),
    )
