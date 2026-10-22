"""Logique de l'ecran Play (CDC §8) : resolution de l'item en cours (podcast /
radio / musique), Like/Not Like avec compteurs globaux, commentaires (fil
partage : par morceau, par radio, par podcast),
compteur d'ecoutes, partage avec un autre utilisateur, navigation Precedent/Suivant, et
persistance de l'etat de lecture dans la table Play pour la reprise."""

from datetime import datetime

from app.extensions import db
from app.models import (
    Users,
    Podcast,
    PodcastUsers,
    PodcastComment,
    EpisodesUsers,
    Radio,
    RadioUsers,
    RadioComment,
    MusicAlbum,
    MusicTrack,
    MusicTrackComment,
    Play,
)
from app import podcast_service, radio_service, music_catalog, now_playing
from app.cover_service import cover_for
from flask import url_for


# --------------------------------------------------------------------------
# Like / Comment generiques (memes regles pour les 3 types de contenu)
# --------------------------------------------------------------------------

def apply_like(item, catalog, like_attr: str, catalog_like_attr: str, catalog_notlike_attr: str, new_value: str):
    """Bascule Like/Not Like sur `item` et ajuste les compteurs globaux sur
    `catalog`. Cliquer sur le choix deja actif l'annule (toggle off)."""
    current = getattr(item, like_attr)

    def _bump(attr, delta):
        if catalog is None:
            return
        setattr(catalog, attr, max(0, (getattr(catalog, attr) or 0) + delta))

    if current == new_value:
        setattr(item, like_attr, None)
        _bump(catalog_like_attr if new_value == "Like" else catalog_notlike_attr, -1)
        return None

    if current == "Like":
        _bump(catalog_like_attr, -1)
    elif current == "Not Like":
        _bump(catalog_notlike_attr, -1)

    setattr(item, like_attr, new_value)
    _bump(catalog_like_attr if new_value == "Like" else catalog_notlike_attr, 1)
    return new_value


def apply_comment(item, catalog, comment_attr: str, catalog_comment_attr: str, text: str | None):
    had_comment = bool(getattr(item, comment_attr))
    setattr(item, comment_attr, text or None)
    has_comment = bool(text)

    if catalog is None:
        return
    if has_comment and not had_comment:
        setattr(catalog, catalog_comment_attr, (getattr(catalog, catalog_comment_attr) or 0) + 1)
    elif not has_comment and had_comment:
        setattr(catalog, catalog_comment_attr, max(0, (getattr(catalog, catalog_comment_attr) or 0) - 1))


# --------------------------------------------------------------------------
# Fil de commentaires partage (musique, radio, podcast) et compteur d'ecoutes
# --------------------------------------------------------------------------

# Table des commentaires par type de contenu ; la colonne de liaison porte le
# meme nom que la cle du catalogue (MusicTrack.TrackId, Radio.RadioId, Podcast.PodcastId).
_COMMENT_TABLES = {
    "music": (MusicTrackComment, "TrackId"),
    "radio": (RadioComment, "RadioId"),
    "podcast": (PodcastComment, "PodcastId"),
}


def _comment_query(ctx: dict):
    model, key = _COMMENT_TABLES[ctx["content_type"]]
    return model, key, model.query.filter(getattr(model, key) == getattr(ctx["catalog"], key))


def thread_comments(ctx: dict, item_id: int, users_id: int) -> list[dict]:
    """Commentaires de l'item, du plus recent au plus ancien. Seul l'auteur
    d'un commentaire peut le supprimer (delete_url)."""
    model, _key, query = _comment_query(ctx)
    rows = query.order_by(model.CreatedAt.desc(), model.CommentId.desc()).all()
    return [
        {
            "id": c.CommentId,
            "author": c.user.FullName if c.user else "?",
            "created_at": c.CreatedAt.isoformat() + "Z",  # UTC, formate cote navigateur
            "text": c.CommentText,
            "delete_url": url_for("play.comment_delete", content_type=ctx["content_type"], item_id=item_id, comment_id=c.CommentId)
            if c.UsersId == users_id else None,
        }
        for c in rows
    ]


def add_thread_comment(ctx: dict, users_id: int, text: str) -> None:
    model, key = _COMMENT_TABLES[ctx["content_type"]]
    catalog = ctx["catalog"]
    db.session.add(model(**{key: getattr(catalog, key)}, UsersId=users_id, CommentText=text[:2056]))
    attr = ctx["catalog_comment_attr"]
    setattr(catalog, attr, (getattr(catalog, attr) or 0) + 1)
    db.session.commit()


def delete_thread_comment(ctx: dict, users_id: int, comment_id: int) -> bool:
    """Supprime un commentaire, par son auteur uniquement."""
    model, _key, query = _comment_query(ctx)
    comment = query.filter(model.CommentId == comment_id, model.UsersId == users_id).first()
    if comment is None:
        return False
    db.session.delete(comment)
    catalog, attr = ctx["catalog"], ctx["catalog_comment_attr"]
    setattr(catalog, attr, max(0, (getattr(catalog, attr) or 0) - 1))
    db.session.commit()
    return True


def record_music_listen(users_id: int, track_id: int) -> None:
    """Une ecoute de plus : compteur global du morceau (MusicTrack.MusicCount)
    et compteur de l'utilisateur (MusicUsers.CountListen)."""
    track = db.session.get(MusicTrack, track_id)
    if track is None:
        return
    track.MusicCount = (track.MusicCount or 0) + 1
    user_track = music_catalog.get_user_track(users_id, track_id)
    user_track.CountListen = (user_track.CountListen or 0) + 1
    if user_track.MusicStatus == "New":
        user_track.MusicStatus = "Unread"
    db.session.commit()


# --------------------------------------------------------------------------
# Contexte d'affichage par type de contenu
# --------------------------------------------------------------------------

def _podcast_context(item_id: int, users_id: int) -> dict | None:
    episode = EpisodesUsers.query.filter_by(EpisodesUsersId=item_id, UsersId=users_id).first()
    if not episode:
        return None
    subscription = db.session.get(PodcastUsers, episode.PodcastUsersId)
    catalog = db.session.get(Podcast, episode.PodcastId) if episode.PodcastId else None

    # Meme ordre que la liste des episodes (series 1/2 puis 2/2) : Suivant et
    # l'enchainement automatique suivent ce qui est affiche
    sibling_ids = [
        ep.EpisodesUsersId
        for ep in podcast_service.order_episodes(
            EpisodesUsers.query.filter_by(PodcastUsersId=episode.PodcastUsersId).all()
        )
    ]
    idx = sibling_ids.index(item_id) if item_id in sibling_ids else -1
    prev_id = sibling_ids[idx - 1] if idx > 0 else None
    next_id = sibling_ids[idx + 1] if 0 <= idx < len(sibling_ids) - 1 else None

    return {
        "item": episode,
        "catalog": catalog,
        "image_url": subscription.LinkImage if subscription else None,
        "image_placeholder": "🎙",
        "title": episode.EpisodeName,
        "subtitle": subscription.PodcastName if subscription else "",
        "content_type": "podcast",
        "stream_url": episode.EpisodeURL,
        "like_attr": "EpisodeLike",
        "comment_attr": None,  # fil de commentaires du podcast (PodcastComment), pas de commentaire perso
        "comment_thread": catalog is not None,
        "listen_attr": "PodcastCount",
        "catalog_like_attr": "PodcastLike",
        "catalog_notlike_attr": "PodcastNotLike",
        "catalog_comment_attr": "PodcastComment",
        "show_progress": True,
        "prev_id": prev_id,
        "next_id": next_id,
        "subscription": subscription,
    }


def _radio_context(item_id: int, users_id: int) -> dict | None:
    radio = RadioUsers.query.filter_by(RadioUsersId=item_id, UsersId=users_id).first()
    if not radio:
        return None
    catalog = db.session.get(Radio, radio.RadiosId) if radio.RadiosId else None

    return {
        "item": radio,
        "catalog": catalog,
        "image_url": radio.LinkImage,
        "image_placeholder": "📻",
        "title": radio.RadioName,
        "subtitle": "radio_live",  # cle de traduction (CDC §8)
        "content_type": "radio",
        "stream_url": radio.LinkURL,
        "like_attr": "RadioLike",
        "comment_attr": None,  # fil de commentaires (RadioComment), pas de commentaire perso
        "comment_thread": catalog is not None,
        "listen_attr": "RadioCount",
        "catalog_like_attr": "RadioLike",
        "catalog_notlike_attr": "RadioNotLike",
        "catalog_comment_attr": "RadioComment",
        "show_progress": False,
        "prev_id": None,
        "next_id": None,
    }


def music_stream_url(track: MusicTrack) -> str:
    if track.Source == music_catalog.SOURCE_YOUTUBE:
        return url_for("media.ytmusic_stream", track_id=track.TrackId)
    return url_for("media.mp3_stream", track_id=track.TrackId)


# File de lecture explicite (ex. "Les plus populaires" d'un artiste, dans
# l'ordre de popularite) : Precedent/Suivant la suivent tant que le morceau
# en cours en fait partie. Sinon, ordre de l'ecran Artiste (cf. ci-dessous).
# Stockee dans Play.MusicQueue (et non le cookie de session, cf. models.Play) :
# suivie aussi par l'enchainement Cast et la reprise sur un autre appareil.


def set_music_queue(users_id: int, track_ids) -> None:
    """Memorise (ou efface, si vide) la file de lecture musique de l'utilisateur."""
    play = Play.query.filter_by(UsersId=users_id).first()
    if not play:
        if not track_ids:
            return
        play = Play(UsersId=users_id)
        db.session.add(play)
    play.MusicQueue = list(track_ids) if track_ids else None
    db.session.commit()


def _music_queue(users_id: int) -> list[int]:
    play = Play.query.filter_by(UsersId=users_id).first()
    return (play.MusicQueue if play else None) or []


def _music_context(item_id: int, users_id: int) -> dict | None:
    """item_id = TrackId (catalogue partage) : le lien utilisateur <-> morceau
    (MusicUsers, like/commentaire perso) est cree au besoin."""
    track = db.session.get(MusicTrack, item_id)
    if not track:
        return None
    user_track = music_catalog.get_user_track(users_id, track.TrackId)
    db.session.commit()

    # Meme ordre que l'ecran Artiste (album par album) et l'ecran Album
    # (numero de piste) : le "suivant" reste dans l'album en cours avant de
    # passer a l'album suivant, au lieu de melanger tous les titres de l'artiste.
    queue = _music_queue(users_id)
    sibling_ids = queue if item_id in queue else [
        tid
        for (tid,) in db.session.query(MusicTrack.TrackId)
        .outerjoin(MusicAlbum, MusicTrack.AlbumId == MusicAlbum.AlbumId)
        .filter(MusicTrack.ArtistId == track.ArtistId, MusicTrack.IsAvailable.is_(True))
        .order_by(
            MusicAlbum.Year.asc().nullslast(),
            MusicAlbum.Title.asc().nullslast(),
            MusicTrack.TrackNumber.asc().nullslast(),
            MusicTrack.Title.asc(),
        )
        .all()
    ]
    idx = sibling_ids.index(item_id) if item_id in sibling_ids else -1
    prev_id = sibling_ids[idx - 1] if idx > 0 else None
    next_id = sibling_ids[idx + 1] if 0 <= idx < len(sibling_ids) - 1 else None

    return {
        "item": user_track,
        "catalog": track,
        "image_url": None,
        "cover_url": cover_for(track, 500),
        "image_placeholder": "🎵",
        "title": track.Title,
        "subtitle": track.artist.Name,
        "content_type": "music",
        "stream_url": music_stream_url(track),
        "like_attr": "MusicLike",
        "comment_attr": None,  # fil de commentaires (MusicTrackComment), pas de commentaire perso
        "comment_thread": True,
        "listen_attr": "MusicCount",
        "catalog_like_attr": "MusicLike",
        "catalog_notlike_attr": "MusicNotLike",
        "catalog_comment_attr": "MusicComment",
        "show_progress": True,
        "prev_id": prev_id,
        "next_id": next_id,
    }


_CONTEXT_RESOLVERS = {"podcast": _podcast_context, "radio": _radio_context, "music": _music_context}
_PLAY_TYPE_LABELS = {"podcast": "Podcast", "radio": "Radio", "music": "Music"}
CONTENT_TYPE_BY_PLAY_TYPE = {v: k for k, v in _PLAY_TYPE_LABELS.items()}


def get_context(content_type: str, item_id: int, users_id: int) -> dict | None:
    resolver = _CONTEXT_RESOLVERS.get(content_type)
    if not resolver:
        return None
    return resolver(item_id, users_id)


# --------------------------------------------------------------------------
# Serialisation JSON pour le panneau #play-overlay (ouverture/fermeture sans
# navigation, cf. app/static/js/app.js) - mêmes données que celles rendues
# par app/templates/play/_screen_body.html, pour un item donné.
# --------------------------------------------------------------------------

def _overlay_cover(ctx: dict) -> dict:
    if ctx.get("cover_url"):
        return {"type": "cover", "url": ctx["cover_url"], "placeholder": ctx["image_placeholder"]}
    if ctx.get("image_url"):
        return {"type": "image", "url": ctx["image_url"]}
    return {"type": "placeholder", "placeholder": ctx["image_placeholder"]}


def to_overlay_json(content_type: str, item_id: int, ctx: dict, playing: bool, current_play: Play | None = None) -> dict:
    is_podcast = content_type == "podcast"
    subtitle_display = now_playing.live_subtitle(ctx["stream_url"]) if content_type == "radio" else ctx["subtitle"]
    like_value = getattr(ctx["item"], ctx["like_attr"])

    def _play_url(other_id):
        return url_for("play.show", content_type=content_type, item_id=other_id) if other_id else None

    return {
        "content_type": content_type,
        "item_id": item_id,
        "title": ctx["title"],
        "subtitle": subtitle_display,
        "overlay_title": subtitle_display if is_podcast else ctx["title"],
        "overlay_subtitle": ctx["title"] if is_podcast else subtitle_display,
        "podcast_style": is_podcast,
        "cover": _overlay_cover(ctx),
        "like_active": like_value == "Like",
        "not_like_active": like_value == "Not Like",
        "like_count": (ctx["catalog"] and getattr(ctx["catalog"], ctx["catalog_like_attr"], 0)) or 0,
        "not_like_count": (ctx["catalog"] and getattr(ctx["catalog"], ctx["catalog_notlike_attr"], 0)) or 0,
        "comment_text": (getattr(ctx["item"], ctx["comment_attr"]) or "") if ctx["comment_attr"] else "",
        "comment_count": comment_count(ctx),
        "comment_active": user_commented(ctx, ctx["item"].UsersId),
        "listen_count": None if listen_count(ctx) is None else short_count(listen_count(ctx)),
        "comment_thread": ctx["comment_thread"],
        "comments_url": url_for("play.comments", content_type=content_type, item_id=item_id)
        if ctx["comment_thread"] else None,
        "show_progress": ctx["show_progress"],
        "prev_id": ctx["prev_id"],
        "next_id": ctx["next_id"],
        "play_url": url_for("play.show", content_type=content_type, item_id=item_id),
        "prev_play_url": _play_url(ctx["prev_id"]),
        "next_play_url": _play_url(ctx["next_id"]),
        "like_url": url_for("play.like", content_type=content_type, item_id=item_id),
        "comment_url": url_for("play.comment", content_type=content_type, item_id=item_id),
        "share_url": url_for("play.share", content_type=content_type, item_id=item_id),
        "stream_url": ctx["stream_url"],
        "playing": playing,
        # Rejoue a chaque ouverture/rafraichissement du panneau (CDC §7, Doc/A
        # faire.md "afficher si l'ecoute en local et si sur cast device") -
        # cote client, tunoovaFillPlayOverlay resynchronise le badge Cast a
        # partir de CETTE valeur a chaque appel plutot que de se fier a un
        # etat JS potentiellement perime (ex. reouverture du panneau apres un
        # arret de la diffusion dans un autre onglet, ou tout autre desync).
        "cast_device_label": cast_device_label_for(current_play),
    }


def user_commented(ctx: dict, users_id: int) -> bool:
    """Le lecteur a-t-il commente cet item (bouton Commentaire actif) ?"""
    if ctx["comment_thread"]:
        model, _key, query = _comment_query(ctx)
        return query.filter(model.UsersId == users_id).first() is not None
    return bool(getattr(ctx["item"], ctx["comment_attr"]))


def short_count(n: int | None) -> str:
    """Compteur abrege : 999 -> '999', 1000 a 1999 -> '1k', 25400 -> '25k',
    1 200 000 -> '1M' (arrondi a l'inferieur)."""
    n = n or 0
    if n < 1000:
        return str(n)
    if n < 1_000_000:
        return f"{n // 1000}k"
    return f"{n // 1_000_000}M"


def listen_count(ctx: dict) -> int | None:
    """Nombre d'ecoutes tous utilisateurs, None si le type de contenu n'en a
    pas (podcast : pas de compteur global)."""
    if not ctx.get("listen_attr") or ctx["catalog"] is None:
        return None
    return getattr(ctx["catalog"], ctx["listen_attr"]) or 0


def comment_count(ctx: dict) -> int:
    return (ctx["catalog"] and getattr(ctx["catalog"], ctx["catalog_comment_attr"], 0)) or 0


def cast_device_label_for(play: Play | None) -> str | None:
    """Nom a afficher ('Diffusion sur ...') si `play` diffuse actuellement sur
    une enceinte, sinon None. Centralise pour rester coherent entre le rendu
    serveur (context_processor inject_current_play) et le panneau Play
    (to_overlay_json)."""
    if not play or not play.CastDeviceId:
        return None
    from app.models import CastDevice

    device = db.session.get(CastDevice, play.CastDeviceId)
    return (device.Label or device.FriendlyName) if device else None


# --------------------------------------------------------------------------
# Etat de lecture courant (table Play, CDC §2/§8)
# --------------------------------------------------------------------------

def set_current_play(users_id: int, content_type: str, item_id: int, link_url: str, status: str = "Play") -> bool:
    """Renvoie True si l'item en cours a change. Un nouveau morceau compte
    alors comme une ecoute (Precedent/Suivant, enchainement Cast...)."""
    play_type = _PLAY_TYPE_LABELS.get(content_type, content_type)
    play = Play.query.filter_by(UsersId=users_id).first()

    if play and play.PlayType == play_type and play.ItemId == item_id:
        # Deja l'item en cours (ex. ouverture de l'ecran Play plein format
        # pour ce qui joue deja depuis le mini-player) : ne pas reinitialiser
        # la position/l'etat, sous peine de faire repartir la lecture a 0.
        return False

    if not play:
        play = Play(UsersId=users_id)
        db.session.add(play)
    play.PlayType = play_type
    play.ItemId = item_id
    play.LinkURL = link_url
    play.PlayStatus = status
    play.PositionSeconds = 0.0
    db.session.commit()
    if play_type == "Music":
        record_music_listen(users_id, item_id)
    elif play_type == "Podcast":
        podcast_service.record_podcast_listen(item_id)
    return True


def get_current_play(users_id: int) -> Play | None:
    return Play.query.filter_by(UsersId=users_id).first()


def current_play_summary(users_id: int) -> dict | None:
    """Etat du mini-lecteur d'apres la table Play (CDC §2) : rendu de base.html
    (app/__init__.py::inject_current_play) et resynchronisation JSON d'un
    autre appareil (/play/current, cf. app/static/js/app.js)."""
    play = get_current_play(users_id)
    if not play or not play.ItemId or not play.PlayType:
        return None

    content_type = {"Podcast": "podcast", "Radio": "radio", "Music": "music"}.get(play.PlayType)
    if not content_type:
        return None

    ctx = get_context(content_type, play.ItemId, users_id)
    if not ctx:
        return None

    thumbnail = ctx.get("image_url") or ctx.get("cover_url")

    cast_device_label = cast_device_label_for(play)

    return {
        "title": ctx["title"],
        "subtitle": now_playing.live_subtitle(ctx["stream_url"]) if content_type == "radio" else ctx["subtitle"],
        "thumbnail": thumbnail,
        "stream_url": play.LinkURL,
        "position": play.PositionSeconds or 0,
        "playing": play.PlayStatus == "Play",
        "play_url": url_for("play.show", content_type=content_type, item_id=play.ItemId),
        "cast_device_label": cast_device_label,
        "cast_device_id": play.CastDeviceId,
    }


# --------------------------------------------------------------------------
# Fonctionnalite Cast (CDC §2/§7/§8/§10) : contrairement a la lecture "normale"
# dans l'appli (radio/podcast en URL directe depuis le navigateur), la fenetre
# Cast resout systematiquement l'URL vers cet endpoint HTTP proxifie, quel que
# soit le type de contenu - necessaire pour les enceintes qui ne savent lire
# que du HTTP simple (Bose SoundTouch), meme quand la source est en HTTPS.
# --------------------------------------------------------------------------

def cast_media_url(base_url: str, content_type: str, item_id: int, users_id: int, start: int = 0) -> str:
    """Construit l'URL /media/cast/... a passer au device (AVTransport). Prend
    `base_url` explicitement (plutot que flask.url_for(_external=True)) car
    elle doit aussi pouvoir etre reconstruite hors contexte de requete, par le
    thread de fond qui enchaine automatiquement sur la piste suivante
    (app/cast_service.py) - cf. CDC §12 : serveur et devices toujours sur le
    meme reseau local, donc l'adresse locale du serveur calculee au choix du
    device (cast_service.cast_base_url) reste valable pour toute la session.

    `start` (secondes) : le flux commence directement a cette position
    (ffmpeg -ss cote serveur, cf. media.cast_stream) - les enceintes (Bose
    notamment) refusent le Seek AVTransport, la reprise se fait donc ici."""
    from app.tokens import make_cast_media_token

    token = make_cast_media_token(content_type, item_id, users_id)
    url = f"{base_url.rstrip('/')}/media/cast/{content_type}/{item_id}?token={token}"
    if start > 0:
        url += f"&start={start}"
    return url


def update_play_state(users_id: int, status: str | None = None, position: float | None = None) -> None:
    play = Play.query.filter_by(UsersId=users_id).first()
    if not play:
        return
    if status is not None:
        play.PlayStatus = status
    if position is not None:
        play.PositionSeconds = position
    db.session.commit()


# --------------------------------------------------------------------------
# Partage avec un autre utilisateur (CDC §8 "Share")
# --------------------------------------------------------------------------

def share_with_users(content_type: str, item_id: int, from_users_id: int, to_users_ids) -> int:
    """Partage avec plusieurs utilisateurs ; renvoie le nombre de partages reussis."""
    return sum(share_with_user(content_type, item_id, from_users_id, uid) for uid in dict.fromkeys(to_users_ids))


def share_with_user(content_type: str, item_id: int, from_users_id: int, to_users_id: int) -> bool:
    if to_users_id == from_users_id or not db.session.get(Users, to_users_id):
        return False

    if content_type == "podcast":
        # item_id = EpisodesUsersId (ecran Play d'un episode), pas l'abonnement
        source = EpisodesUsers.query.filter_by(EpisodesUsersId=item_id, UsersId=from_users_id).first()
        if not source:
            return False
        podcast_service.share_episode(to_users_id, source)
        return True

    if content_type == "radio":
        source = RadioUsers.query.filter_by(RadioUsersId=item_id, UsersId=from_users_id).first()
        if not source:
            return False
        radio_service.share_radio(to_users_id, source, from_users_id)
        return True

    if content_type == "music":
        # item_id = TrackId : le morceau est deja au catalogue partage, il
        # suffit de le signaler comme nouveaute a l'autre utilisateur - meme
        # s'il le connait deja (notification "X vous a partage ce morceau").
        if not db.session.get(MusicTrack, item_id):
            return False
        row = music_catalog.get_user_track(to_users_id, item_id)
        row.MusicStatus = "New"
        row.SharedBy = from_users_id
        row.CreatedAt = datetime.utcnow()  # tri des notifications
        db.session.commit()
        return True

    return False
