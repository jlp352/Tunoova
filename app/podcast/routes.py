from datetime import datetime

from flask import render_template, request, redirect, url_for, flash, jsonify
from flask_login import login_required, current_user

from app.podcast import bp
from app.extensions import db
from app.models import PodcastUsers, EpisodesUsers
from app.logging_util import log_screen_view
from app.i18n import current_locale
from app import podcast_service, play_service
from app.sort_prefs import requested_sort

SORTS = ("newest", "oldest", "az", "za")

# Episodes charges par page ; les suivants arrivent au defilement (pagination_li)
EPISODES_PAGE_SIZE = 20


def _get_subscriptions(users_id: int, sort: str):
    query = PodcastUsers.query.filter_by(UsersId=users_id)

    if sort == "za":
        return query.order_by(PodcastUsers.PodcastName.desc()).all()

    if sort in ("newest", "oldest"):
        latest_episode = (
            db.session.query(
                EpisodesUsers.PodcastUsersId.label("PodcastUsersId"),
                db.func.max(EpisodesUsers.EpisodePublishDate).label("LatestPublishDate"),
            )
            .filter(EpisodesUsers.UsersId == users_id)
            .group_by(EpisodesUsers.PodcastUsersId)
            .subquery()
        )
        query = query.outerjoin(latest_episode, PodcastUsers.PodcastUsersId == latest_episode.c.PodcastUsersId)
        if sort == "newest":
            order = latest_episode.c.LatestPublishDate.desc().nullslast()
        else:
            order = latest_episode.c.LatestPublishDate.asc().nullsfirst()
        return query.order_by(order, PodcastUsers.PodcastName.asc()).all()

    return query.order_by(PodcastUsers.PodcastName.asc()).all()


def _search_subscriptions(users_id: int, q: str):
    """Recherche locale fusionnee : separe les podcasts dont le NOM correspond
    (lignes compactes, lien vers le detail complet) de ceux dont seul un
    EPISODE correspond (carte avec uniquement les episodes correspondants)."""
    like = f"%{q}%"
    name_matches = (
        PodcastUsers.query.filter_by(UsersId=users_id)
        .filter(PodcastUsers.PodcastName.ilike(like))
        .order_by(PodcastUsers.PodcastName.asc())
        .all()
    )
    name_match_ids = {p.PodcastUsersId for p in name_matches}

    episode_query = EpisodesUsers.query.filter_by(UsersId=users_id).filter(EpisodesUsers.EpisodeName.ilike(like))
    if name_match_ids:
        episode_query = episode_query.filter(~EpisodesUsers.PodcastUsersId.in_(name_match_ids))
    matching_episodes = episode_query.order_by(EpisodesUsers.EpisodePublishDate.desc().nullslast()).all()

    episodes_by_podcast: dict[int, list] = {}
    for ep in matching_episodes:
        episodes_by_podcast.setdefault(ep.PodcastUsersId, []).append(ep)

    episode_matches = []
    if episodes_by_podcast:
        subs_by_id = {
            s.PodcastUsersId: s
            for s in PodcastUsers.query.filter(
                PodcastUsers.UsersId == users_id,
                PodcastUsers.PodcastUsersId.in_(episodes_by_podcast.keys()),
            ).all()
        }
        episode_matches = [(subs_by_id[pid], episodes) for pid, episodes in episodes_by_podcast.items()]

    return name_matches, episode_matches


def _build_list_context(users_id: int, q: str, sort: str) -> dict:
    if q and len(q) >= 3:
        name_matches, episode_matches = _search_subscriptions(users_id, q)
        return {
            "subscriptions": [],
            "name_matches": name_matches,
            "episode_matches": episode_matches,
        }

    # Liste en tuiles (titre seul) : les episodes ne sont charges que sur la page detail
    return {
        "subscriptions": _get_subscriptions(users_id, sort),
        "name_matches": [],
        "episode_matches": [],
    }


def _get_episodes_page(
    podcast_users_id: int, offset: int, limit: int = EPISODES_PAGE_SIZE, episode_filter: str | None = None
):
    """Pagine les episodes d'un abonnement en appliquant le champ Filter (contain/
    exclude/max) a l'AFFICHAGE - pas seulement a la synchro RSS - pour que la
    limite s'applique aussi aux episodes deja connus avant que le filtre soit
    renseigne (CDC : "max:20 n'affichera que les 20 premiers")."""
    parsed = podcast_service._parse_episode_filter(episode_filter)

    query = EpisodesUsers.query.filter_by(PodcastUsersId=podcast_users_id)
    if parsed["contain"]:
        query = query.filter(EpisodesUsers.EpisodeName.ilike(f"%{parsed['contain']}%"))
    if parsed["exclude"]:
        query = query.filter(~EpisodesUsers.EpisodeName.ilike(f"%{parsed['exclude']}%"))
    # Tri en Python (series "[1/2]" / "[2/2]" dans l'ordre de lecture, cf.
    # podcast_service.order_episodes) puis pagination sur la liste triee
    episodes = podcast_service.order_episodes(query.all())
    if parsed["max"] is not None:
        episodes = episodes[: parsed["max"]]
    page = episodes[offset : offset + limit]
    return page, offset + limit < len(episodes)


def _search_api(q: str) -> list[dict]:
    if not q or len(q) < 3:
        return []
    country = "FR" if current_locale() == "fr" else "US"
    return podcast_service.search_itunes(q, country=country)


@bp.route("/")
@login_required
def index():
    log_screen_view("Podcast")
    q = request.args.get("q", "").strip()
    sort = requested_sort("podcast", SORTS, "az")
    context = _build_list_context(current_user.UsersId, q, sort)

    return render_template(
        "podcast/index.html",
        add_results=_search_api(q),
        q=q,
        sort=sort,
        page_size=EPISODES_PAGE_SIZE,
        scroll_to=request.args.get("added", type=int),
        **context,
    )


@bp.route("/list")
@login_required
def list_fragment():
    """Fragment htmx utilise par la recherche live et le tri (SearchPodcast / SortBy, CDC §8)."""
    q = request.args.get("q", "").strip()
    sort = requested_sort("podcast", SORTS, "az")
    context = _build_list_context(current_user.UsersId, q, sort)

    return render_template(
        "podcast/_list.html",
        add_results=_search_api(q),
        q=q,
        page_size=EPISODES_PAGE_SIZE,
        **context,
    )


@bp.route("/<int:podcast_users_id>")
@login_required
def detail(podcast_users_id):
    """Vue detaillee d'un seul podcast (accessible depuis un resultat de recherche
    par nom, CDC §8 - remplace le survol de tous les episodes dans la liste)."""
    subscription = PodcastUsers.query.filter_by(
        PodcastUsersId=podcast_users_id, UsersId=current_user.UsersId
    ).first_or_404()
    log_screen_view("PodcastDetail")
    episodes, has_more = _get_episodes_page(subscription.PodcastUsersId, 0, episode_filter=subscription.EpisodeFilter)
    return render_template(
        "podcast/detail.html",
        subscription=subscription,
        episodes=episodes,
        has_more=has_more,
        page_size=EPISODES_PAGE_SIZE,
    )


@bp.route("/<int:podcast_users_id>/episodes")
@login_required
def more_episodes(podcast_users_id):
    sub = PodcastUsers.query.filter_by(
        PodcastUsersId=podcast_users_id, UsersId=current_user.UsersId
    ).first_or_404()
    offset = request.args.get("offset", 0, type=int)
    episodes, has_more = _get_episodes_page(sub.PodcastUsersId, offset, episode_filter=sub.EpisodeFilter)
    return render_template(
        "podcast/_episodes_more.html",
        subscription=sub,
        episodes=episodes,
        offset=offset,
        next_offset=offset + EPISODES_PAGE_SIZE,
        has_more=has_more,
    )


@bp.route("/<int:podcast_users_id>/refresh", methods=["POST"])
@login_required
def refresh(podcast_users_id):
    sub = PodcastUsers.query.filter_by(
        PodcastUsersId=podcast_users_id, UsersId=current_user.UsersId
    ).first_or_404()
    podcast_service.sync_episodes(sub)
    episodes, has_more = _get_episodes_page(sub.PodcastUsersId, 0, episode_filter=sub.EpisodeFilter)
    return render_template(
        "podcast/_episode_block.html",
        subscription=sub,
        episodes=episodes,
        has_more=has_more,
        page_size=EPISODES_PAGE_SIZE,
    )


@bp.route("/add", methods=["POST"])
@login_required
def add_submit():
    name = request.form.get("name", "").strip()
    feed_url = request.form.get("feed_url", "").strip()
    image_url = request.form.get("image_url", "").strip() or None

    if not name or not feed_url:
        flash("Nom et URL du flux RSS sont requis.", "error")
        return redirect(url_for("podcast.index"))

    subscription, created = podcast_service.add_podcast_for_user(current_user.UsersId, name, feed_url, image_url)
    if created:
        podcast_service.sync_episodes(subscription, mark_new=False)
        flash(f"« {name} » ajouté.", "success")
    else:
        flash(f"« {name} » est déjà dans vos podcasts.", "info")
    return redirect(url_for("podcast.index", added=subscription.PodcastUsersId))


@bp.route("/add/advanced", methods=["GET", "POST"])
@login_required
def add_advanced():
    log_screen_view("AddAdvancedPodcast")
    error = None
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        feed_url = request.form.get("feed_url", "").strip()
        image_url = request.form.get("image_url", "").strip() or None
        episode_filter = request.form.get("episode_filter", "").strip() or None

        if not name or not feed_url:
            error = "Nom et URL du flux RSS sont requis."
        else:
            subscription, created = podcast_service.add_podcast_for_user(
                current_user.UsersId, name, feed_url, image_url, episode_filter
            )
            if created:
                podcast_service.sync_episodes(subscription, mark_new=False)
                flash(f"« {name} » ajouté.", "success")
            else:
                flash(f"« {name} » est déjà dans vos podcasts.", "info")
            return redirect(url_for("podcast.index", added=subscription.PodcastUsersId))

    return render_template("podcast/add_advanced.html", error=error, values=request.form)


@bp.route("/<int:podcast_users_id>/edit", methods=["GET", "POST"])
@login_required
def edit(podcast_users_id):
    subscription = PodcastUsers.query.filter_by(
        PodcastUsersId=podcast_users_id, UsersId=current_user.UsersId
    ).first_or_404()
    log_screen_view("EditPodcast")

    error = None
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        feed_url = request.form.get("feed_url", "").strip()
        image_url = request.form.get("image_url", "").strip() or None
        episode_filter = request.form.get("episode_filter", "").strip() or None

        if not name or not feed_url:
            error = "Nom et URL du flux RSS sont requis."
        else:
            podcast_service.update_podcast_subscription(subscription, name, feed_url, image_url, episode_filter)
            flash(f"« {name} » mis à jour.", "success")
            return redirect(url_for("podcast.index"))

    return render_template("podcast/edit.html", subscription=subscription, error=error)


@bp.route("/episode/<int:episodes_users_id>/play", methods=["POST"])
@login_required
def play_episode(episodes_users_id):
    episode = EpisodesUsers.query.filter_by(
        EpisodesUsersId=episodes_users_id, UsersId=current_user.UsersId
    ).first_or_404()

    if episode.EpisodeStatus == "New":
        episode.EpisodeStatus = "Unread"

    subscription = db.session.get(PodcastUsers, episode.PodcastUsersId)
    if subscription:
        subscription.CountListen = (subscription.CountListen or 0) + 1
        subscription.LastListen = datetime.utcnow()

    db.session.commit()

    play_service.set_current_play(current_user.UsersId, "podcast", episode.EpisodesUsersId, episode.EpisodeURL)

    return jsonify(
        {
            "stream_url": episode.EpisodeURL,
            "title": episode.EpisodeName,
            "subtitle": subscription.PodcastName if subscription else "",
            "thumbnail": subscription.LinkImage if subscription else None,
            "play_url": url_for("play.show", content_type="podcast", item_id=episode.EpisodesUsersId),
        }
    )


@bp.route("/<int:podcast_users_id>/delete", methods=["POST"])
@login_required
def delete(podcast_users_id):
    subscription = PodcastUsers.query.filter_by(
        PodcastUsersId=podcast_users_id, UsersId=current_user.UsersId
    ).first_or_404()
    name = subscription.PodcastName
    podcast_service.delete_podcast_subscription(subscription)
    if request.headers.get("HX-Request") == "true":
        return "", 200
    flash(f"« {name} » supprimé.", "success")
    return redirect(url_for("podcast.index"))
