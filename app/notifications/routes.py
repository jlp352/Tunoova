from datetime import datetime

from flask import render_template, url_for, redirect
from flask_login import login_required, current_user

from app.extensions import db
from app.notifications import bp
from app.models import EpisodesUsers, PodcastUsers, MusicUsers, MusicTrack, RadioUsers
from app.i18n import translate as t
from app.logging_util import log_screen_view
from app.cover_service import cover_for
from app import radio_service, update_service


def _podcast_notifications(users_id: int) -> list[dict]:
    rows = (
        EpisodesUsers.query.filter_by(UsersId=users_id, EpisodeStatus="New")
        .join(PodcastUsers, EpisodesUsers.PodcastUsersId == PodcastUsers.PodcastUsersId)
        .add_columns(PodcastUsers.PodcastName, PodcastUsers.LinkImage)
        .all()
    )
    items = []
    for episode, podcast_name, link_image in rows:
        items.append(
            {
                "kind": "podcast",
                "image_url": link_image,
                "cover_url": None,
                "placeholder": "🎙",
                "bold": podcast_name,
                "subtitle": episode.EpisodeName,
                "sort_date": episode.EpisodePublishDate or datetime.min,
                "action_kind": "play",
                "action_url": url_for("podcast.play_episode", episodes_users_id=episode.EpisodesUsersId),
            }
        )
    return items


def _new_music_query(users_id: int):
    """Nouveautes musique non lues, sans les MP3 disparus du disque."""
    return MusicUsers.query.join(MusicTrack, MusicUsers.TrackId == MusicTrack.TrackId).filter(
        MusicUsers.UsersId == users_id, MusicUsers.MusicStatus == "New", MusicTrack.IsAvailable.is_(True)
    )


def _music_notifications(users_id: int) -> list[dict]:
    """Nouveaux MP3 d'un artiste deja ecoute (cf. music_catalog.notify_new_tracks)
    ou morceaux partages par un autre utilisateur : regroupes ici par
    Artiste+Album pour eviter une notification par piste et afficher
    directement l'artiste et l'album ajoutes (demande utilisateur)."""
    rows = _new_music_query(users_id).all()

    items = []
    for row in [r for r in rows if r.SharedBy]:
        # Partage : une notification par morceau, avec l'expediteur.
        track = row.track
        sender = row.shared_by.FullName if row.shared_by else "?"
        items.append(
            {
                "kind": "music",
                "image_url": None,
                "cover_url": cover_for(track),
                "placeholder": "🎵",
                "bold": f"{track.Title} • {track.artist.Name}",
                "subtitle": t("notifications_music_shared", name=sender),
                "sort_date": row.CreatedAt or datetime.min,
                "action_kind": "play",
                "action_url": url_for("library.play", track_id=track.TrackId),
            }
        )
    rows = [r for r in rows if not r.SharedBy]

    groups: dict[tuple[int, int | None], list] = {}
    for row in rows:
        groups.setdefault((row.track.ArtistId, row.track.AlbumId), []).append(row)

    for group_rows in groups.values():
        first = group_rows[0].track
        latest = max((r.CreatedAt or datetime.min) for r in group_rows)

        if first.album is not None:
            if len(group_rows) > 1:
                subtitle = t("notifications_music_album_count", album=first.album.Title, count=len(group_rows))
            else:
                subtitle = first.album.Title
            action_kind = "link"
            action_url = url_for("library.album_detail", album_id=first.AlbumId)
        else:
            subtitle = first.Title
            action_kind = "play"
            action_url = url_for("library.play", track_id=first.TrackId)

        items.append(
            {
                "kind": "music",
                "image_url": None,
                "cover_url": cover_for(first),
                "placeholder": "🎵",
                "bold": first.artist.Name,
                "subtitle": subtitle,
                "sort_date": latest,
                "action_kind": action_kind,
                "action_url": action_url,
            }
        )
    return items


def _shared_radio_query(users_id: int):
    return RadioUsers.query.filter(
        RadioUsers.UsersId == users_id, RadioUsers.RadioStatus.in_(radio_service.NOTIFY_STATUSES)
    )


def _radio_notifications(users_id: int) -> list[dict]:
    """Radios partagees par un autre utilisateur, pas encore ecoutees."""
    rows = _shared_radio_query(users_id).all()
    return [
        {
            "kind": "radio",
            "image_url": row.LinkImage,
            "cover_url": None,
            "placeholder": "📻",
            "bold": row.RadioName,
            "subtitle": t("notifications_radio_shared", name=row.shared_by.FullName if row.shared_by else "?"),
            "sort_date": row.CreatedAt or datetime.min,
            "action_kind": "play",
            "action_url": url_for("radio.play", radio_users_id=row.RadioUsersId),
        }
        for row in rows
    ]


def _update_notifications() -> list[dict]:
    """Nouvelle version annoncee par l'Espace Admin (cf.
    update_service.notify_update_available) : commune a tous les utilisateurs,
    affichee jusqu'a son installation ("Tout effacer" ne la masque pas)."""
    version = update_service.announced_version()
    if not version:
        return []
    return [
        {
            "kind": "update",
            "image_url": url_for("static", filename="brand/update-available.png"),
            "cover_url": None,
            "placeholder": "⬆",
            "bold": t("notifications_update_title", version=version),
            "subtitle": t("notifications_update_subtitle"),
            "sort_date": datetime.max,
            "action_kind": None,
            "action_url": None,
        }
    ]


def count_new(users_id: int) -> int:
    """Compte rapide (sans construire les objets d'affichage) pour le badge
    de la nav du bas (CDC §7, maquette)."""
    podcast_count = EpisodesUsers.query.filter_by(UsersId=users_id, EpisodeStatus="New").count()
    music_count = _new_music_query(users_id).count()
    radio_count = _shared_radio_query(users_id).count()
    update_count = 1 if update_service.announced_version() else 0
    return podcast_count + music_count + radio_count + update_count


@bp.route("/")
@login_required
def index():
    log_screen_view("Notifications")

    items = (
        _podcast_notifications(current_user.UsersId)
        + _music_notifications(current_user.UsersId)
        + _radio_notifications(current_user.UsersId)
        + _update_notifications()
    )
    items.sort(key=lambda i: i["sort_date"], reverse=True)

    return render_template("notifications/index.html", items=items)


@bp.route("/clear", methods=["POST"])
@login_required
def clear_all():
    """Bouton "Tout effacer" (demande utilisateur) : marque toutes les
    notifications comme lues sans supprimer les episodes/morceaux
    sous-jacents, qui restent accessibles depuis les ecrans Podcast/Musique."""
    EpisodesUsers.query.filter_by(UsersId=current_user.UsersId, EpisodeStatus="New").update(
        {"EpisodeStatus": "Unread"}
    )
    MusicUsers.query.filter_by(UsersId=current_user.UsersId, MusicStatus="New").update({"MusicStatus": "Unread"})
    # Partages de radio : refuses s'ils n'etaient pas encore acceptes.
    for row in RadioUsers.query.filter_by(UsersId=current_user.UsersId, RadioStatus=radio_service.STATUS_SHARED).all():
        radio_service.delete_radio_subscription(row, commit=False)
    RadioUsers.query.filter_by(UsersId=current_user.UsersId, RadioStatus="New").update({"RadioStatus": "Read"})
    db.session.commit()
    return redirect(url_for("notifications.index"))
