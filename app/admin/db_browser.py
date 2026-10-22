"""Table browser generique : CRUD sur toutes les tables de la base (CDC §9,
etendu a la demande a l'ensemble du schema plutot qu'aux seuls referentiels).
Passe par SQLAlchemy Core (db.metadata) plutot que par les modeles ORM un par
un, pour couvrir automatiquement toute table presente ou future sans code
dedie par table."""

import re
from datetime import datetime

from flask import render_template, request, redirect, url_for, flash, abort
from sqlalchemy import select, func
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.types import Boolean, Date, DateTime

from app.admin import bp
from app.admin.decorators import admin_required
from app.extensions import db
from app.i18n import translate

PAGE_SIZE = 50
_SENSITIVE_RE = re.compile(r"password|hash|secret|token|apikey", re.IGNORECASE)

# Categorie/description de chaque table, repris du Modele de donnees du
# cahier des charges (Doc/Cahier des charges.md §5). Valeurs = suffixes de
# cles de traduction (admin_category_<category>, admin_desc_<desc>), pas de
# texte en dur - cf. TABLE_METADATA utilise dans db_tables(). Une table
# absente de ce dict (nouveau modele ajoute plus tard) retombe sur la
# categorie "Autres" avec une description vide - le table browser reste
# generique, ce mapping n'est que de la presentation.
TABLE_METADATA = {
    "Users": {"category": "accounts", "desc": "users"},
    "Langues": {"category": "reference", "desc": "langues"},
    "AgeRange": {"category": "reference", "desc": "agerange"},
    "Hobby": {"category": "reference", "desc": "hobby"},
    "MusicGenre": {"category": "reference", "desc": "musicgenre"},
    "Country": {"category": "reference", "desc": "country"},
    "Podcast": {"category": "podcasts", "desc": "podcast"},
    "PodcastUsers": {"category": "podcasts", "desc": "podcastusers"},
    "EpisodesUsers": {"category": "podcasts", "desc": "episodesusers"},
    "PodcastComment": {"category": "podcasts", "desc": "podcastcomment"},
    "Radio": {"category": "radios", "desc": "radio"},
    "RadioUsers": {"category": "radios", "desc": "radiousers"},
    "RadioComment": {"category": "radios", "desc": "radiocomment"},
    "RadioPlugIn": {"category": "radios", "desc": "radioplugin"},
    "MusicArtist": {"category": "music", "desc": "musicartist"},
    "MusicAlbum": {"category": "music", "desc": "musicalbum"},
    "MusicTrack": {"category": "music", "desc": "musictrack"},
    "MusicUsers": {"category": "music", "desc": "musicusers"},
    "MusicTrackComment": {"category": "music", "desc": "musictrackcomment"},
    "Play": {"category": "activity", "desc": "play"},
    "Log": {"category": "activity", "desc": "log"},
    "CastDevice": {"category": "activity", "desc": "castdevice"},
    "LoginHistory": {"category": "activity", "desc": "loginhistory"},
}


def _is_sensitive(column_name: str) -> bool:
    return bool(_SENSITIVE_RE.search(column_name))


def _get_table(table_name):
    table = db.metadata.tables.get(table_name)
    if table is None:
        abort(404)
    return table


def _pk_column(table):
    pk_cols = list(table.primary_key.columns)
    if len(pk_cols) != 1:
        abort(400, description=translate("admin_msg_composite_pk_unsupported", table=table.name))
    return pk_cols[0]


def _cast_pk(pk, raw_value: str):
    try:
        return pk.type.python_type(raw_value)
    except (ValueError, TypeError):
        return raw_value


def _field_type(column) -> str:
    if isinstance(column.type, Boolean):
        return "boolean"
    if isinstance(column.type, DateTime):
        return "datetime"
    if isinstance(column.type, Date):
        return "date"
    return "text"


def _coerce_value(column, raw_value: str):
    field_type = _field_type(column)
    if field_type == "boolean":
        return raw_value == "on"
    if raw_value == "":
        return None
    if field_type == "datetime":
        # datetime-local peut envoyer "YYYY-MM-DDTHH:MM" ou avec secondes.
        return datetime.fromisoformat(raw_value)
    if field_type == "date":
        return datetime.strptime(raw_value, "%Y-%m-%d").date()
    return raw_value


def _format_input_value(column, value):
    """Formate une valeur pour l'attribut value d'un <input> HTML.
    datetime-local exige "YYYY-MM-DDTHH:MM" (pas d'espace ni de microsecondes),
    sinon le navigateur affiche un champ vide."""
    if value is None:
        return ""
    field_type = _field_type(column)
    if field_type == "datetime":
        if isinstance(value, str):
            try:
                value = datetime.fromisoformat(value)
            except ValueError:
                return ""
        return value.strftime("%Y-%m-%dT%H:%M")
    if field_type == "date":
        if isinstance(value, datetime):
            value = value.date()
        return value.isoformat() if hasattr(value, "isoformat") else str(value)
    return value


# Operateurs de filtre exposes dans le table browser. Les operateurs textuels
# (contains/starts_with/ends_with) restent utilisables sur une colonne
# date/datetime : compares a la representation texte stockee (ex. "2026-01"
# pour retrouver tout janvier), en plus de "between" demande specifiquement
# pour ces colonnes. Valeurs = cles de traduction, traduites a la volee dans
# _filter_ops_for() (pas au chargement du module : translate() a besoin d'un
# contexte de requete pour connaitre la langue courante).
FILTER_OP_KEYS = [
    ("eq", "admin_op_eq"),
    ("ne", "admin_op_ne"),
    ("contains", "admin_op_contains"),
    ("not_contains", "admin_op_not_contains"),
    ("starts_with", "admin_op_starts_with"),
    ("ends_with", "admin_op_ends_with"),
    ("between", "admin_op_between"),
]
_BOOLEAN_FILTER_OP_KEYS = [("eq", "admin_op_eq"), ("ne", "admin_op_ne")]
_TEXT_FILTER_OPS = {"contains", "not_contains", "starts_with", "ends_with"}


def _filter_ops_for(field_type: str):
    keys = _BOOLEAN_FILTER_OP_KEYS if field_type == "boolean" else FILTER_OP_KEYS
    return [(value, translate(label_key)) for value, label_key in keys]


def _parse_filter_value(column, raw_value: str, op: str):
    if raw_value == "":
        return None
    if op in _TEXT_FILTER_OPS:
        # Comparaison texte brute quel que soit le type de la colonne.
        return raw_value
    field_type = _field_type(column)
    if field_type == "boolean":
        return raw_value == "on"
    if field_type == "datetime":
        try:
            return datetime.strptime(raw_value, "%Y-%m-%dT%H:%M")
        except ValueError:
            return raw_value
    if field_type == "date":
        try:
            return datetime.strptime(raw_value, "%Y-%m-%d").date()
        except ValueError:
            return raw_value
    return raw_value


def _apply_filter(stmt, column, op: str, value, value2=None):
    if op == "eq":
        return stmt.where(column == value)
    if op == "ne":
        return stmt.where(column != value)
    if op == "contains":
        return stmt.where(column.like(f"%{value}%"))
    if op == "not_contains":
        return stmt.where(~column.like(f"%{value}%"))
    if op == "starts_with":
        return stmt.where(column.like(f"{value}%"))
    if op == "ends_with":
        return stmt.where(column.like(f"%{value}%"))
    if op == "between":
        return stmt.where(column.between(value, value2))
    return stmt


def _form_values(fields):
    """Construit le dict de valeurs a ecrire depuis request.form, en ignorant
    les colonnes sensibles (jamais soumises depuis le formulaire, cf. CDC §9)."""
    values = {}
    for col in fields:
        if _is_sensitive(col.name):
            continue
        raw = request.form.get(col.name, "")
        values[col.name] = _coerce_value(col, raw)
    return values


@bp.route("/db")
@admin_required
def db_tables():
    sort_field = request.args.get("sort_field", "category")
    sort_dir = request.args.get("sort_dir", "asc")
    if sort_field not in ("category", "name", "count"):
        sort_field = "category"

    tables = []
    for table in db.metadata.sorted_tables:
        count = db.session.execute(select(func.count()).select_from(table)).scalar()
        meta = TABLE_METADATA.get(table.name, {})
        desc_key = meta.get("desc")
        tables.append(
            {
                "name": table.name,
                "count": count,
                "category": translate(f"admin_category_{meta.get('category', 'other')}"),
                "description": translate(f"admin_desc_{desc_key}") if desc_key else "",
            }
        )

    reverse = sort_dir == "desc"
    if sort_field == "name":
        tables.sort(key=lambda t: t["name"], reverse=reverse)
    elif sort_field == "count":
        tables.sort(key=lambda t: t["count"], reverse=reverse)
    else:
        # Categorie puis nom, pour un affichage groupe stable (cf. gabarit).
        tables.sort(key=lambda t: (t["category"], t["name"]), reverse=reverse)

    return render_template(
        "admin/db_tables.html", tables=tables, sort_field=sort_field, sort_dir=sort_dir
    )


@bp.route("/db/<table_name>")
@admin_required
def db_table_rows(table_name):
    table = _get_table(table_name)
    pk = _pk_column(table)
    page = max(1, request.args.get("page", 1, type=int))

    # Colonnes proposees au filtre/tri : on exclut les colonnes sensibles
    # (pas de recherche sur un hash de mot de passe, cf. CDC §9).
    filterable_columns = [col for col in table.columns if not _is_sensitive(col.name)]

    filter_field = request.args.get("filter_field", "")
    filter_op = request.args.get("filter_op", "eq")
    filter_value = request.args.get("filter_value", "")
    filter_value2 = request.args.get("filter_value2", "")
    sort_field = request.args.get("sort_field", "")
    sort_dir = request.args.get("sort_dir", "asc")

    filter_column = table.columns.get(filter_field) if filter_field else None
    if filter_column is not None and _is_sensitive(filter_column.name):
        filter_column = None
    filter_field_type = _field_type(filter_column) if filter_column is not None else "text"
    if filter_field_type == "boolean" and filter_op not in ("eq", "ne"):
        filter_op = "eq"

    stmt = select(table)
    count_stmt = select(func.count()).select_from(table)
    filter_error = None

    if filter_column is not None:
        value = _parse_filter_value(filter_column, filter_value, filter_op)
        value2 = _parse_filter_value(filter_column, filter_value2, "eq") if filter_op == "between" else None
        if value is not None and (filter_op != "between" or value2 is not None):
            try:
                stmt = _apply_filter(stmt, filter_column, filter_op, value, value2)
                count_stmt = _apply_filter(count_stmt, filter_column, filter_op, value, value2)
                db.session.execute(count_stmt).scalar()  # valide le filtre avant de l'utiliser pour de bon
            except SQLAlchemyError:
                db.session.rollback()
                filter_error = translate("admin_msg_filter_invalid")
                stmt = select(table)
                count_stmt = select(func.count()).select_from(table)

    sort_column = table.columns.get(sort_field) if sort_field else None
    if sort_column is None or _is_sensitive(sort_column.name):
        sort_column = pk
    order_col = sort_column.desc() if sort_dir == "desc" else sort_column.asc()
    stmt = stmt.order_by(order_col).limit(PAGE_SIZE).offset((page - 1) * PAGE_SIZE)

    total = db.session.execute(count_stmt).scalar()
    rows = db.session.execute(stmt).mappings().all()
    columns = [{"name": col.name, "sensitive": _is_sensitive(col.name)} for col in table.columns]

    active_query = {
        "filter_field": filter_field,
        "filter_op": filter_op,
        "filter_value": filter_value,
        "filter_value2": filter_value2,
        "sort_field": sort_field,
        "sort_dir": sort_dir,
    }

    return render_template(
        "admin/db_table_rows.html",
        table_name=table_name,
        columns=columns,
        filterable_columns=[col.name for col in filterable_columns],
        rows=rows,
        pk_name=pk.name,
        page=page,
        total_pages=max(1, -(-total // PAGE_SIZE)),
        filter_field=filter_field,
        filter_op=filter_op,
        filter_value=filter_value,
        filter_value2=filter_value2,
        filter_field_type=filter_field_type,
        filter_ops=_filter_ops_for(filter_field_type),
        filter_error=filter_error,
        sort_field=sort_field,
        sort_dir=sort_dir,
        active_query=active_query,
    )


@bp.route("/db/<table_name>/new", methods=["GET", "POST"])
@admin_required
def db_row_new(table_name):
    table = _get_table(table_name)
    pk = _pk_column(table)
    fields = [col for col in table.columns if col is not pk]

    if request.method == "POST":
        try:
            db.session.execute(table.insert().values(**_form_values(fields)))
            db.session.commit()
        except SQLAlchemyError as exc:
            db.session.rollback()
            error_detail = exc.orig if hasattr(exc, "orig") else exc
            flash(translate("admin_msg_row_add_failed", error=error_detail), "error")
            return redirect(url_for("admin.db_row_new", table_name=table_name))
        flash(translate("admin_msg_row_added"), "success")
        return redirect(url_for("admin.db_table_rows", table_name=table_name))

    field_defs = [
        {
            "name": col.name,
            "type": _field_type(col),
            "sensitive": _is_sensitive(col.name),
            "required": not col.nullable and col.default is None,
            "value": "",
        }
        for col in fields
    ]
    return render_template(
        "admin/db_row_form.html",
        table_name=table_name,
        fields=field_defs,
        pk_name=pk.name,
        pk_value=None,
        mode="new",
    )


@bp.route("/db/<table_name>/<pk_value>/edit", methods=["GET", "POST"])
@admin_required
def db_row_edit(table_name, pk_value):
    table = _get_table(table_name)
    pk = _pk_column(table)
    fields = [col for col in table.columns if col is not pk]
    cast_pk_value = _cast_pk(pk, pk_value)

    row = db.session.execute(select(table).where(pk == cast_pk_value)).mappings().first()
    if row is None:
        abort(404)

    if request.method == "POST":
        try:
            db.session.execute(table.update().where(pk == cast_pk_value).values(**_form_values(fields)))
            db.session.commit()
        except SQLAlchemyError as exc:
            db.session.rollback()
            error_detail = exc.orig if hasattr(exc, "orig") else exc
            flash(translate("admin_msg_row_update_failed", error=error_detail), "error")
            return redirect(url_for("admin.db_row_edit", table_name=table_name, pk_value=pk_value))
        flash(translate("admin_msg_row_updated"), "success")
        return redirect(url_for("admin.db_table_rows", table_name=table_name))

    field_defs = [
        {
            "name": col.name,
            "type": _field_type(col),
            "sensitive": _is_sensitive(col.name),
            "required": not col.nullable and col.default is None,
            "value": "" if _is_sensitive(col.name) else _format_input_value(col, row[col.name]),
        }
        for col in fields
    ]
    return render_template(
        "admin/db_row_form.html",
        table_name=table_name,
        fields=field_defs,
        pk_name=pk.name,
        pk_value=pk_value,
        mode="edit",
    )


@bp.route("/db/<table_name>/<pk_value>/delete", methods=["POST"])
@admin_required
def db_row_delete(table_name, pk_value):
    table = _get_table(table_name)
    pk = _pk_column(table)
    cast_pk_value = _cast_pk(pk, pk_value)

    try:
        db.session.execute(table.delete().where(pk == cast_pk_value))
        db.session.commit()
    except SQLAlchemyError as exc:
        db.session.rollback()
        error_detail = exc.orig if hasattr(exc, "orig") else exc
        flash(translate("admin_msg_row_delete_failed", error=error_detail), "error")
        return redirect(url_for("admin.db_table_rows", table_name=table_name))

    flash(translate("admin_msg_row_deleted"), "success")
    return redirect(url_for("admin.db_table_rows", table_name=table_name))
