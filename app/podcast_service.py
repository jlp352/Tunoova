"""Logique metier Podcast (CDC §8, §10) : recherche iTunes, synchronisation
des episodes depuis le flux RSS, gestion du catalogue partage Podcast."""

import re
from datetime import datetime, timezone

import feedparser
import requests

from app.extensions import db
from app.models import Podcast, PodcastUsers, EpisodesUsers, PodcastComment

ITUNES_SEARCH_URL = "https://itunes.apple.com/search"
REQUEST_TIMEOUT = 6


def search_itunes(query: str, limit: int = 15, country: str = "US") -> list[dict]:
    """Recherche de podcasts via l'API Apple iTunes Search (gratuite, sans cle).

    Le parametre `country` influence fortement les resultats (et la presence
    du champ feedUrl) pour les podcasts non anglophones : passer 'FR' pour de
    meilleurs resultats sur les podcasts francophones prives (RTL, Ausha,
    Audiomeans...). Certains podcasts (notamment Radio France) n'exposent pas
    feedUrl via cette API quel que soit le pays : ils restent ajoutables via
    AddAdvancedPodcast (saisie manuelle de l'URL RSS)."""
    if not query or len(query) < 2:
        return []
    try:
        resp = requests.get(
            ITUNES_SEARCH_URL,
            params={"term": query, "media": "podcast", "entity": "podcast", "limit": limit, "country": country},
            timeout=REQUEST_TIMEOUT,
        )
        resp.raise_for_status()
        data = resp.json()
    except (requests.RequestException, ValueError):
        return []

    results = []
    for item in data.get("results", []):
        feed_url = item.get("feedUrl")
        if not feed_url:
            continue
        results.append(
            {
                "name": item.get("collectionName") or item.get("trackName") or "?",
                "artist": item.get("artistName"),
                "image": item.get("artworkUrl600") or item.get("artworkUrl100"),
                "feed_url": feed_url,
            }
        )
    return results


def get_or_create_podcast_catalog(name: str, rss_url: str, image_url: str | None, description: str | None = None) -> Podcast:
    podcast = Podcast.query.filter_by(LinkRSS=rss_url).first()
    if podcast:
        return podcast
    podcast = Podcast(
        PodcastName=name,
        PodcastDescription=description or name,
        LinkRSS=rss_url,
        LinkImage=image_url,
        PodcastLike=0,
        PodcastNotLike=0,
        PodcastComment=0,
        PodcastCount=0,
    )
    db.session.add(podcast)
    db.session.flush()  # obtient PodcastId avant creation de PodcastUsers
    return podcast


def add_podcast_for_user(
    users_id: int, name: str, rss_url: str, image_url: str | None, episode_filter: str | None = None
) -> tuple[PodcastUsers, bool]:
    """Retourne (PodcastUsers, created). Idempotent : ne duplique pas un abonnement existant."""
    existing = PodcastUsers.query.filter_by(UsersId=users_id, LinkRSS=rss_url).first()
    if existing:
        return existing, False

    catalog = get_or_create_podcast_catalog(name, rss_url, image_url)
    subscription = PodcastUsers(
        UsersId=users_id,
        PodcastName=name,
        LinkRSS=rss_url,
        LinkImage=image_url,
        PodcastId=catalog.PodcastId,
        CountListen=0,
        EpisodeFilter=episode_filter,
    )
    db.session.add(subscription)
    db.session.commit()
    return subscription, True


def update_podcast_subscription(
    subscription: PodcastUsers, name: str, rss_url: str, image_url: str | None, episode_filter: str | None = None
) -> None:
    subscription.PodcastName = name
    subscription.LinkRSS = rss_url
    subscription.LinkImage = image_url
    subscription.EpisodeFilter = episode_filter
    subscription.PodcastId = get_or_create_podcast_catalog(name, rss_url, image_url).PodcastId
    db.session.commit()


def delete_podcast_subscription(subscription: PodcastUsers) -> None:
    EpisodesUsers.query.filter_by(PodcastUsersId=subscription.PodcastUsersId).delete()
    db.session.delete(subscription)
    db.session.commit()


def share_episode(to_users_id: int, source: EpisodesUsers) -> None:
    """Partage d'un episode : le destinataire est abonne au podcast (si besoin,
    avec import des episodes sans les marquer 'New') et l'episode partage
    passe en 'New' chez lui, pour apparaitre dans ses notifications."""
    source_sub = db.session.get(PodcastUsers, source.PodcastUsersId)
    subscription, created = add_podcast_for_user(
        to_users_id, source_sub.PodcastName, source_sub.LinkRSS, source_sub.LinkImage
    )
    if created:
        sync_episodes(subscription, mark_new=False)

    episode = _find_same_episode(subscription.PodcastUsersId, source)
    if episode is None:
        # Flux indisponible ou episode plus dans le flux : copie de l'episode
        episode = EpisodesUsers(
            UsersId=to_users_id,
            PodcastUsersId=subscription.PodcastUsersId,
            PodcastId=subscription.PodcastId,
            EpisodeURL=source.EpisodeURL,
            EpisodeGuid=source.EpisodeGuid,
            EpisodeName=source.EpisodeName,
            EpisodeDescription=source.EpisodeDescription,
            EpisodePublishDate=source.EpisodePublishDate,
        )
        db.session.add(episode)
    episode.EpisodeStatus = "New"
    db.session.commit()


def _find_same_episode(podcast_users_id: int, source: EpisodesUsers) -> EpisodesUsers | None:
    """L'episode `source` (d'un autre utilisateur) dans l'abonnement
    `podcast_users_id`, reconnu par les memes cles que sync_episodes."""
    source_keys = episode_keys(source.EpisodeGuid, source.EpisodeURL, source.EpisodeName, source.EpisodePublishDate)
    for episode in EpisodesUsers.query.filter_by(PodcastUsersId=podcast_users_id).all():
        keys = episode_keys(episode.EpisodeGuid, episode.EpisodeURL, episode.EpisodeName, episode.EpisodePublishDate)
        if keys & source_keys:
            return episode
    return None


def record_podcast_listen(episodes_users_id: int) -> None:
    """Une ecoute de plus au compteur global du podcast (Podcast.PodcastCount)."""
    episode = db.session.get(EpisodesUsers, episodes_users_id)
    podcast = db.session.get(Podcast, episode.PodcastId) if episode else None
    if podcast is None:
        return
    podcast.PodcastCount = (podcast.PodcastCount or 0) + 1
    db.session.commit()


def delete_user_comments(users_id: int) -> None:
    """Suppression d'un compte : retire ses commentaires et met a jour le
    compteur Podcast.PodcastComment des podcasts concernes."""
    counts = (
        db.session.query(PodcastComment.PodcastId, db.func.count())
        .filter(PodcastComment.UsersId == users_id)
        .group_by(PodcastComment.PodcastId)
        .all()
    )
    for podcast_id, count in counts:
        podcast = db.session.get(Podcast, podcast_id)
        if podcast is not None:
            podcast.PodcastComment = max(0, (podcast.PodcastComment or 0) - count)
    PodcastComment.query.filter_by(UsersId=users_id).delete(synchronize_session=False)


def _parse_publish_date(entry) -> datetime | None:
    parsed = entry.get("published_parsed") or entry.get("updated_parsed")
    if not parsed:
        return None
    return datetime(*parsed[:6], tzinfo=timezone.utc).replace(tzinfo=None)


def _parse_episode_filter(filter_text: str | None) -> dict:
    """Parse le champ Filter de l'ecran EditPodcast (maquette), syntaxe libre du
    type 'max:20 contain:INTEGRALE exclude:LIVE' (separateurs espace ou virgule)."""
    parsed = {"max": None, "contain": None, "exclude": None}
    if not filter_text:
        return parsed
    for token in filter_text.replace(",", " ").split():
        key, sep, value = token.partition(":")
        if not sep:
            continue
        key = key.strip().lower()
        value = value.strip()
        if key == "max" and value.isdigit():
            parsed["max"] = int(value)
        elif key in ("contain", "exclude") and value:
            parsed[key] = value.lower()
    return parsed


def _entry_matches_filter(title: str, parsed_filter: dict) -> bool:
    title_lower = (title or "").lower()
    if parsed_filter["contain"] and parsed_filter["contain"] not in title_lower:
        return False
    if parsed_filter["exclude"] and parsed_filter["exclude"] in title_lower:
        return False
    return True


# Marqueur de partie d'une serie : "[1/2]", "(1/3)", "2/2", "Partie 1", "Part 2",
# "Episode 3", "Ep. 3"...
_SERIES_PART_RE = re.compile(
    r"[\[(]?\s*(?:(\d{1,3})\s*/\s*(\d{1,3})|(?:partie|part|[ée]pisode|ep\.?)\s*(\d{1,3}))\s*[\])]?",
    re.IGNORECASE,
)


# Debut de titre encore dans une etiquette ouverte : "[", "[REDIFF ", "(Inedit "
_LEADING_TAG_RE = re.compile(r"^\s*[\[(][^\])]*$")


def _normalize_series_title(text: str) -> str:
    return re.sub(r"[\W_]+", " ", text.lower()).strip()


def _series_key(title: str | None) -> tuple[str, int] | None:
    """(titre de la serie normalise, numero de partie), ou None hors serie.
    Marqueur "X/N" : chaque partie a souvent son propre sous-titre
    ("Celine Dion 1/3 : une enfant..." / "Celine Dion 2/3 : rene..."), la
    serie est donc identifiee par le texte avant le marqueur (apres s'il est
    en tete) et le total N. Marqueur dans une etiquette en tete ("[REDIFF 1/2]
    Bruno Cholet...") : etiquette + titre qui suit, sinon toutes les
    rediffusions en 2 parties du flux formeraient une seule serie "rediff".
    Marqueur "Partie/Episode n" : titre complet sans le marqueur, sinon
    "Podcast episode 12 : ..." regrouperait tout le flux."""
    title = title or ""
    match = _SERIES_PART_RE.search(title)
    if not match:
        return None
    if match.group(1):
        part = int(match.group(1))
        before = title[: match.start()]
        if _LEADING_TAG_RE.match(before):
            base = _normalize_series_title(before + " " + title[match.end():])
        else:
            base = _normalize_series_title(before) or _normalize_series_title(title[match.end():])
        if base:
            base = f"{base} /{match.group(2)}"
    else:
        part = int(match.group(3))
        base = _normalize_series_title(title[: match.start()] + " " + title[match.end():])
    return (base, part) if base else None


def order_episodes(episodes: list) -> list:
    """Ordre d'affichage ET d'enchainement des episodes : du plus recent au
    plus ancien, sauf les episodes d'une meme serie (meme titre, "[1/2]" /
    "[2/2]"...) regroupes a la place du plus recent d'entre eux et ranges par
    numero de partie croissant, pour que 1/2 soit lu avant 2/2."""
    def recency(ep):
        return (ep.EpisodePublishDate or datetime.min, ep.EpisodesUsersId)

    series: dict[str, list] = {}
    for ep in episodes:
        key = _series_key(ep.EpisodeName)
        if key:
            series.setdefault(key[0], []).append(ep)

    groups, seen = [], set()
    for ep in sorted(episodes, key=recency, reverse=True):
        if ep.EpisodesUsersId in seen:
            continue
        key = _series_key(ep.EpisodeName)
        members = series.get(key[0], [ep]) if key else [ep]
        if len(members) > 1:
            members = sorted(members, key=lambda e: (_series_key(e.EpisodeName)[1], recency(e)))
        else:
            members = [ep]
        groups.extend(members)
        seen.update(m.EpisodesUsersId for m in members)
    return groups


_AUDIO_FILE_RE = re.compile(r"\.(mp3|m4a|aac|ogg|oga|opus|wav|flac|mp4)$")


def _url_key(url: str) -> str:
    """URL audio sans schema ni parametres : Ausha ajoute regulierement des
    parametres de tracking ("keywords=", "kw_applecategory=") a toutes ses
    URL, le chemin identifie toujours l'episode. Parametres gardes si le
    chemin n'est pas un fichier audio ("download.php?id=123" : c'est alors
    le parametre qui identifie l'episode)."""
    url = url.split("#", 1)[0].lower()
    path = url.split("?", 1)[0]
    if _AUDIO_FILE_RE.search(path):
        url = path
    return re.sub(r"^[a-z]+://", "", url)


def episode_keys(guid: str | None, url: str | None, title: str | None, publish_date: datetime | None) -> set[tuple]:
    """Cles qui identifient un episode dans un abonnement : guid RSS, URL
    sans parametres, et titre + date de publication (Radio France reencode
    ses fichiers - autre URL, parfois autre domaine - et certains flux
    publient deux fois le meme episode sous deux guid). Deux episodes qui
    partagent une cle sont le meme episode."""
    keys = set()
    if guid:
        keys.add(("guid", guid))
    if url:
        keys.add(("url", _url_key(url)))
    if title and publish_date:
        keys.add(("title", _normalize_series_title(title), publish_date.replace(microsecond=0)))
    return keys


def sync_episodes(subscription: PodcastUsers, max_episodes: int = 50, mark_new: bool = True) -> int:
    """Recupere le flux RSS et ajoute les episodes pas encore connus dans
    EpisodesUsers. Best-effort : un flux indisponible ne casse pas
    l'affichage, on garde ce qui est deja en base.
    `mark_new=False` lors du premier import d'un podcast (l'utilisateur
    vient de l'ajouter, ce n'est pas une "nouvelle" parution a notifier) ;
    `mark_new=True` (defaut) lors des rafraichissements suivants d'un
    podcast deja suivi, ou seuls les episodes reellement inedits doivent
    apparaitre dans les notifications.
    Applique le champ Filter de l'abonnement (max/contain/exclude) s'il est
    renseigne. Retourne le nombre de nouveaux episodes ajoutes."""
    try:
        resp = requests.get(subscription.LinkRSS, timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
        feed = feedparser.parse(resp.content)
    except requests.RequestException:
        return 0

    known: dict[tuple, EpisodesUsers] = {}
    for episode in EpisodesUsers.query.filter_by(PodcastUsersId=subscription.PodcastUsersId).all():
        for key in episode_keys(episode.EpisodeGuid, episode.EpisodeURL, episode.EpisodeName, episode.EpisodePublishDate):
            known.setdefault(key, episode)

    episode_filter = _parse_episode_filter(subscription.EpisodeFilter)
    limit = episode_filter["max"] if episode_filter["max"] is not None else max_episodes

    added, updated = 0, False
    for entry in feed.entries[:max_episodes]:
        if added >= limit:
            break

        title = entry.get("title", "?")[:512]
        if not _entry_matches_filter(title, episode_filter):
            continue

        episode_url = None
        for link in entry.get("links", []):
            if link.get("rel") == "enclosure" or (link.get("type", "").startswith("audio")):
                episode_url = link.get("href")
                break
        episode_url = episode_url or entry.get("link")
        if not episode_url:
            continue
        guid = (entry.get("id") or "")[:512] or None
        publish_date = _parse_publish_date(entry)
        keys = episode_keys(guid, episode_url, title, publish_date)
        existing = next((known[key] for key in keys if key in known), None)
        if existing is not None:
            # Episode deja en base (URL qui a change, ou importe avant
            # EpisodeGuid) : on retient son guid et, s'il est le meme, l'URL
            # du flux - l'ancienne peut ne plus repondre.
            if existing.EpisodeGuid is None and guid:
                existing.EpisodeGuid = guid
                updated = True
            if existing.EpisodeGuid == guid and existing.EpisodeURL != episode_url:
                existing.EpisodeURL = episode_url
                updated = True
            continue

        episode = EpisodesUsers(
            UsersId=subscription.UsersId,
            PodcastUsersId=subscription.PodcastUsersId,
            PodcastId=subscription.PodcastId,
            EpisodeURL=episode_url,
            EpisodeGuid=guid,
            EpisodeName=title,
            EpisodeDescription=(entry.get("summary") or "")[:2056],
            EpisodePublishDate=publish_date,
            EpisodeStatus="New" if mark_new else "Unread",
        )
        db.session.add(episode)
        for key in keys:
            known[key] = episode
        added += 1

    if added or updated:
        db.session.commit()
    return added
