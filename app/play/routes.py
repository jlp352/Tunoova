from flask import render_template, request, redirect, url_for, flash, abort, jsonify
from flask_login import login_required, current_user

from app.play import bp
from app.extensions import db
from app.logging_util import log_screen_view
from app.i18n import translate
from app import play_service

_VALID_TYPES = ("podcast", "radio", "music")


def _wants_json() -> bool:
    """Le panneau #play-overlay (app/static/js/app.js) envoie cet en-tete sur
    ses fetch() pour recevoir du JSON au lieu de la redirection habituelle -
    utilisee par la page complete /play/... (repli SSR/JS desactive)."""
    return request.headers.get("X-Requested-With") == "XMLHttpRequest"


@bp.route("/current")
@login_required
def current():
    """Lecture en cours en JSON : resynchronise le mini-lecteur d'un appareil
    revenu au premier plan apres une lecture sur un autre (app.js)."""
    return jsonify(play_service.current_play_summary(current_user.UsersId))


@bp.route("/<content_type>/<int:item_id>")
@login_required
def show(content_type, item_id):
    if content_type not in _VALID_TYPES:
        abort(404)

    context = play_service.get_context(content_type, item_id, current_user.UsersId)
    if not context:
        abort(404)

    log_screen_view("Play")

    play_service.set_current_play(current_user.UsersId, content_type, item_id, context["stream_url"])

    return render_template(
        "play/index.html",
        content_type=content_type,
        item_id=item_id,
        ctx=context,
        comment_active=play_service.user_commented(context, current_user.UsersId),
    )


@bp.route("/<content_type>/<int:item_id>/data")
@login_required
def data(content_type, item_id):
    """Donnees JSON pour le panneau #play-overlay (ouverture/changement de
    piste sans navigation, cf. app/static/js/app.js). Meme resolution que
    show() (y compris l'effet de bord set_current_play, deja sans reset de
    position quand il s'agit de l'item deja en cours).

    ?prefetch=1 : prechargement du morceau suivant pendant que le courant
    joue encore (cf. tunoovaPreloadNext) - ni set_current_play, ni ecoute
    comptee, ni vue d'ecran : l'item ne devient courant qu'a l'enchainement."""
    if content_type not in _VALID_TYPES:
        abort(404)

    context = play_service.get_context(content_type, item_id, current_user.UsersId)
    if not context:
        abort(404)

    if request.args.get("prefetch") != "1":
        log_screen_view("Play")
        play_service.set_current_play(current_user.UsersId, content_type, item_id, context["stream_url"])

    current_play = play_service.get_current_play(current_user.UsersId)
    playing = bool(current_play and current_play.PlayStatus == "Play")

    return jsonify(play_service.to_overlay_json(content_type, item_id, context, playing, current_play))


@bp.route("/<content_type>/<int:item_id>/like", methods=["POST"])
@login_required
def like(content_type, item_id):
    if content_type not in _VALID_TYPES:
        abort(404)
    context = play_service.get_context(content_type, item_id, current_user.UsersId)
    if not context:
        abort(404)

    value = request.form.get("value")
    if value in ("Like", "Not Like"):
        play_service.apply_like(
            context["item"],
            context["catalog"],
            context["like_attr"],
            context["catalog_like_attr"],
            context["catalog_notlike_attr"],
            value,
        )
        db.session.commit()

    if _wants_json():
        current_value = getattr(context["item"], context["like_attr"])
        return jsonify(
            like_active=current_value == "Like",
            not_like_active=current_value == "Not Like",
            like_count=(context["catalog"] and getattr(context["catalog"], context["catalog_like_attr"], 0)) or 0,
            not_like_count=(context["catalog"] and getattr(context["catalog"], context["catalog_notlike_attr"], 0)) or 0,
        )

    return redirect(url_for("play.show", content_type=content_type, item_id=item_id))


@bp.route("/<content_type>/<int:item_id>/comment", methods=["POST"])
@login_required
def comment(content_type, item_id):
    if content_type not in _VALID_TYPES:
        abort(404)
    context = play_service.get_context(content_type, item_id, current_user.UsersId)
    if not context:
        abort(404)

    text = request.form.get("text", "").strip()
    if context["comment_thread"]:
        # Musique, radio, podcast : chaque envoi ajoute un commentaire au fil.
        if text:
            play_service.add_thread_comment(context, current_user.UsersId, text)
        if _wants_json():
            return _comments_json(item_id, context)
    else:
        play_service.apply_comment(context["item"], context["catalog"], context["comment_attr"], context["catalog_comment_attr"], text)
        db.session.commit()

    if _wants_json():
        return jsonify(
            ok=True,
            comment_count=play_service.comment_count(context),
            comment_active=play_service.user_commented(context, current_user.UsersId),
        )

    flash("play_comment_saved", "success")
    return redirect(url_for("play.show", content_type=content_type, item_id=item_id))


def _comments_json(item_id, context):
    return jsonify(
        comments=play_service.thread_comments(context, item_id, current_user.UsersId),
        comment_count=play_service.comment_count(context),
        comment_active=play_service.user_commented(context, current_user.UsersId),
    )


@bp.route("/<content_type>/<int:item_id>/comments")
@login_required
def comments(content_type, item_id):
    """Fil de commentaires d'un morceau ou d'une radio (fenetre Commentaires de l'ecran Play)."""
    context = play_service.get_context(content_type, item_id, current_user.UsersId) if content_type in _VALID_TYPES else None
    if not context or not context["comment_thread"]:
        abort(404)
    return _comments_json(item_id, context)


@bp.route("/<content_type>/<int:item_id>/comments/<int:comment_id>/delete", methods=["POST"])
@login_required
def comment_delete(content_type, item_id, comment_id):
    context = play_service.get_context(content_type, item_id, current_user.UsersId) if content_type in _VALID_TYPES else None
    if not context or not context["comment_thread"]:
        abort(404)
    play_service.delete_thread_comment(context, current_user.UsersId, comment_id)
    if _wants_json():
        return _comments_json(item_id, context)
    return redirect(url_for("play.show", content_type=content_type, item_id=item_id))


@bp.route("/<content_type>/<int:item_id>/share", methods=["POST"])
@login_required
def share(content_type, item_id):
    if content_type not in _VALID_TYPES:
        abort(404)

    to_users_ids = request.form.getlist("to_users_id", type=int)
    ok = bool(to_users_ids) and play_service.share_with_users(content_type, item_id, current_user.UsersId, to_users_ids) > 0

    if _wants_json():
        return jsonify(ok=ok, message=translate("play_share_sent" if ok else "play_share_failed"))

    flash("play_share_sent" if ok else "play_share_failed", "success" if ok else "error")
    return redirect(url_for("play.show", content_type=content_type, item_id=item_id))


@bp.route("/state", methods=["POST"])
@login_required
def state():
    """Rapporte periodiquement la position/etat de lecture (JS, CDC §2)."""
    status = request.form.get("status")
    position = request.form.get("position", type=float)
    play_service.update_play_state(current_user.UsersId, status=status, position=position)
    return ("", 204)
