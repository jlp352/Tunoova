from flask import render_template, request, redirect, url_for
from flask_login import login_required, current_user

from app.userprofile import bp
from app.extensions import db
from app.models import Users
from app.logging_util import log_screen_view
from app import profile_service
from app.url_util import safe_local_url

VALID_LANGUAGES = ("fr", "en")


@bp.route("/", methods=["GET", "POST"])
@login_required
def index():
    log_screen_view("UserProfile")

    if request.method == "POST":
        is_htmx = request.headers.get("HX-Request") == "true"
        next_url = safe_local_url(request.form.get("next"), url_for("main.home"))

        fullname = request.form.get("fullname", "").strip()
        email = request.form.get("email", "").strip().lower()
        age_range = request.form.get("age_range") or ""
        country = request.form.get("country") or ""

        error = None
        if not fullname:
            error = "userprofile_error_fullname_required"
        elif not email:
            error = "userprofile_error_email_required"
        elif email != current_user.Email and Users.query.filter_by(Email=email).first():
            error = "userprofile_error_email_taken"
        elif not age_range:
            error = "userprofile_error_age_range_required"
        elif not country:
            error = "userprofile_error_country_required"

        if error:
            if is_htmx:
                return render_template("userprofile/_save_status.html", error=error)
            age_ranges, hobbies, music_genres, countries = profile_service.get_reference_lists()
            return render_template(
                "userprofile/index.html",
                age_ranges=age_ranges,
                hobbies=hobbies,
                music_genres=music_genres,
                countries=countries,
                next_url=next_url,
                error=error,
                form_values=request.form,
            )

        current_user.Email = email
        current_user.FullName = fullname
        current_user.Notifications = request.form.get("notifications") == "on"
        current_user.AgeRange = age_range
        current_user.Hobby = ", ".join(request.form.getlist("hobby"))
        current_user.MusicGenre = ", ".join(request.form.getlist("music_genre"))
        current_user.Country = country

        langue = request.form.get("langue")
        if langue in VALID_LANGUAGES:
            current_user.Langue = langue

        photo_path = profile_service.save_profile_photo(request.files.get("photo"))
        if photo_path:
            current_user.Photo = photo_path

        db.session.commit()

        if is_htmx:
            return render_template("userprofile/_save_status.html", error=None)
        return redirect(next_url)

    next_url = safe_local_url(request.args.get("next") or request.referrer, url_for("main.home"))
    age_ranges, hobbies, music_genres, countries = profile_service.get_reference_lists()

    return render_template(
        "userprofile/index.html",
        age_ranges=age_ranges,
        hobbies=hobbies,
        music_genres=music_genres,
        countries=countries,
        next_url=next_url,
        error=None,
        form_values=None,
    )
