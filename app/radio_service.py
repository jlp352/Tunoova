"""Logique metier Radio (CDC §8, §10) : recherche Radio Browser, gestion du
catalogue partage Radio."""

import concurrent.futures
import time
from datetime import datetime

import requests

from app.extensions import db
from app.models import Radio, RadioUsers, RadioComment

# Radio Browser expose plusieurs miroirs equivalents ; on les essaie dans
# l'ordre et on s'arrete au premier qui repond (evite une dependance a une
# resolution DNS SRV pour un projet de cette taille).
RADIO_BROWSER_MIRRORS = [
    "https://de1.api.radio-browser.info",
    "https://nl1.api.radio-browser.info",
    "https://at1.api.radio-browser.info",
]
REQUEST_TIMEOUT = 6
FAVICON_CHECK_TIMEOUT = 1.5
FAVICON_CHECK_WORKERS = 24
FAVICON_CACHE_TTL = 6 * 3600  # les memes stations/favicons reviennent sans cesse d'une recherche a l'autre
USER_AGENT = "Tunoova/1.0"

_favicon_cache: dict[str, tuple[bool, float]] = {}


def _normalize_station_name(name: str) -> str:
    return " ".join(name.strip().lower().split())


def _favicon_reachable(url: str) -> bool:
    """Verifie que le favicon charge vraiment une image. Necessaire car le champ
    favicon de Radio Browser est rempli a la main par les contributeurs : il est
    souvent non vide mais casse (lien mort, lien vers une page HTML au lieu de
    l'image...) - la seule facon fiable de le savoir est de le charger."""
    try:
        resp = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=FAVICON_CHECK_TIMEOUT, stream=True)
        ok = resp.status_code == 200 and resp.headers.get("Content-Type", "").lower().startswith("image")
        resp.close()
        return ok
    except requests.RequestException:
        return False


def _validate_favicons(urls: set[str]) -> dict[str, bool]:
    """Verification HTTP en parallele, avec cache : les memes URLs de favicon
    reviennent sans arret d'une recherche a l'autre (memes stations populaires),
    les revalider a chaque frappe serait un gaspillage de temps et de reseau."""
    if not urls:
        return {}

    now = time.time()
    results: dict[str, bool] = {}
    to_check = []
    for url in urls:
        cached = _favicon_cache.get(url)
        if cached is not None and now - cached[1] < FAVICON_CACHE_TTL:
            results[url] = cached[0]
        else:
            to_check.append(url)

    if to_check:
        with concurrent.futures.ThreadPoolExecutor(max_workers=FAVICON_CHECK_WORKERS) as pool:
            checked = dict(zip(to_check, pool.map(_favicon_reachable, to_check)))
        for url, ok in checked.items():
            _favicon_cache[url] = (ok, now)
        results.update(checked)

    return results


def search_radio_browser(query: str, limit: int = 20, country: str | None = None) -> list[dict]:
    """Recherche Radio Browser avec reclassement cote client : la meme station
    y est souvent listee plusieurs fois (contributions independantes), avec des
    favicons qui pretendent exister mais ne chargent pas vraiment. On sur-
    echantillonne, on ecarte les stations dont le logo ne charge pas reellement
    (verification HTTP, en parallele), puis pour les doublons (meme nom, meme
    pays) on ne garde que celle qui a le plus de votes.

    'country' (pays du profil utilisateur, cote CDC) fait remonter les stations
    de ce pays en priorite - ce n'est pas un filtre strict : on garde les autres
    pays en repli, une station specifiquement cherchee ne doit pas disparaitre
    juste parce qu'elle est cataloguee sous un pays voisin/different."""
    if not query or len(query) < 2:
        return []

    query_norm = _normalize_station_name(query)
    country_norm = _normalize_station_name(country) if country else None
    fetch_limit = min(limit * 3, 60)

    for base_url in RADIO_BROWSER_MIRRORS:
        try:
            resp = requests.get(
                f"{base_url}/json/stations/search",
                params={
                    "name": query,
                    "limit": fetch_limit,
                    "hidebroken": "true",
                    "order": "votes",
                    "reverse": "true",
                },
                headers={"User-Agent": USER_AGENT},
                timeout=REQUEST_TIMEOUT,
            )
            resp.raise_for_status()
            stations = resp.json()
        except (requests.RequestException, ValueError):
            continue

        candidates = []
        favicon_urls = set()
        for s in stations:
            stream_url = s.get("url_resolved") or s.get("url")
            favicon = (s.get("favicon") or "").strip()
            if not stream_url or not favicon:
                continue
            favicon_urls.add(favicon)
            name = s.get("name") or "?"
            candidates.append(
                {
                    "name": name,
                    "name_norm": _normalize_station_name(name),
                    "country": s.get("country") or "",
                    "votes": s.get("votes") or 0,
                    "favicon": favicon,
                    "stream_url": stream_url,
                }
            )

        favicon_ok = _validate_favicons(favicon_urls)
        with_working_logo = [c for c in candidates if favicon_ok.get(c["favicon"])]

        best_by_group: dict[tuple[str, str], dict] = {}
        for c in with_working_logo:
            key = (c["name_norm"], c["country"])
            current = best_by_group.get(key)
            if current is None or c["votes"] > current["votes"]:
                best_by_group[key] = c

        ranked = sorted(
            best_by_group.values(),
            key=lambda c: (
                0 if country_norm and _normalize_station_name(c["country"]) == country_norm else 1,
                0 if c["name_norm"] == query_norm else 1,
                -c["votes"],
            ),
        )

        results = []
        seen_urls = set()
        for c in ranked:
            if c["stream_url"] in seen_urls:
                continue
            seen_urls.add(c["stream_url"])
            results.append(
                {
                    "name": c["name"],
                    "image": c["favicon"],
                    "stream_url": c["stream_url"],
                    "country": c["country"],
                }
            )
            if len(results) >= limit:
                break
        return results

    return []


def get_or_create_radio_catalog(name: str, stream_url: str, image_url: str | None, description: str | None = None) -> Radio:
    radio = Radio.query.filter_by(LinkURL=stream_url).first()
    if radio:
        return radio
    radio = Radio(
        RadioName=name,
        RadioDescription=description or name,
        LinkURL=stream_url,
        LinkImage=image_url,
        RadioLike=0,
        RadioNotLike=0,
        RadioComment=0,
        RadioCount=0,
    )
    db.session.add(radio)
    db.session.flush()
    return radio


# RadioUsers.RadioStatus : partage recu, pas encore accepte (hors liste)
STATUS_SHARED = "Shared"
# Partage a notifier (Shared, ou New si la radio etait deja dans la liste)
NOTIFY_STATUSES = (STATUS_SHARED, "New")


def user_radios(users_id: int):
    """Radios de la liste de l'utilisateur, sans les partages pas encore acceptes."""
    return RadioUsers.query.filter(
        RadioUsers.UsersId == users_id,
        db.or_(RadioUsers.RadioStatus.is_(None), RadioUsers.RadioStatus != STATUS_SHARED),
    )


def add_radio_for_user(users_id: int, name: str, stream_url: str, image_url: str | None) -> tuple[RadioUsers, bool]:
    existing = RadioUsers.query.filter_by(UsersId=users_id, LinkURL=stream_url).first()
    if existing:
        if existing.RadioStatus == STATUS_SHARED:
            # Ajoutee a la main alors qu'un partage attendait : acceptee.
            existing.RadioStatus = "Read"
            db.session.commit()
        return existing, False

    catalog = get_or_create_radio_catalog(name, stream_url, image_url)
    subscription = RadioUsers(
        UsersId=users_id,
        RadioName=name,
        LinkURL=stream_url,
        LinkImage=image_url,
        RadiosId=catalog.RadioId,
        CountListen=0,
    )
    db.session.add(subscription)
    db.session.commit()
    return subscription, True


def share_radio(to_users_id: int, source: RadioUsers, from_users_id: int) -> None:
    """Partage recu : notification seulement. La radio n'entre dans la liste
    du destinataire que s'il l'ecoute depuis la notification (radio.play)."""
    row = RadioUsers.query.filter_by(UsersId=to_users_id, LinkURL=source.LinkURL).first()
    if row is None:
        catalog = get_or_create_radio_catalog(source.RadioName, source.LinkURL, source.LinkImage)
        row = RadioUsers(
            UsersId=to_users_id,
            RadioName=source.RadioName,
            LinkURL=source.LinkURL,
            LinkImage=source.LinkImage,
            RadiosId=catalog.RadioId,
            CountListen=0,
            RadioStatus=STATUS_SHARED,
        )
        db.session.add(row)
    elif row.RadioStatus != STATUS_SHARED:
        row.RadioStatus = "New"  # deja dans sa liste : simple notification
    row.SharedBy = from_users_id
    row.CreatedAt = datetime.utcnow()
    db.session.commit()


def _bump_like(subscription: RadioUsers, catalog: Radio | None, delta: int) -> None:
    """Reporte le Like/Not Like de l'utilisateur sur les compteurs globaux."""
    if catalog is None or subscription.RadioLike not in ("Like", "Not Like"):
        return
    attr = "RadioLike" if subscription.RadioLike == "Like" else "RadioNotLike"
    setattr(catalog, attr, max(0, (getattr(catalog, attr) or 0) + delta))


def update_radio_subscription(subscription: RadioUsers, name: str, stream_url: str, image_url: str | None) -> None:
    subscription.RadioName = name
    subscription.LinkURL = stream_url
    subscription.LinkImage = image_url
    catalog = get_or_create_radio_catalog(name, stream_url, image_url)
    if catalog.RadioId != subscription.RadiosId:
        # Nouveau flux = autre radio du catalogue : le Like suit.
        _bump_like(subscription, db.session.get(Radio, subscription.RadiosId), -1)
        _bump_like(subscription, catalog, 1)
        subscription.RadiosId = catalog.RadioId
    db.session.commit()


def delete_radio_subscription(subscription: RadioUsers, commit: bool = True) -> None:
    """Retire la radio de la liste de l'utilisateur. Ses commentaires restent :
    ils appartiennent a la radio partagee (Radio), pas a sa liste."""
    _bump_like(subscription, db.session.get(Radio, subscription.RadiosId), -1)
    db.session.delete(subscription)
    if commit:
        db.session.commit()


def delete_user_radio_data(users_id: int) -> None:
    """Suppression d'un compte : retire ses radios (et leurs Like des
    compteurs) et ses commentaires, en mettant a jour Radio.RadioComment."""
    for subscription in RadioUsers.query.filter_by(UsersId=users_id).all():
        delete_radio_subscription(subscription, commit=False)
    counts = (
        db.session.query(RadioComment.RadioId, db.func.count())
        .filter(RadioComment.UsersId == users_id)
        .group_by(RadioComment.RadioId)
        .all()
    )
    for radio_id, count in counts:
        radio = db.session.get(Radio, radio_id)
        if radio is not None:
            radio.RadioComment = max(0, (radio.RadioComment or 0) - count)
    RadioComment.query.filter_by(UsersId=users_id).delete(synchronize_session=False)

