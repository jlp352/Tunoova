from flask import render_template, request, jsonify, url_for, abort, redirect, session
from flask_login import login_required, current_user

from app.library import bp
from app.extensions import db
from app.models import MusicArtist, MusicAlbum, MusicTrack, MusicUsers
from app.logging_util import log_screen_view
from app.cover_service import cover_for, sized
from app import youtube_music_service, music_catalog, play_service
from app.sort_prefs import requested_sort, saved_sort


# --------------------------------------------------------------------------
# Construction des lignes affichees
# --------------------------------------------------------------------------

def _track_order():
    return (MusicTrack.TrackNumber.asc().nullslast(), MusicTrack.Title.asc())


def _available_tracks():
    """Morceaux affichables : sans les MP3 disparus du disque (masques par le
    scan, cf. mp3_library_service._update_availability)."""
    return MusicTrack.query.filter(MusicTrack.IsAvailable.is_(True))


def _library_tracks(query):
    """Sans les morceaux ajoutes seulement via "Les plus populaires"
    (MusicTrack.FromTop) : ils ne font pas apparaitre leur album."""
    return query.filter(MusicTrack.FromTop.is_(False))


def _favorite_tracks_query(users_id):
    """Morceaux ecoutes au moins une fois par l'utilisateur ("Ma musique
    preferee", CDC §8) - un seul morceau ecoute suffit a faire apparaitre
    son artiste et son album."""
    return (
        _available_tracks().join(MusicUsers, MusicUsers.TrackId == MusicTrack.TrackId)
        .filter(MusicUsers.UsersId == users_id, MusicUsers.CountListen > 0)
    )


def _albums_of(tracks):
    """Albums distincts d'une liste de morceaux, dans l'ordre d'apparition."""
    return list(dict.fromkeys(t.album for t in tracks if t.album is not None))


def _artist_rows(artists):
    return [{"artist": a} for a in artists]


def _album_rows(albums):
    return [{"album": a} for a in albums]


def _get_groups(users_id, sort, q):
    """Sections de l'ecran Musique (CDC §8). Tris Artiste/Album : tout le
    catalogue (MP3 locaux + morceaux YouTube ajoutes par les utilisateurs) ;
    "Ma musique preferee" : uniquement les morceaux ecoutes par
    l'utilisateur. Chaque ligne mene a l'ecran suivant de la hierarchie
    Artiste -> Album -> Morceaux (library.artist_detail / library.album_detail)."""
    if q and len(q) >= 3:
        like = f"%{q}%"
        # Section bibliotheque : MP3 locaux seulement - les morceaux YouTube
        # sont deja couverts par la section "YouTube Music" juste dessous.
        tracks = (
            _available_tracks().filter(MusicTrack.Source == music_catalog.SOURCE_LOCAL).join(MusicArtist, MusicTrack.ArtistId == MusicArtist.ArtistId)
            .outerjoin(MusicAlbum, MusicTrack.AlbumId == MusicAlbum.AlbumId)
            .filter(db.or_(MusicTrack.Title.ilike(like), MusicArtist.Name.ilike(like), MusicAlbum.Title.ilike(like)))
            .order_by(MusicAlbum.Title.asc(), *_track_order())
            .all()
        )

        # Cascade Album -> Artiste -> Morceau (CDC §8, meme principe que la
        # recherche YouTube) : le niveau le plus specifique qui explique a
        # lui seul tous les resultats gagne, pour eviter de forcer un detour
        # par l'artiste quand la recherche cible deja un album precis.
        # Exception : si la recherche vise le nom de l'artiste lui-meme (ex.
        # "beck"), c'est l'artiste qui s'affiche, meme s'il n'a qu'un album ou
        # qu'un morceau.
        artists = list(dict.fromkeys(t.artist for t in tracks))
        if len(artists) == 1 and q.lower() in artists[0].Name.lower():
            return [{"mode": "artist_flat", "artists": _artist_rows(artists)}]

        if len(tracks) == 1:
            return [{"mode": "flat", "tracks": tracks, "new_ids": music_catalog.new_track_ids(users_id, [tracks[0].TrackId])}]

        # Morceaux "plus populaires" : comptent pour trouver l'artiste, pas
        # pour faire apparaitre leur album ou un morceau isole.
        library_tracks = [t for t in tracks if not t.FromTop]
        albums = _albums_of(library_tracks)
        loose_tracks = [t for t in library_tracks if t.album is None]
        if len(albums) == 1 and not loose_tracks and len(library_tracks) == len(tracks):
            return [{"mode": "album_flat", "albums": _album_rows(albums)}]

        if len(artists) == 1:
            return [{"mode": "artist_flat", "artists": _artist_rows(artists)}]

        return [{
            "mode": "search",
            "albums": _album_rows(albums),
            "loose_tracks": loose_tracks,
            "new_ids": music_catalog.new_track_ids(users_id, [t.TrackId for t in loose_tracks]),
        }]

    descending = sort.endswith("_za")

    if sort in ("favorites_az", "favorites_za"):
        artist_ids = _favorite_tracks_query(users_id).with_entities(MusicTrack.ArtistId).distinct()
        order = MusicArtist.Name.desc() if descending else MusicArtist.Name.asc()
        artists = MusicArtist.query.filter(MusicArtist.ArtistId.in_(artist_ids)).order_by(order).all()
        return [{"mode": "artist_flat", "artists": _artist_rows(artists), "fav": True}]

    if sort in ("album_az", "album_za"):
        order = MusicAlbum.Title.desc() if descending else MusicAlbum.Title.asc()
        album_ids = _library_tracks(_available_tracks()).with_entities(MusicTrack.AlbumId).filter(MusicTrack.AlbumId.isnot(None)).distinct()
        albums = MusicAlbum.query.filter(MusicAlbum.AlbumId.in_(album_ids)).order_by(order).all()
        return [{"mode": "album_flat", "albums": _album_rows(albums)}]

    # sort in ("artist_az", "artist_za") - defaut (CDC §8). Artistes qui ont
    # au moins un morceau (pas 'Various Artists', artiste d'album seulement).
    order = MusicArtist.Name.desc() if descending else MusicArtist.Name.asc()
    artist_ids = _available_tracks().with_entities(MusicTrack.ArtistId).distinct()
    artists = MusicArtist.query.filter(MusicArtist.ArtistId.in_(artist_ids)).order_by(order).all()
    return [{"mode": "artist_flat", "artists": _artist_rows(artists)}]


# Recherches d'images (Deezer/iTunes) faites pendant l'affichage de l'ecran :
# peu, pour ne pas le ralentir ; le job 'mp3_scan' complete tout le catalogue.
THUMB_LOOKUP_LIMIT = 6


def _fill_thumbs(groups):
    """Image de chaque ligne Artiste/Album ('thumb'). Les images manquantes
    sont normalement deja resolues par le scan planifie ; on complete ici en
    secours (morceaux YouTube, MP3 ajoute depuis le dernier scan). Un artiste
    sans photo reprend la pochette d'un de ses albums."""
    artist_rows = [r for g in groups for r in g.get("artists", [])]
    album_rows = [r for g in groups for r in g.get("albums", [])]
    music_catalog.ensure_images(
        albums=[r["album"] for r in album_rows], artists=[r["artist"] for r in artist_rows], limit=THUMB_LOOKUP_LIMIT
    )

    missing = [r["artist"].ArtistId for r in artist_rows if not r["artist"].PictureUrl]
    fallback = {}
    if missing:
        rows = (
            db.session.query(MusicTrack.ArtistId, MusicAlbum.CoverUrl)
            .join(MusicAlbum, MusicTrack.AlbumId == MusicAlbum.AlbumId)
            .filter(MusicTrack.ArtistId.in_(missing), MusicTrack.IsAvailable.is_(True), MusicAlbum.CoverUrl.isnot(None))
        )
        for artist_id, url in rows:
            fallback.setdefault(artist_id, url)

    youtube_artists = _youtube_only(MusicTrack.ArtistId, [r["artist"].ArtistId for r in artist_rows])
    youtube_albums = _youtube_only(MusicTrack.AlbumId, [r["album"].AlbumId for r in album_rows])
    for r in artist_rows:
        r["thumb"] = cover_for(r["artist"]) or sized(fallback.get(r["artist"].ArtistId), 250)
        r["youtube_only"] = r["artist"].ArtistId in youtube_artists
    for r in album_rows:
        r["thumb"] = cover_for(r["album"])
        r["youtube_only"] = r["album"].AlbumId in youtube_albums


def _youtube_only(column, ids) -> set[int]:
    """Parmi `ids` (artistes ou albums selon `column`), ceux dont tous les
    morceaux disponibles viennent de YouTube Music - pastille YouTube."""
    if not ids:
        return set()
    sources: dict[int, set[str]] = {}
    rows = _available_tracks().with_entities(column, MusicTrack.Source).filter(column.in_(ids)).distinct()
    for item_id, source in rows:
        sources.setdefault(item_id, set()).add(source)
    return {i for i, found in sources.items() if found == {music_catalog.SOURCE_YOUTUBE}}


DEFAULT_SORT = "artist_az"
SORTS = ("artist_az", "artist_za", "album_az", "album_za", "favorites_az", "favorites_za")


def _list_context(q, sort):
    """Contenu de la liste de l'ecran Musique : bibliotheque + resultats
    YouTube Music d'une recherche. Memorise la recherche/le tri en session
    pour que le bouton retour des ecrans suivants y ramene (_library_url)."""
    session["library_list"] = {"q": q, "sort": sort}
    groups = _get_groups(current_user.UsersId, sort, q)
    _fill_thumbs(groups)

    yt_mode, yt_data, yt_songs = None, None, []
    if q and len(q) >= 3 and current_user.YoutubeMusicEnabled:
        smart = youtube_music_service.search_smart(q)
        yt_mode = smart["mode"]
        if yt_mode == "songs":
            yt_songs = smart["songs"]
        else:
            yt_data = smart
    return {"groups": groups, "q": q, "sort": sort, "yt_mode": yt_mode, "yt_data": yt_data, "yt_songs": yt_songs}


def _library_url(sort=None):
    """Ecran Musique tel que l'utilisateur l'a laisse (recherche, tri) -
    cible des boutons retour. `sort` : tri a imposer si le tri memorise est
    d'une autre famille (ex. retour d'un artiste de "Ma musique preferee")."""
    state = session.get("library_list") or {}
    q = state.get("q") or None
    remembered = state.get("sort") or saved_sort("library", DEFAULT_SORT)
    if sort and remembered.split("_")[0] != sort.split("_")[0]:
        remembered, q = sort, None
    return url_for("library.index", q=q, sort=remembered if remembered != saved_sort("library", DEFAULT_SORT) else None)


def _back_arg():
    """Parametre `back` : ecran d'ou l'on vient (ex. albums YouTube d'un
    artiste -> titres d'un album), limite aux URL internes de /library."""
    back = request.args.get("back", "")
    return back if back.startswith("/library/") else None


@bp.route("/")
@login_required
def index():
    log_screen_view("Library")

    q = request.args.get("q", "").strip()
    sort = requested_sort("library", SORTS, DEFAULT_SORT)
    return render_template("library/index.html", **_list_context(q, sort))


@bp.route("/list")
@login_required
def list_fragment():
    """Fragment htmx utilise par SearchMusic et SortByMusic (CDC §8)."""
    q = request.args.get("q", "").strip()
    sort = requested_sort("library", SORTS, DEFAULT_SORT)
    return render_template("library/_list.html", **_list_context(q, sort))


# --------------------------------------------------------------------------
# YouTube Music (previsualisation ephemere, puis ajout au catalogue)
# --------------------------------------------------------------------------

@bp.route("/youtube/artist")
@login_required
def youtube_artist():
    """Albums d'un artiste trouve sur YouTube Music (niveau 2 de la
    hierarchie Artiste -> Album -> Morceaux cote recherche YouTube, CDC §8) -
    prevue ephemere, rien n'est enregistre tant que l'utilisateur n'ajoute
    pas un morceau precis."""
    channel_id = request.args.get("channel_id", "").strip()
    name = request.args.get("name", "").strip()
    if not channel_id or not name or not current_user.YoutubeMusicEnabled:
        abort(404)

    log_screen_view("LibraryYoutubeArtist")
    albums = youtube_music_service.albums_for_artist(channel_id)

    return render_template("library/youtube_artist.html", name=name, albums=albums, back_url=_library_url())


@bp.route("/youtube/album")
@login_required
def youtube_album():
    """Morceaux d'un album trouve sur YouTube Music (niveau 3, CDC §8) -
    previsualisation ephemere, comme youtube_artist ci-dessus."""
    browse_id = request.args.get("browse_id", "").strip()
    if not browse_id or not current_user.YoutubeMusicEnabled:
        abort(404)

    log_screen_view("LibraryYoutubeAlbum")
    album = youtube_music_service.tracks_for_album(browse_id)
    if not album:
        abort(404)

    return render_template("library/youtube_album.html", album=album, back_url=_back_arg() or _library_url())


def _youtube_track(song, artist_name=None, album_title=None, album_artist_name=None, track_number=None, from_top=False):
    """Ajoute (idempotent) un morceau YouTube Music au catalogue partage.
    `from_top` : ajout via "Les plus populaires" - le morceau n'est marque
    FromTop que s'il est nouveau ; tout autre ajout ("Ecouter", import de
    l'album) le fait entrer pour de bon dans la bibliotheque."""
    artist_name = artist_name or song.get("artist")
    track, created = music_catalog.get_or_create_track(
        music_catalog.SOURCE_YOUTUBE,
        song["video_id"],
        song.get("title"),
        artist_name,
        album_title=album_title if album_title is not None else song.get("album"),
        album_artist_name=album_artist_name or artist_name,
        track_number=track_number,
    )
    if not from_top:
        track.FromTop = False
    elif created:
        track.FromTop = True
    return track


@bp.route("/youtube/album/import")
@login_required
def youtube_album_import():
    """Importe tous les morceaux d'un album YouTube dans le catalogue (CDC §8)
    puis redirige vers l'ecran album normal - evite de laisser l'utilisateur
    sur l'ecran de previsualisation (boutons Ajouter) une fois que l'album
    fait officiellement partie de la bibliotheque, et reutilise le
    retour/historique deja correct de library.album_detail."""
    browse_id = request.args.get("browse_id", "").strip()
    if not browse_id or not current_user.YoutubeMusicEnabled:
        abort(404)

    album = youtube_music_service.tracks_for_album(browse_id)
    if not album or not album["tracks"]:
        abort(404)

    tracks = [
        _youtube_track(t, album_title=album["title"], album_artist_name=album["artist"], track_number=i)
        for i, t in enumerate(album["tracks"], start=1)
    ]
    db.session.commit()
    first = tracks[0]
    music_catalog.ensure_images(albums=[first.album], artists=[first.artist])

    if first.album is None:
        return redirect(url_for("library.artist_detail", artist_id=first.ArtistId))
    return redirect(url_for("library.album_detail", album_id=first.AlbumId, autoplay=1, back=_back_arg()))


@bp.route("/youtube/track/add", methods=["GET", "POST"])
@login_required
def youtube_track_add():
    """Ajoute et lance immediatement un morceau YouTube trouve via "Ecouter"
    (recherche ou details d'album YouTube, CDC §8), puis redirige vers
    l'ecran album (ou artiste si le morceau n'a pas d'album) - meme ecran
    "deja dans la bibliotheque" que pour un import d'album entier ou un
    morceau local, au lieu de rester sur l'ecran de previsualisation."""
    if not current_user.YoutubeMusicEnabled:
        abort(404)

    video_id = request.args.get("video_id", "").strip()
    title = request.args.get("title", "").strip()
    if not video_id or not title:
        abort(404)

    song = {
        "video_id": video_id,
        "title": title,
        "artist": request.args.get("artist", "").strip(),
        "album": request.args.get("album", "").strip() or None,
    }
    track = _youtube_track(song)
    db.session.commit()
    music_catalog.ensure_images(albums=[track.album], artists=[track.artist])
    playback = _play_track(track)
    # POST (clic sur la ligne, tunoovaStartPlayback) : lecture sur place,
    # l'utilisateur reste sur la liste de resultats.
    if request.method == "POST":
        return playback

    if track.AlbumId:
        return redirect(url_for("library.album_detail", album_id=track.AlbumId, back=_back_arg()))
    return redirect(url_for("library.artist_detail", artist_id=track.ArtistId))


# --------------------------------------------------------------------------
# Ecrans Artiste / Album / Plus populaires
# --------------------------------------------------------------------------

def _artist_albums(artist_id, fav):
    """Albums d'un artiste (les siens + les compilations ou il figure) et ses
    morceaux de la bibliotheque. `fav` : restreint a ce que l'utilisateur a
    ecoute (tri "Ma musique preferee")."""
    base = _favorite_tracks_query(current_user.UsersId) if fav else _available_tracks()
    tracks = _library_tracks(base).filter(MusicTrack.ArtistId == artist_id).order_by(*_track_order()).all()

    albums = _albums_of(tracks)
    if not fav:
        available_album_ids = _library_tracks(_available_tracks()).with_entities(MusicTrack.AlbumId)
        own = MusicAlbum.query.filter(MusicAlbum.ArtistId == artist_id, MusicAlbum.AlbumId.in_(available_album_ids)).all()
        albums = list(dict.fromkeys(own + albums))
    albums.sort(key=lambda a: (a.Year or 9999, a.Title.lower()))
    return albums, tracks


@bp.route("/artist/<int:artist_id>")
@login_required
def artist_detail(artist_id):
    """Ecran des albums d'un artiste (niveau 2 de la hierarchie Artiste ->
    Album -> Morceaux, CDC §8) : ses albums + les compilations ou il figure,
    puis ses morceaux hors album. `fav=1` : restreint a ce que l'utilisateur
    a ecoute (tri "Ma musique preferee")."""
    artist = db.session.get(MusicArtist, artist_id) or abort(404)
    fav = bool(request.args.get("fav"))

    log_screen_view("LibraryArtist")
    albums, tracks = _artist_albums(artist_id, fav)
    loose_tracks = [t for t in tracks if t.album is None]

    album_rows = _album_rows(albums)
    _fill_thumbs([{"albums": album_rows}])
    music_catalog.ensure_images(artists=[artist])

    return render_template(
        "library/artist.html",
        artist=artist,
        albums=album_rows,
        loose_tracks=loose_tracks,
        new_ids=music_catalog.new_track_ids(current_user.UsersId, [t.TrackId for t in loose_tracks]),
        fav=fav,
        show_top=current_user.YoutubeMusicEnabled,
        back_url=_library_url(sort="favorites_az" if fav else None),
    )


@bp.route("/artist/<int:artist_id>/others")
@login_required
def artist_other_albums(artist_id):
    """Ecran "Autres albums" d'un artiste (CDC §8) : ses albums de la
    bibliotheque absents de l'ecran de l'artiste (en "Ma musique preferee",
    ceux jamais ecoutes), puis ses albums YouTube Music pas encore dans la
    bibliotheque - previsualisation ephemere comme youtube_artist, rien
    n'est enregistre tant qu'un album n'est pas importe."""
    artist = db.session.get(MusicArtist, artist_id) or abort(404)
    fav = bool(request.args.get("fav"))

    log_screen_view("LibraryArtistOtherAlbums")
    library_albums, _ = _artist_albums(artist_id, False)
    shown = set(_artist_albums(artist_id, True)[0]) if fav else set(library_albums)
    local_rows = _album_rows([a for a in library_albums if a not in shown])
    _fill_thumbs([{"albums": local_rows}])

    yt_albums = []
    if current_user.YoutubeMusicEnabled:
        channel_id = youtube_music_service.artist_channel_id(artist.Name)
        known = {music_catalog.normalize_key(a.Title) for a in library_albums}
        yt_albums = [
            a for a in (youtube_music_service.albums_for_artist(channel_id) if channel_id else [])
            if music_catalog.normalize_key(a["title"]) not in known
        ]

    return render_template(
        "library/youtube_artist.html",
        name=artist.Name,
        local_albums=local_rows,
        albums=yt_albums,
        show_top=False,
        back_url=url_for("library.artist_detail", artist_id=artist_id, fav=1 if fav else None),
    )


def _artist_detail_redirect(name):
    artist = MusicArtist.query.filter_by(NameKey=music_catalog.normalize_key(name)).first() or abort(404)
    return redirect(url_for("library.artist_detail", artist_id=artist.ArtistId))


@bp.route("/artist/top")
@login_required
def artist_top():
    """Ecran des morceaux les plus populaires d'un artiste sur YouTube Music
    (CDC §8) - ajoutes au catalogue sous leur vrai album YouTube, donc
    lisibles via le meme Precedent/Suivant que le reste de la bibliotheque."""
    name = request.args.get("name", "").strip()
    if not name:
        abort(404)
    # YouTube Music desactive (profil) ou sans resultat : le clic sur
    # l'artiste (ligne de l'ecran Musique) mene a ses albums - un 404 serait
    # ignore par hx-boost et le clic ne ferait rien.
    if not current_user.YoutubeMusicEnabled:
        return _artist_detail_redirect(name)

    log_screen_view("LibraryArtistTop")
    songs = youtube_music_service.top_tracks_for_artist(name)
    if not songs:
        return _artist_detail_redirect(name)
    artist = music_catalog.get_or_create_artist(name)
    tracks = [_youtube_track(s, artist_name=artist.Name, from_top=True) for s in songs]
    db.session.commit()
    music_catalog.ensure_images(albums=_albums_of(tracks), artists=[artist])

    autoplay_url = url_for("library.artist_top_play", name=name) if request.args.get("autoplay") and tracks else None

    return render_template(
        "library/top_tracks.html",
        artist=artist,
        tracks=tracks,
        new_ids=music_catalog.new_track_ids(current_user.UsersId, [t.TrackId for t in tracks]),
        autoplay_url=autoplay_url,
        back_url=url_for("library.artist_detail", artist_id=artist.ArtistId),
    )


@bp.route("/artist/top/play", methods=["POST"])
@login_required
def artist_top_play():
    name = request.args.get("name", "").strip()
    if not name or not current_user.YoutubeMusicEnabled:
        return jsonify({"error": "not available"}), 404

    songs = youtube_music_service.top_tracks_for_artist(name)
    if not songs:
        return jsonify({"error": "no results"}), 404

    artist = music_catalog.get_or_create_artist(name)
    tracks = [_youtube_track(s, artist_name=artist.Name, from_top=True) for s in songs]
    db.session.commit()
    music_catalog.ensure_images(albums=[tracks[0].album], artists=[artist])
    return _play_track(tracks[0], queue=[t.TrackId for t in tracks])


@bp.route("/album/<int:album_id>")
@login_required
def album_detail(album_id):
    """Ecran des morceaux d'un album (niveau 3 de la hierarchie Artiste ->
    Album -> Morceaux, CDC §8) - affiche tout l'album, pas de pagination.
    `fav=1` : seulement les morceaux ecoutes par l'utilisateur."""
    album = db.session.get(MusicAlbum, album_id) or abort(404)
    fav = bool(request.args.get("fav"))

    log_screen_view("LibraryAlbum")
    base = _favorite_tracks_query(current_user.UsersId) if fav else _available_tracks()
    tracks = base.filter(MusicTrack.AlbumId == album_id).order_by(*_track_order()).all()
    music_catalog.ensure_images(albums=[album])

    track_ids = [t.TrackId for t in tracks]
    new_ids = music_catalog.new_track_ids(current_user.UsersId, track_ids)
    if new_ids:
        MusicUsers.query.filter(
            MusicUsers.UsersId == current_user.UsersId, MusicUsers.TrackId.in_(new_ids)
        ).update({"MusicStatus": "Unread"}, synchronize_session=False)
        db.session.commit()

    autoplay_url = url_for("library.album_play", album_id=album_id, fav=1 if fav else None) if request.args.get("autoplay") and tracks else None

    if _back_arg():
        back_url = _back_arg()
    elif album.IsCompilation:
        back_url = _library_url(sort="album_az")
    else:
        back_url = url_for("library.artist_detail", artist_id=album.ArtistId, fav=1 if fav else None)

    return render_template(
        "library/album.html",
        album=album,
        tracks=tracks,
        new_ids=new_ids,
        autoplay_url=autoplay_url,
        back_url=back_url,
    )


@bp.route("/album/<int:album_id>/play", methods=["POST"])
@login_required
def album_play(album_id):
    """Lance la lecture d'un album en un clic (premier morceau, CDC §8) -
    meme principe que artist_top_play pour un artiste."""
    base = _favorite_tracks_query(current_user.UsersId) if request.args.get("fav") else _available_tracks()
    track = base.filter(MusicTrack.AlbumId == album_id).order_by(*_track_order()).first()
    if not track:
        return jsonify({"error": "not found"}), 404
    return _play_track(track)


# --------------------------------------------------------------------------
# Lecture
# --------------------------------------------------------------------------

def _play_track(track, queue=None):
    """Lecture d'un morceau du catalogue : cree au besoin le lien
    utilisateur <-> morceau (MusicUsers) qui porte ses propres compteurs.
    `queue` : TrackIds a suivre pour Precedent/Suivant (ex. "Les plus
    populaires") ; None = ordre habituel de l'artiste."""
    play_service.set_music_queue(current_user.UsersId, queue)
    stream_url = play_service.music_stream_url(track)
    # set_current_play compte l'ecoute quand le morceau change ; relancer le
    # morceau deja en cours compte aussi.
    if not play_service.set_current_play(current_user.UsersId, "music", track.TrackId, stream_url):
        play_service.record_music_listen(current_user.UsersId, track.TrackId)

    return jsonify(
        {
            "stream_url": stream_url,
            "title": track.Title,
            "subtitle": track.artist.Name,
            "thumbnail": cover_for(track),
            "play_url": url_for("play.show", content_type="music", item_id=track.TrackId),
        }
    )


@bp.route("/<int:track_id>/play", methods=["POST"])
@login_required
def play(track_id):
    track = db.session.get(MusicTrack, track_id)
    if track is None or not track.IsAvailable:
        abort(404)
    # File explicite passee par l'ecran "Les plus populaires" (?queue=12,15,9)
    queue = [int(i) for i in request.args.get("queue", "").split(",") if i.isdigit()]
    return _play_track(track, queue=queue if track_id in queue else None)
