"""Peuple les tables referentielles (Langues, AgeRange, Hobby, MusicGenre, Country)
a partir des listes ci-dessous (CDC §5).
Idempotent : ne cree que ce qui n'existe pas encore."""

import secrets

from sqlalchemy import inspect, text

from app.extensions import db
from app.models import Langues, AgeRange, Hobby, MusicGenre, Country, Users, EpisodesUsers, Play

LANGUAGES = [
    ("en", "English", True), ("fr", "French", True),
    ("ab", "Abkhazian", False), ("aa", "Afar", False), ("af", "Afrikaans", False),
    ("ak", "Akan", False), ("sq", "Albanian", False), ("am", "Amharic", False),
    ("ar", "Arabic", False), ("an", "Aragonese", False), ("hy", "Armenian", False),
    ("as", "Assamese", False), ("av", "Avaric", False), ("ae", "Avestan", False),
    ("ay", "Aymara", False), ("az", "Azerbaijani", False), ("bm", "Bambara", False),
    ("ba", "Bashkir", False), ("eu", "Basque", False), ("be", "Belarusian", False),
    ("bn", "Bengali", False), ("bi", "Bislama", False), ("bs", "Bosnian", False),
    ("br", "Breton", False), ("bg", "Bulgarian", False), ("my", "Burmese", False),
    ("ca", "Catalan, Valencian", False), ("ch", "Chamorro", False), ("ce", "Chechen", False),
    ("ny", "Chichewa, Chewa, Nyanja", False), ("zh", "Chinese", False),
    ("cu", "Church Slavonic", False), ("cv", "Chuvash", False), ("kw", "Cornish", False),
    ("co", "Corsican", False), ("cr", "Cree", False), ("hr", "Croatian", False),
    ("cs", "Czech", False), ("da", "Danish", False),
    ("dv", "Divehi, Dhivehi, Maldivian", False), ("nl", "Dutch, Flemish", False),
    ("dz", "Dzongkha", False), ("eo", "Esperanto", False), ("et", "Estonian", False),
    ("ee", "Ewe", False), ("fo", "Faroese", False), ("fj", "Fijian", False),
    ("fi", "Finnish", False), ("fy", "Western Frisian", False), ("ff", "Fulah", False),
    ("gd", "Gaelic, Scottish Gaelic", False), ("gl", "Galician", False),
    ("lg", "Ganda", False), ("ka", "Georgian", False), ("de", "German", False),
    ("el", "Greek", False), ("kl", "Kalaallisut, Greenlandic", False),
    ("gn", "Guarani", False), ("gu", "Gujarati", False),
    ("ht", "Haitian, Haitian Creole", False), ("ha", "Hausa", False),
    ("he", "Hebrew", False), ("hz", "Herero", False), ("hi", "Hindi", False),
    ("ho", "Hiri Motu", False), ("hu", "Hungarian", False), ("is", "Icelandic", False),
    ("io", "Ido", False), ("ig", "Igbo", False), ("id", "Indonesian", False),
    ("ia", "Interlingua", False), ("ie", "Interlingue, Occidental", False),
    ("iu", "Inuktitut", False), ("ik", "Inupiaq", False), ("ga", "Irish", False),
    ("it", "Italian", False), ("ja", "Japanese", False), ("jv", "Javanese", False),
    ("kn", "Kannada", False), ("kr", "Kanuri", False), ("ks", "Kashmiri", False),
    ("kk", "Kazakh", False), ("km", "Central Khmer", False),
    ("ki", "Kikuyu, Gikuyu", False), ("rw", "Kinyarwanda", False),
    ("ky", "Kyrgyz, Kirghiz", False), ("kv", "Komi", False), ("kg", "Kongo", False),
    ("ko", "Korean", False), ("kj", "Kuanyama, Kwanyama", False), ("ku", "Kurdish", False),
    ("lo", "Lao", False), ("la", "Latin", False), ("lv", "Latvian", False),
    ("li", "Limburgan, Limburger, Limburgish", False), ("ln", "Lingala", False),
    ("lt", "Lithuanian", False), ("lu", "Luba-Katanga", False),
    ("lb", "Luxembourgish", False), ("mk", "Macedonian", False),
    ("mg", "Malagasy", False), ("ms", "Malay", False), ("ml", "Malayalam", False),
    ("mt", "Maltese", False), ("gv", "Manx", False), ("mi", "Maori", False),
    ("mr", "Marathi", False), ("mh", "Marshallese", False), ("mn", "Mongolian", False),
    ("na", "Nauru", False), ("nv", "Navajo, Navaho", False),
    ("nd", "North Ndebele", False), ("nr", "South Ndebele", False),
    ("ng", "Ndonga", False), ("ne", "Nepali", False), ("no", "Norwegian", False),
    ("nb", "Norwegian Bokmål", False), ("nn", "Norwegian Nynorsk", False),
    ("oc", "Occitan", False), ("oj", "Ojibwa", False), ("or", "Oriya", False),
    ("om", "Oromo", False), ("os", "Ossetian, Ossetic", False), ("pi", "Pali", False),
    ("ps", "Pashto, Pushto", False), ("fa", "Persian", False), ("pl", "Polish", False),
    ("pt", "Portuguese", False), ("pa", "Punjabi, Panjabi", False),
    ("qu", "Quechua", False), ("ro", "Romanian, Moldavian, Moldovan", False),
    ("rm", "Romansh", False), ("rn", "Rundi", False), ("ru", "Russian", False),
    ("se", "Northern Sami", False), ("sm", "Samoan", False), ("sg", "Sango", False),
    ("sa", "Sanskrit", False), ("sc", "Sardinian", False), ("sr", "Serbian", False),
    ("sn", "Shona", False), ("sd", "Sindhi", False), ("si", "Sinhala, Sinhalese", False),
    ("sk", "Slovak", False), ("sl", "Slovenian", False), ("so", "Somali", False),
    ("st", "Southern Sotho", False), ("es", "Spanish, Castilian", False),
    ("su", "Sundanese", False), ("sw", "Swahili", False), ("ss", "Swati", False),
    ("sv", "Swedish", False), ("tl", "Tagalog", False), ("ty", "Tahitian", False),
    ("tg", "Tajik", False), ("ta", "Tamil", False), ("tt", "Tatar", False),
    ("te", "Telugu", False), ("th", "Thai", False), ("bo", "Tibetan", False),
    ("ti", "Tigrinya", False), ("to", "Tonga (Tonga Islands)", False),
    ("ts", "Tsonga", False), ("tn", "Tswana", False), ("tr", "Turkish", False),
    ("tk", "Turkmen", False), ("tw", "Twi", False), ("ug", "Uighur, Uyghur", False),
    ("uk", "Ukrainian", False), ("ur", "Urdu", False), ("uz", "Uzbek", False),
    ("ve", "Venda", False), ("vi", "Vietnamese", False), ("vo", "Volapük", False),
    ("wa", "Walloon", False), ("cy", "Welsh", False), ("wo", "Wolof", False),
    ("xh", "Xhosa", False), ("ii", "Sichuan Yi, Nuosu", False), ("yi", "Yiddish", False),
    ("yo", "Yoruba", False), ("za", "Zhuang, Chuang", False), ("zu", "Zulu", False),
]

AGE_RANGES = [
    ("Silent Generation", "1928 – 1945"),
    ("Baby Boomers", "1946 – 1964"),
    ("Generation X", "1965 – 1979"),
    ("Generation Y -Millennials", "1980 – 1994"),
    ("Generation Z", "1995 – 2009"),
    ("Generation Alpha", "2010 – 2024"),
    ("Generation Beta", "2025 – 2039"),
]

HOBBIES = [
    ("Drawing / Painting", "Creative & Artistic Activities"),
    ("Photography", "Creative & Artistic Activities"),
    ("Writing (journal, novel, poetry)", "Creative & Artistic Activities"),
    ("DIY / Crafting", "Creative & Artistic Activities"),
    ("Sewing / Knitting", "Creative & Artistic Activities"),
    ("Ceramics / Pottery", "Creative & Artistic Activities"),
    ("Calligraphy", "Creative & Artistic Activities"),
    ("Reading", "Intellectual Hobbies"),
    ("Language Learning", "Intellectual Hobbies"),
    ("Chess / Strategy Games", "Intellectual Hobbies"),
    ("Podcasts / Documentaries", "Intellectual Hobbies"),
    ("Coding", "Intellectual Hobbies"),
    ("Genealogy", "Intellectual Hobbies"),
    ("Running / Trail Running", "Physical & Sports Activities"),
    ("Yoga / Pilates", "Physical & Sports Activities"),
    ("Swimming", "Physical & Sports Activities"),
    ("Cycling", "Physical & Sports Activities"),
    ("Climbing", "Physical & Sports Activities"),
    ("Martial Arts", "Physical & Sports Activities"),
    ("Dancing", "Physical & Sports Activities"),
    ("Gardening", "Nature & Wellness Hobbies"),
    ("Hiking", "Nature & Wellness Hobbies"),
    ("Bird Watching", "Nature & Wellness Hobbies"),
    ("Meditation", "Nature & Wellness Hobbies"),
    ("Camping", "Nature & Wellness Hobbies"),
    ("Beekeeping", "Nature & Wellness Hobbies"),
    ("Video Games", "Digital & Tech Hobbies"),
    ("3D Printing", "Digital & Tech Hobbies"),
    ("Virtual Reality", "Digital & Tech Hobbies"),
    ("Video Editing", "Digital & Tech Hobbies"),
    ("Streaming / Content Creation", "Digital & Tech Hobbies"),
    ("Crypto / Blockchain", "Digital & Tech Hobbies"),
    ("World Cuisine", "Culinary Hobbies"),
    ("Baking", "Culinary Hobbies"),
    ("Mixology (Cocktails)", "Culinary Hobbies"),
    ("Barbecue / Smoking", "Culinary Hobbies"),
    ("Fermentation (Kombucha)", "Culinary Hobbies"),
    ("Traveling", "Exploration & Cultural Hobbies"),
    ("Geocaching", "Exploration & Cultural Hobbies"),
    ("Museum Visits", "Exploration & Cultural Hobbies"),
    ("Collecting (Stamps, etc.)", "Exploration & Cultural Hobbies"),
    ("Volunteering / Humanitarian Work", "Exploration & Cultural Hobbies"),
]

MUSIC_GENRES = [
    ("Pop", "Popular Music"), ("Rock", "Popular Music"), ("Rap / Hip-hop", "Popular Music"),
    ("RnB", "Popular Music"), ("Reggaeton", "Popular Music"), ("Electro / EDM", "Popular Music"),
    ("Funk", "Popular Music"), ("Soul", "Popular Music"),
    ("Classical", "Classical & Art Music"), ("Baroque", "Classical & Art Music"),
    ("Opera", "Classical & Art Music"), ("Contemporary Classical", "Classical & Art Music"),
    ("Chamber Music", "Classical & Art Music"),
    ("African Music", "World Music"), ("Latin Music", "World Music"),
    ("Asian Music", "World Music"), ("Middle Eastern Music", "World Music"),
    ("Celtic Music", "World Music"),
    ("House", "Electronic Music"), ("Techno", "Electronic Music"), ("Trance", "Electronic Music"),
    ("Drum and Bass", "Electronic Music"), ("Dubstep", "Electronic Music"),
    ("Ambient", "Electronic Music"), ("Chillwave", "Electronic Music"),
    ("Classic Rock", "Rock & Metal"), ("Punk Rock", "Rock & Metal"), ("Hard Rock", "Rock & Metal"),
    ("Metal", "Rock & Metal"), ("Grunge", "Rock & Metal"), ("Indie Rock", "Rock & Metal"),
    ("French Chanson", "Vocal & Traditional Music"), ("Gospel", "Vocal & Traditional Music"),
    ("Country", "Vocal & Traditional Music"), ("Folk", "Vocal & Traditional Music"),
    ("Blues", "Vocal & Traditional Music"), ("Jazz", "Vocal & Traditional Music"),
    ("Ska", "Vocal & Traditional Music"),
    ("Trip-hop", "Experimental & Hybrid"), ("Lo-fi", "Experimental & Hybrid"),
    ("Avant-garde", "Experimental & Hybrid"), ("Noise", "Experimental & Hybrid"),
    ("Post-rock", "Experimental & Hybrid"), ("Electroacoustic", "Experimental & Hybrid"),
]


# Perimetre europeen (CDC : app destinee a un usage en Europe). Nom affiche,
# puis nom exact attendu par le filtre 'country' de l'API Radio Browser quand
# il differe du nom affiche.
COUNTRIES = [
    ("Albania", None), ("Andorra", None), ("Austria", None), ("Belarus", None),
    ("Belgium", None), ("Bosnia and Herzegovina", "Bosnia And Herzegovina"),
    ("Bulgaria", None), ("Croatia", None), ("Cyprus", None), ("Czechia", None),
    ("Denmark", None), ("Estonia", None), ("Finland", None), ("France", None),
    ("Germany", None), ("Gibraltar", None), ("Greece", None), ("Hungary", None),
    ("Iceland", None), ("Ireland", None), ("Italy", None), ("Kosovo", None),
    ("Latvia", None), ("Liechtenstein", None), ("Lithuania", None), ("Luxembourg", None),
    ("Malta", None), ("Moldova", "The Republic Of Moldova"), ("Monaco", None),
    ("Montenegro", None), ("Netherlands", "The Netherlands"),
    ("North Macedonia", "Republic Of North Macedonia"), ("Norway", None),
    ("Poland", None), ("Portugal", None), ("Romania", None), ("San Marino", None),
    ("Serbia", None), ("Slovakia", None), ("Slovenia", None), ("Spain", None),
    ("Sweden", None), ("Switzerland", None), ("Turkey", "Türkiye"), ("Ukraine", None),
    ("United Kingdom", "The United Kingdom Of Great Britain And Northern Ireland"),
]


def ensure_schema_upgrades():
    """db.create_all() ne cree que les tables absentes, il ne modifie pas une
    table existante (pas d'Alembic dans ce projet). Ajoute donc a la main les
    colonnes manquantes sur les tables deja presentes - idempotent et sans
    danger sur les donnees existantes (ADD COLUMN nullable)."""
    inspector = inspect(db.engine)
    if "Users" in inspector.get_table_names():
        columns = {col["name"] for col in inspector.get_columns("Users")}
        if "Country" not in columns:
            db.session.execute(text("ALTER TABLE Users ADD COLUMN Country VARCHAR(80)"))
            db.session.commit()
        if "LastLogin" not in columns:
            db.session.execute(text("ALTER TABLE Users ADD COLUMN LastLogin DATETIME"))
            db.session.commit()
        if "SortPrefs" not in columns:
            db.session.execute(text("ALTER TABLE Users ADD COLUMN SortPrefs JSON"))
            db.session.commit()
        if "LibrarySort" in columns:
            # Remplacee par SortPrefs (un JSON pour tous les ecrans)
            db.session.execute(text(
                "UPDATE Users SET SortPrefs = json_object('library', LibrarySort) "
                "WHERE LibrarySort IS NOT NULL AND LibrarySort <> 'artist_az'"))
            db.session.execute(text("ALTER TABLE Users DROP COLUMN LibrarySort"))
            db.session.commit()
    # Refonte du modele musique (MusicArtist -> MusicAlbum -> MusicTrack,
    # MusicUsers reference TrackId) : environnement de test, pas de migration
    # des donnees - l'ancien schema (MusicUsers.PathFile, table Music) est
    # supprime puis recree vide, le prochain scan MP3 remplit le catalogue.
    if "MusicUsers" in inspector.get_table_names():
        columns = {col["name"] for col in inspector.get_columns("MusicUsers")}
        if "TrackId" not in columns:
            for table in ("MusicUsers", "Music", "ArtistImage"):
                db.session.execute(text(f'DROP TABLE IF EXISTS "{table}"'))
            db.session.execute(text("DELETE FROM Play WHERE PlayType = 'Music'"))
            db.session.commit()
            db.create_all()
    if "MusicTrack" in inspector.get_table_names():
        columns = {col["name"] for col in inspector.get_columns("MusicTrack")}
        if "IsAvailable" not in columns:
            db.session.execute(text("ALTER TABLE MusicTrack ADD COLUMN IsAvailable BOOLEAN NOT NULL DEFAULT 1"))
            db.session.commit()
        if "MissingSince" not in columns:
            db.session.execute(text("ALTER TABLE MusicTrack ADD COLUMN MissingSince DATETIME"))
            db.session.commit()
        if "FileMtime" not in columns:
            db.session.execute(text("ALTER TABLE MusicTrack ADD COLUMN FileMtime FLOAT"))
            db.session.commit()
        if "FileSize" not in columns:
            db.session.execute(text("ALTER TABLE MusicTrack ADD COLUMN FileSize INTEGER"))
            db.session.commit()
        if "FromTop" not in columns:
            db.session.execute(text("ALTER TABLE MusicTrack ADD COLUMN FromTop BOOLEAN NOT NULL DEFAULT 0"))
            db.session.commit()
        if "MusicCount" not in columns:
            # Point de depart : somme des ecoutes deja comptees par utilisateur.
            db.session.execute(text("ALTER TABLE MusicTrack ADD COLUMN MusicCount INTEGER NOT NULL DEFAULT 0"))
            db.session.execute(text(
                "UPDATE MusicTrack SET MusicCount = (SELECT COALESCE(SUM(CountListen), 0)"
                " FROM MusicUsers WHERE MusicUsers.TrackId = MusicTrack.TrackId)"
            ))
            db.session.commit()
    # Commentaires musique : un seul commentaire perso (MusicUsers.MusicComment)
    # -> fil de commentaires partage (table MusicTrackComment, creee par
    # db.create_all). Reprise des commentaires existants, puis recalcul du
    # compteur MusicTrack.MusicComment.
    if "MusicUsers" in inspector.get_table_names():
        columns = {col["name"] for col in inspector.get_columns("MusicUsers")}
        if "SharedBy" not in columns:
            db.session.execute(text("ALTER TABLE MusicUsers ADD COLUMN SharedBy INTEGER REFERENCES Users(UsersId)"))
            db.session.commit()
        if "MusicComment" in columns:
            db.session.execute(text(
                "INSERT INTO MusicTrackComment (TrackId, UsersId, CommentText, CreatedAt)"
                " SELECT TrackId, UsersId, TRIM(MusicComment), COALESCE(LastListen, CreatedAt) FROM MusicUsers"
                " WHERE TRIM(COALESCE(MusicComment, '')) <> ''"
            ))
            db.session.execute(text("ALTER TABLE MusicUsers DROP COLUMN MusicComment"))
            db.session.execute(text(
                "UPDATE MusicTrack SET MusicComment = (SELECT COUNT(*) FROM MusicTrackComment"
                " WHERE MusicTrackComment.TrackId = MusicTrack.TrackId)"
            ))
            db.session.commit()
        # MusicUsers.LastListen : ecrit a chaque ecoute mais jamais relu
        # (apres la reprise MusicComment ci-dessus, qui s'en sert).
        if "LastListen" in columns:
            db.session.execute(text("ALTER TABLE MusicUsers DROP COLUMN LastListen"))
            db.session.commit()
    if "Podcast" in inspector.get_table_names():
        columns = {col["name"] for col in inspector.get_columns("Podcast")}
        if "PodcastCount" not in columns:
            # Point de depart : somme des ecoutes deja comptees par utilisateur.
            db.session.execute(text("ALTER TABLE Podcast ADD COLUMN PodcastCount INTEGER NOT NULL DEFAULT 0"))
            db.session.execute(text(
                "UPDATE Podcast SET PodcastCount = (SELECT COALESCE(SUM(CountListen), 0)"
                " FROM PodcastUsers WHERE PodcastUsers.PodcastId = Podcast.PodcastId)"
            ))
            db.session.commit()
    # Commentaires podcast : un commentaire perso par episode (EpisodesUsers.EpisodeComment)
    # -> fil de commentaires partage par podcast (table PodcastComment, creee par
    # db.create_all). Meme reprise que pour RadioComment ci-dessous.
    if "EpisodesUsers" in inspector.get_table_names():
        columns = {col["name"] for col in inspector.get_columns("EpisodesUsers")}
        if "EpisodeComment" in columns:
            db.session.execute(text(
                "INSERT INTO PodcastComment (PodcastId, UsersId, CommentText, CreatedAt)"
                " SELECT PodcastId, UsersId, TRIM(EpisodeComment), COALESCE(EpisodePublishDate, CURRENT_TIMESTAMP)"
                " FROM EpisodesUsers WHERE TRIM(COALESCE(EpisodeComment, '')) <> ''"
            ))
            db.session.execute(text("ALTER TABLE EpisodesUsers DROP COLUMN EpisodeComment"))
            db.session.execute(text(
                "UPDATE Podcast SET PodcastComment = (SELECT COUNT(*) FROM PodcastComment"
                " WHERE PodcastComment.PodcastId = Podcast.PodcastId)"
            ))
            db.session.commit()
        if "EpisodeGuid" not in columns:
            # Les episodes etaient reconnus par leur seule URL audio : chaque
            # changement d'URL cote editeur (tracking Ausha, reencodage Radio
            # France) les a reimportes en double. Fusion une seule fois, a
            # l'ajout de la colonne ; ensuite sync_episodes les reconnait.
            db.session.execute(text("ALTER TABLE EpisodesUsers ADD COLUMN EpisodeGuid VARCHAR(512)"))
            db.session.execute(text(
                'CREATE INDEX IF NOT EXISTS "ix_EpisodesUsers_EpisodeGuid" ON EpisodesUsers (EpisodeGuid)'))
            db.session.commit()
            _merge_duplicate_episodes()
    if "Radio" in inspector.get_table_names():
        columns = {col["name"] for col in inspector.get_columns("Radio")}
        if "RadioCount" not in columns:
            # Point de depart : somme des ecoutes deja comptees par utilisateur.
            db.session.execute(text("ALTER TABLE Radio ADD COLUMN RadioCount INTEGER NOT NULL DEFAULT 0"))
            db.session.execute(text(
                "UPDATE Radio SET RadioCount = (SELECT COALESCE(SUM(CountListen), 0)"
                " FROM RadioUsers WHERE RadioUsers.RadiosId = Radio.RadioId)"
            ))
            db.session.commit()
        if "RadioPlugInId" not in columns:
            db.session.execute(text("ALTER TABLE Radio ADD COLUMN RadioPlugInId INTEGER REFERENCES RadioPlugIn(RadioPlugInId)"))
            db.session.execute(text("ALTER TABLE Radio ADD COLUMN PlugInRef VARCHAR(100)"))
            db.session.execute(text("ALTER TABLE Radio ADD COLUMN PlugInCheckedAt DATETIME"))
            db.session.commit()
        if "PlugInStatus" not in columns:
            db.session.execute(text("ALTER TABLE Radio ADD COLUMN PlugInStatus VARCHAR(10)"))
            db.session.execute(text("ALTER TABLE Radio ADD COLUMN PlugInStatusDetail VARCHAR(1024)"))
            db.session.execute(text("ALTER TABLE Radio ADD COLUMN PlugInLastTest DATETIME"))
            db.session.commit()
    # L'ICY n'est plus un plug-in mais le repli integre (app/icy.py)
    if "RadioPlugIn" in inspector.get_table_names():
        db.session.execute(text(
            "UPDATE Radio SET RadioPlugInId = NULL, PlugInRef = NULL WHERE RadioPlugInId IN"
            " (SELECT RadioPlugInId FROM RadioPlugIn WHERE ScriptName = 'icy.py')"))
        db.session.execute(text("DELETE FROM RadioPlugIn WHERE ScriptName = 'icy.py'"))
        db.session.commit()
    # Commentaires radio : un seul commentaire perso (RadioUsers.RadioComment)
    # -> fil de commentaires partage par radio (table RadioComment, creee par
    # db.create_all). Meme reprise que pour MusicTrackComment ci-dessus.
    if "RadioUsers" in inspector.get_table_names():
        columns = {col["name"] for col in inspector.get_columns("RadioUsers")}
        # Partage de radio notifie au destinataire (comme MusicUsers)
        if "RadioStatus" not in columns:
            db.session.execute(text("ALTER TABLE RadioUsers ADD COLUMN RadioStatus VARCHAR(10)"))
            db.session.commit()
        if "SharedBy" not in columns:
            db.session.execute(text("ALTER TABLE RadioUsers ADD COLUMN SharedBy INTEGER REFERENCES Users(UsersId)"))
            db.session.commit()
        if "CreatedAt" not in columns:
            db.session.execute(text("ALTER TABLE RadioUsers ADD COLUMN CreatedAt DATETIME"))
            db.session.commit()
        if "RadioComment" in columns:
            db.session.execute(text(
                "INSERT INTO RadioComment (RadioId, UsersId, CommentText, CreatedAt)"
                " SELECT RadiosId, UsersId, TRIM(RadioComment), COALESCE(LastListen, CURRENT_TIMESTAMP) FROM RadioUsers"
                " WHERE TRIM(COALESCE(RadioComment, '')) <> ''"
            ))
            db.session.execute(text("ALTER TABLE RadioUsers DROP COLUMN RadioComment"))
            db.session.execute(text(
                "UPDATE Radio SET RadioComment = (SELECT COUNT(*) FROM RadioComment"
                " WHERE RadioComment.RadioId = Radio.RadioId)"
            ))
            db.session.commit()
    if "Play" in inspector.get_table_names():
        columns = {col["name"] for col in inspector.get_columns("Play")}
        if "CastDeviceId" not in columns:
            db.session.execute(text("ALTER TABLE Play ADD COLUMN CastDeviceId INTEGER REFERENCES CastDevice(CastDeviceId)"))
            db.session.commit()
        if "MusicQueue" not in columns:
            db.session.execute(text("ALTER TABLE Play ADD COLUMN MusicQueue JSON"))
            db.session.commit()
    # Comptes crees par l'admin avant la correction de l'audit SAST : leur mot
    # de passe temporaire etait "Users" + email, devinable tant que le lien
    # d'activation n'a pas ete utilise. Remplace par un aleatoire ; le lien
    # d'activation (ou "mot de passe oublie") reste le seul moyen d'entrer.
    for user in Users.query.filter_by(FirstLogin=True).all():
        if user.check_password(Users.__name__ + user.Email):
            user.set_password(secrets.token_urlsafe(32))
            db.session.commit()


_STATUS_RANK = {"New": 0, "Unread": 1, "Read": 2}


def _merge_duplicate_episodes():
    """Fusionne, par abonnement, les episodes qui partagent une cle
    (podcast_service.episode_keys). On garde le plus ancien (id stable pour
    la lecture en cours), avec le statut le plus avance des doublons, leur
    like et l'URL du plus recent (celle du flux actuel)."""
    from app.podcast_service import episode_keys

    episodes = EpisodesUsers.query.order_by(EpisodesUsers.PodcastUsersId, EpisodesUsers.EpisodesUsersId).all()
    keep_for: dict[int, EpisodesUsers] = {}  # EpisodesUsersId supprime -> episode conserve
    seen: dict[tuple, EpisodesUsers] = {}
    for episode in episodes:
        keys = {(episode.PodcastUsersId, *key) for key in episode_keys(
            None, episode.EpisodeURL, episode.EpisodeName, episode.EpisodePublishDate)}
        kept = next((seen[key] for key in keys if key in seen), None)
        if kept is None:
            for key in keys:
                seen[key] = episode
            continue
        for key in keys:
            seen.setdefault(key, kept)
        if _STATUS_RANK.get(episode.EpisodeStatus, 0) > _STATUS_RANK.get(kept.EpisodeStatus, 0):
            kept.EpisodeStatus = episode.EpisodeStatus
        kept.EpisodeLike = kept.EpisodeLike or episode.EpisodeLike
        kept.EpisodeURL = episode.EpisodeURL
        keep_for[episode.EpisodesUsersId] = kept

    if not keep_for:
        return
    for play in Play.query.filter(Play.PlayType == "Podcast", Play.ItemId.in_(keep_for)).all():
        play.ItemId = keep_for[play.ItemId].EpisodesUsersId
    EpisodesUsers.query.filter(EpisodesUsers.EpisodesUsersId.in_(keep_for)).delete(synchronize_session=False)
    db.session.commit()


def seed_reference_data():
    if not Langues.query.first():
        for code, name, used in LANGUAGES:
            db.session.add(Langues(LangCode=code, Langue=name, Used=used))

    if not AgeRange.query.first():
        for name, description in AGE_RANGES:
            db.session.add(AgeRange(AgeRange=name, Description=description, Used=True))

    if not Hobby.query.first():
        for name, category in HOBBIES:
            db.session.add(Hobby(HobbyName=name, Category=category, Used=True))

    if not MusicGenre.query.first():
        for name, category in MUSIC_GENRES:
            db.session.add(MusicGenre(MusicGenreName=name, Category=category, Used=True))

    if not Country.query.first():
        for name, api_name in COUNTRIES:
            db.session.add(Country(CountryName=name, ApiName=api_name, Used=True))

    db.session.commit()

    # Plug-ins "titre en cours" : une ligne RadioPlugIn par script de radio_plugins/
    from app import now_playing
    now_playing.sync_plugins()
