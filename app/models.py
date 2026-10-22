from datetime import datetime, timedelta

import bcrypt
from flask_login import UserMixin

from app.extensions import db

MAX_FAILED_LOGIN_ATTEMPTS = 5
LOCKOUT_DURATION = timedelta(minutes=15)


class Users(UserMixin, db.Model):
    __tablename__ = "Users"

    UsersId = db.Column(db.Integer, primary_key=True, autoincrement=True)
    Email = db.Column(db.String(320), unique=True, nullable=False, index=True)
    FullName = db.Column(db.String(120), nullable=False)
    Password = db.Column(db.String(60), nullable=False)  # hash bcrypt
    Photo = db.Column(db.String(255), nullable=True)
    Langue = db.Column(db.String(2), nullable=False, default="en")  # Langues.LangCode
    Notifications = db.Column(db.Boolean, nullable=False, default=True)
    AgeRange = db.Column(db.String(50), nullable=True)  # AgeRange.AgeRange, choix unique
    Hobby = db.Column(db.String(512), nullable=True)  # multi-choix, stocke separe par virgules
    MusicGenre = db.Column(db.String(512), nullable=True)  # multi-choix, stocke separe par virgules
    Country = db.Column(db.String(80), nullable=True)  # utilise pour prioriser la recherche Radio (CDC §8)
    FirstLogin = db.Column(db.Boolean, nullable=False, default=True)
    FailedLoginAttempts = db.Column(db.Integer, nullable=False, default=0)  # echecs de connexion consecutifs
    LockedUntil = db.Column(db.DateTime, nullable=True)  # compte verrouille jusqu'a cette date
    IsActive = db.Column(db.Boolean, nullable=False, default=True)
    LastLogin = db.Column(db.DateTime, nullable=True)  # ecran Admin - Comptes utilisateurs
    # Ajouts CDC §8/§12 (ecran Settings) : absents du schema initial,
    # necessaires pour persister ces reglages par utilisateur.
    DarkMode = db.Column(db.Boolean, nullable=False, default=False)
    YoutubeMusicEnabled = db.Column(db.Boolean, nullable=False, default=False)
    StreamQuality = db.Column(db.String(10), nullable=False, default="auto")  # auto; low; high
    SortPrefs = db.Column(db.JSON, nullable=True)  # dernier tri par ecran, cf. app/sort_prefs.py

    def get_id(self):
        return str(self.UsersId)

    @property
    def is_active(self):
        # Flask-Login desactive la session si is_active est False
        return self.IsActive

    def set_password(self, plain_password: str) -> None:
        self.Password = bcrypt.hashpw(plain_password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")

    def check_password(self, plain_password: str) -> bool:
        return bcrypt.checkpw(plain_password.encode("utf-8"), self.Password.encode("utf-8"))

    def is_locked(self) -> bool:
        return self.LockedUntil is not None and self.LockedUntil > datetime.utcnow()

    def register_failed_login(self) -> None:
        self.FailedLoginAttempts += 1
        if self.FailedLoginAttempts >= MAX_FAILED_LOGIN_ATTEMPTS:
            self.LockedUntil = datetime.utcnow() + LOCKOUT_DURATION

    def register_successful_login(self) -> None:
        self.FailedLoginAttempts = 0
        self.LockedUntil = None

    def hobby_list(self):
        return [h.strip() for h in (self.Hobby or "").split(",") if h.strip()]

    def music_genre_list(self):
        return [g.strip() for g in (self.MusicGenre or "").split(",") if g.strip()]


class Langues(db.Model):
    __tablename__ = "Langues"

    LangId = db.Column(db.Integer, primary_key=True, autoincrement=True)
    LangCode = db.Column(db.String(2), unique=True, nullable=False)
    Langue = db.Column(db.String(50), nullable=False)
    Used = db.Column(db.Boolean, nullable=False, default=False)


class AgeRange(db.Model):
    __tablename__ = "AgeRange"

    AgeRangeId = db.Column(db.Integer, primary_key=True, autoincrement=True)
    AgeRange = db.Column(db.String(30), unique=True, nullable=False)
    Description = db.Column(db.String(120), nullable=True)
    Used = db.Column(db.Boolean, nullable=False, default=True)


class Hobby(db.Model):
    __tablename__ = "Hobby"

    HobbyId = db.Column(db.Integer, primary_key=True, autoincrement=True)
    HobbyName = db.Column(db.String(50), unique=True, nullable=False)
    Category = db.Column(db.String(50), nullable=True)
    Used = db.Column(db.Boolean, nullable=False, default=True)


class MusicGenre(db.Model):
    __tablename__ = "MusicGenre"

    MusicGenreId = db.Column(db.Integer, primary_key=True, autoincrement=True)
    MusicGenreName = db.Column(db.String(50), unique=True, nullable=False)
    Category = db.Column(db.String(50), nullable=True)
    Used = db.Column(db.Boolean, nullable=False, default=True)


class Country(db.Model):
    __tablename__ = "Country"

    CountryId = db.Column(db.Integer, primary_key=True, autoincrement=True)
    CountryName = db.Column(db.String(80), unique=True, nullable=False)
    # Valeur exacte attendue par le filtre 'country' de l'API Radio Browser,
    # quand elle differe du nom affiche (ex: "United Kingdom" -> nom complet
    # officiel de l'API). Si vide, CountryName sert aussi de valeur API.
    ApiName = db.Column(db.String(120), nullable=True)
    Used = db.Column(db.Boolean, nullable=False, default=True)


class Podcast(db.Model):
    """Catalogue partage des podcasts (compteurs globaux)."""

    __tablename__ = "Podcast"

    PodcastId = db.Column(db.Integer, primary_key=True, autoincrement=True)
    PodcastName = db.Column(db.String(512), nullable=False)
    PodcastDescription = db.Column(db.String(1024), nullable=True)
    LinkRSS = db.Column(db.String(2056), nullable=False, unique=True)
    LinkImage = db.Column(db.String(2056), nullable=True)
    PodcastLike = db.Column(db.Integer, nullable=False, default=0)
    PodcastNotLike = db.Column(db.Integer, nullable=False, default=0)
    PodcastComment = db.Column(db.Integer, nullable=False, default=0)  # nombre de PodcastComment
    PodcastCount = db.Column(db.Integer, nullable=False, default=0)  # nombre d'ecoutes, tous utilisateurs
    Hobby = db.Column(db.String(50), nullable=True)  # Hobby.HobbyName, choix unique
    AgeRange = db.Column(db.String(50), nullable=True)  # AgeRange.AgeRange, choix unique


class PodcastUsers(db.Model):
    """Abonnement + stats propres a un utilisateur pour un podcast."""

    __tablename__ = "PodcastUsers"

    PodcastUsersId = db.Column(db.Integer, primary_key=True, autoincrement=True)
    UsersId = db.Column(db.Integer, db.ForeignKey("Users.UsersId"), nullable=False, index=True)
    PodcastName = db.Column(db.String(512), nullable=False)
    LinkRSS = db.Column(db.String(2056), nullable=False)
    LinkImage = db.Column(db.String(2056), nullable=True)
    PodcastId = db.Column(db.Integer, db.ForeignKey("Podcast.PodcastId"), nullable=False, index=True)
    LastListen = db.Column(db.DateTime, nullable=True)
    CountListen = db.Column(db.Integer, nullable=False, default=0)
    # Ajout : absent du schema source, correspond au champ "Filter" de l'ecran
    # EditPodcast (maquette) - syntaxe "max:20 contain:INTEGRALE exclude:LIVE"
    # appliquee lors de la synchro RSS (cf. podcast_service.sync_episodes).
    EpisodeFilter = db.Column(db.String(256), nullable=True)

    user = db.relationship("Users", backref="podcast_subscriptions")
    podcast = db.relationship("Podcast")


class EpisodesUsers(db.Model):
    __tablename__ = "EpisodesUsers"

    EpisodesUsersId = db.Column(db.Integer, primary_key=True, autoincrement=True)
    UsersId = db.Column(db.Integer, db.ForeignKey("Users.UsersId"), nullable=False, index=True)
    PodcastUsersId = db.Column(db.Integer, db.ForeignKey("PodcastUsers.PodcastUsersId"), nullable=False, index=True)
    PodcastId = db.Column(db.Integer, db.ForeignKey("Podcast.PodcastId"), nullable=False, index=True)
    EpisodeURL = db.Column(db.String(2056), nullable=False)
    # <guid> de l'item RSS : identifiant stable de l'episode, alors que l'URL
    # audio change (parametres de tracking Ausha, reencodage Radio France...).
    # NULL pour les episodes importes avant son ajout (renseigne a la synchro).
    EpisodeGuid = db.Column(db.String(512), nullable=True, index=True)
    EpisodeName = db.Column(db.String(512), nullable=True)
    EpisodeDescription = db.Column(db.String(2056), nullable=True)
    EpisodePublishDate = db.Column(db.DateTime, nullable=True)
    EpisodeStatus = db.Column(db.String(10), nullable=False, default="New")  # New; Unread; Read
    EpisodeLike = db.Column(db.String(10), nullable=True)  # Like; Not Like

    user = db.relationship("Users", backref="episodes")
    podcast_users = db.relationship("PodcastUsers", backref="episodes")


class PodcastComment(db.Model):
    """Commentaires d'un podcast, visibles par tous les utilisateurs qui y
    sont abonnes (ecran Play, quel que soit l'episode). Plusieurs commentaires
    possibles par utilisateur ; Podcast.PodcastComment en tient le compte."""

    __tablename__ = "PodcastComment"

    CommentId = db.Column(db.Integer, primary_key=True, autoincrement=True)
    PodcastId = db.Column(db.Integer, db.ForeignKey("Podcast.PodcastId"), nullable=False, index=True)
    UsersId = db.Column(db.Integer, db.ForeignKey("Users.UsersId"), nullable=False, index=True)  # auteur
    CommentText = db.Column(db.String(2056), nullable=False)
    CreatedAt = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)

    user = db.relationship("Users")
    podcast = db.relationship("Podcast")


class Radio(db.Model):
    """Catalogue partage des radios (compteurs globaux)."""

    __tablename__ = "Radio"

    RadioId = db.Column(db.Integer, primary_key=True, autoincrement=True)
    RadioName = db.Column(db.String(512), nullable=False)
    RadioDescription = db.Column(db.String(1024), nullable=True)
    LinkURL = db.Column(db.String(2056), nullable=False, unique=True)
    LinkImage = db.Column(db.String(2056), nullable=True)
    RadioLike = db.Column(db.Integer, nullable=False, default=0)
    RadioNotLike = db.Column(db.Integer, nullable=False, default=0)
    RadioComment = db.Column(db.Integer, nullable=False, default=0)  # nombre de RadioComment
    RadioCount = db.Column(db.Integer, nullable=False, default=0)  # nombre d'ecoutes, tous utilisateurs
    Hobby = db.Column(db.String(50), nullable=True)  # Hobby.HobbyName, choix unique
    AgeRange = db.Column(db.String(50), nullable=True)  # AgeRange.AgeRange, choix unique
    # Titre en cours (app/now_playing.py) : plug-in qui sait le recuperer et
    # identifiant de la station chez ce fournisseur (ex. Radio France : 1 =
    # France Inter). PlugInCheckedAt NULL = jamais detecte ; renseigne avec
    # RadioPlugInId NULL = aucun plug-in, seul le repli ICY est essaye.
    RadioPlugInId = db.Column(db.Integer, db.ForeignKey("RadioPlugIn.RadioPlugInId"), nullable=True)
    PlugInRef = db.Column(db.String(100), nullable=True)
    PlugInCheckedAt = db.Column(db.DateTime, nullable=True)
    # Dernier test du plug-in pour cette radio (bouton Tester de l'Admin)
    PlugInStatus = db.Column(db.String(10), nullable=True)  # ok ; error
    PlugInStatusDetail = db.Column(db.String(1024), nullable=True)
    PlugInLastTest = db.Column(db.DateTime, nullable=True)

    plugin = db.relationship("RadioPlugIn")


class RadioPlugIn(db.Model):
    """Plug-ins "titre en cours" (scripts du dossier radio_plugins/), un par
    fournisseur : la cle d'API sert a toutes les radios qui y sont liees."""

    __tablename__ = "RadioPlugIn"

    RadioPlugInId = db.Column(db.Integer, primary_key=True, autoincrement=True)
    PlugInName = db.Column(db.String(100), nullable=False)
    ScriptName = db.Column(db.String(100), nullable=False, unique=True)
    ApiKey = db.Column(db.String(512), nullable=True)
    Version = db.Column(db.Integer, nullable=False, default=1)  # +1 a chaque upload depuis l'Admin
    Enabled = db.Column(db.Boolean, nullable=False, default=True)
    Status = db.Column(db.String(10), nullable=True)  # ok ; error
    StatusDetail = db.Column(db.String(1024), nullable=True)
    LastCheck = db.Column(db.DateTime, nullable=True)


class RadioUsers(db.Model):
    __tablename__ = "RadioUsers"

    RadioUsersId = db.Column(db.Integer, primary_key=True, autoincrement=True)
    UsersId = db.Column(db.Integer, db.ForeignKey("Users.UsersId"), nullable=False, index=True)
    RadioName = db.Column(db.String(256), nullable=False)
    LinkURL = db.Column(db.String(2056), nullable=False)
    LinkImage = db.Column(db.String(2056), nullable=True)
    RadiosId = db.Column(db.Integer, db.ForeignKey("Radio.RadioId"), nullable=False, index=True)
    LastListen = db.Column(db.DateTime, nullable=True)
    CountListen = db.Column(db.Integer, nullable=False, default=0)
    RadioLike = db.Column(db.String(10), nullable=True)  # Like; Not Like
    # Shared = partagee par un autre utilisateur, pas encore acceptee (absente
    # de la liste tant qu'il n'a pas appuye sur "Ecouter" dans la
    # notification) ; New = partage d'une radio deja dans sa liste ; Read.
    RadioStatus = db.Column(db.String(10), nullable=True)
    # Utilisateur qui a partage cette radio (notification "X vous a partage
    # cette radio") ; NULL = radio ajoutee par l'utilisateur lui-meme.
    SharedBy = db.Column(db.Integer, db.ForeignKey("Users.UsersId"), nullable=True)
    CreatedAt = db.Column(db.DateTime, nullable=True, default=datetime.utcnow)  # tri des notifications

    user = db.relationship("Users", backref="radio_favorites", foreign_keys=[UsersId])
    radio = db.relationship("Radio")
    shared_by = db.relationship("Users", foreign_keys=[SharedBy])


class RadioComment(db.Model):
    """Commentaires d'une radio, visibles par tous les utilisateurs qui l'ont
    ajoutee (ecran Play). Plusieurs commentaires possibles par utilisateur ;
    Radio.RadioComment en tient le compte."""

    __tablename__ = "RadioComment"

    CommentId = db.Column(db.Integer, primary_key=True, autoincrement=True)
    RadioId = db.Column(db.Integer, db.ForeignKey("Radio.RadioId"), nullable=False, index=True)
    UsersId = db.Column(db.Integer, db.ForeignKey("Users.UsersId"), nullable=False, index=True)  # auteur
    CommentText = db.Column(db.String(2056), nullable=False)
    CreatedAt = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)

    user = db.relationship("Users")
    radio = db.relationship("Radio")


class MusicArtist(db.Model):
    """Catalogue partage des artistes (CDC §8) - un artiste = une ligne, quelle
    que soit la source de ses morceaux (MP3 local ou YouTube Music)."""

    __tablename__ = "MusicArtist"

    ArtistId = db.Column(db.Integer, primary_key=True, autoincrement=True)
    Name = db.Column(db.String(256), nullable=False)
    NameKey = db.Column(db.String(256), nullable=False, unique=True, index=True)  # nom normalise, cf. music_catalog
    PictureUrl = db.Column(db.String(2056), nullable=True)  # photo Deezer, cf. cover_service
    CheckedAt = db.Column(db.DateTime, nullable=True)  # derniere recherche de photo (NULL = jamais cherche)


class MusicAlbum(db.Model):
    """Catalogue partage des albums. ArtistId = artiste de l'album (tag ID3
    'albumartist'), 'Various Artists' pour une compilation - chaque morceau
    garde son propre interprete dans MusicTrack.ArtistId."""

    __tablename__ = "MusicAlbum"
    __table_args__ = (db.UniqueConstraint("ArtistId", "TitleKey"),)

    AlbumId = db.Column(db.Integer, primary_key=True, autoincrement=True)
    ArtistId = db.Column(db.Integer, db.ForeignKey("MusicArtist.ArtistId"), nullable=False, index=True)
    Title = db.Column(db.String(256), nullable=False)
    TitleKey = db.Column(db.String(256), nullable=False)
    IsCompilation = db.Column(db.Boolean, nullable=False, default=False)
    Year = db.Column(db.Integer, nullable=True)
    CoverUrl = db.Column(db.String(2056), nullable=True)  # pochette Deezer/iTunes, cf. cover_service
    CheckedAt = db.Column(db.DateTime, nullable=True)  # derniere recherche de pochette (NULL = jamais cherche)

    artist = db.relationship("MusicArtist")


class MusicTrack(db.Model):
    """Catalogue partage des morceaux (compteurs globaux, meme principe que
    Podcast/Radio). Source 'local' : SourceRef = chemin relatif du MP3 sous
    MP3_LIBRARY_PATH ; source 'youtube' : SourceRef = videoId YouTube Music."""

    __tablename__ = "MusicTrack"
    __table_args__ = (db.UniqueConstraint("Source", "SourceRef"),)

    TrackId = db.Column(db.Integer, primary_key=True, autoincrement=True)
    ArtistId = db.Column(db.Integer, db.ForeignKey("MusicArtist.ArtistId"), nullable=False, index=True)
    AlbumId = db.Column(db.Integer, db.ForeignKey("MusicAlbum.AlbumId"), nullable=True, index=True)
    Title = db.Column(db.String(256), nullable=False)
    TrackNumber = db.Column(db.Integer, nullable=True)
    Duration = db.Column(db.Integer, nullable=True)  # secondes
    Source = db.Column(db.String(10), nullable=False)  # local; youtube
    SourceRef = db.Column(db.String(2056), nullable=False)
    # MP3 disparu du disque : masque partout (cf. mp3_library_service.scan_library)
    # plutot que supprime, pour garder l'historique s'il revient ; purge
    # definitive par le job mensuel 'music_purge'.
    IsAvailable = db.Column(db.Boolean, nullable=False, default=True, index=True)
    MissingSince = db.Column(db.DateTime, nullable=True)  # date de disparition du MP3 (NULL = present)
    # MP3 local : date de modification / taille du fichier au dernier scan.
    # Si elles changent (tags edites), le scan relit les tags du fichier.
    FileMtime = db.Column(db.Float, nullable=True)
    FileSize = db.Column(db.Integer, nullable=True)  # octets
    # Morceau YouTube ajoute seulement via "Les plus populaires" d'un artiste :
    # son album n'apparait pas dans la bibliotheque (ecran artiste, tri Album,
    # recherche) tant que l'utilisateur ne l'a pas choisi autrement ("Ecouter",
    # import de l'album) - cf. app/library/routes.py.
    FromTop = db.Column(db.Boolean, nullable=False, default=False)
    MusicLike = db.Column(db.Integer, nullable=False, default=0)  # nombre de "J'aime"
    MusicNotLike = db.Column(db.Integer, nullable=False, default=0)  # nombre de "Je n'aime pas"
    MusicComment = db.Column(db.Integer, nullable=False, default=0)  # nombre de MusicTrackComment
    MusicCount = db.Column(db.Integer, nullable=False, default=0)  # nombre d'ecoutes, tous utilisateurs
    MusicGenre = db.Column(db.String(50), nullable=True)  # MusicGenre.MusicGenreName, choix unique
    AgeRange = db.Column(db.String(50), nullable=True)  # AgeRange.AgeRange, choix unique
    CreatedAt = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)

    artist = db.relationship("MusicArtist")
    album = db.relationship("MusicAlbum")


class MusicUsers(db.Model):
    """Morceaux lies a un utilisateur (ecoutes, like, commentaire, partage,
    nouveaute a notifier) - pas tout le catalogue : les ecrans Artiste/Album
    lisent directement MusicTrack (CDC §8)."""

    __tablename__ = "MusicUsers"
    __table_args__ = (db.UniqueConstraint("UsersId", "TrackId"),)

    MusicUsersId = db.Column(db.Integer, primary_key=True, autoincrement=True)
    UsersId = db.Column(db.Integer, db.ForeignKey("Users.UsersId"), nullable=False, index=True)
    TrackId = db.Column(db.Integer, db.ForeignKey("MusicTrack.TrackId"), nullable=False, index=True)
    CountListen = db.Column(db.Integer, nullable=False, default=0)
    MusicStatus = db.Column(db.String(10), nullable=True)  # New; Unread; Read
    MusicLike = db.Column(db.String(10), nullable=True)  # Like; Not Like
    # Utilisateur qui a partage ce morceau (notification "X vous a partage ce
    # morceau") ; NULL = nouveaute du scan MP3 ou ajout par l'utilisateur.
    SharedBy = db.Column(db.Integer, db.ForeignKey("Users.UsersId"), nullable=True)
    # Tri des nouveautes par anciennete sur l'ecran Notifications (CDC §8).
    CreatedAt = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)

    user = db.relationship("Users", backref="music_library", foreign_keys=[UsersId])
    track = db.relationship("MusicTrack")
    shared_by = db.relationship("Users", foreign_keys=[SharedBy])


class MusicTrackComment(db.Model):
    """Commentaires d'un morceau, visibles par tous les utilisateurs (ecran
    Play). Plusieurs commentaires possibles par utilisateur ;
    MusicTrack.MusicComment en tient le compte."""

    __tablename__ = "MusicTrackComment"

    CommentId = db.Column(db.Integer, primary_key=True, autoincrement=True)
    TrackId = db.Column(db.Integer, db.ForeignKey("MusicTrack.TrackId"), nullable=False, index=True)
    UsersId = db.Column(db.Integer, db.ForeignKey("Users.UsersId"), nullable=False, index=True)  # auteur
    CommentText = db.Column(db.String(2056), nullable=False)
    CreatedAt = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)

    user = db.relationship("Users")
    track = db.relationship("MusicTrack")


class Play(db.Model):
    """Etat de lecture en cours par utilisateur, pour reprise cross-appareil."""

    __tablename__ = "Play"

    PlayId = db.Column(db.Integer, primary_key=True, autoincrement=True)
    UsersId = db.Column(db.Integer, db.ForeignKey("Users.UsersId"), nullable=False, unique=True, index=True)
    PlayType = db.Column(db.String(10), nullable=True)  # Podcast; Radio; Music
    LinkURL = db.Column(db.String(2056), nullable=True)
    PlayStatus = db.Column(db.String(10), nullable=False, default="Stop")  # Play; Pause; Stop
    # Ajouts CDC §2 ("l'etat de lecture (position, contenu en cours, type) reste
    # sauvegarde") : le schema initial ne portait pas de
    # quoi retrouver l'item precis ni la position - nécessaires pour une reprise
    # reelle et pour les actions Like/Comment sur l'ecran Play.
    ItemId = db.Column(db.Integer, nullable=True)  # EpisodesUsersId / RadioUsersId / TrackId selon PlayType
    PositionSeconds = db.Column(db.Float, nullable=False, default=0.0)
    # Ajout fonctionnalite Cast : NULL = lecture locale (navigateur), sinon
    # diffusion en cours sur ce device (cf. app/cast_service.py). PositionSeconds
    # est alors alimente par la position remontee par le device plutot que par
    # <audio>.currentTime cote navigateur.
    CastDeviceId = db.Column(db.Integer, db.ForeignKey("CastDevice.CastDeviceId"), nullable=True, index=True)
    # File de lecture musique explicite (TrackIds, ex. "Les plus populaires"),
    # cf. play_service.set_music_queue. En base plutot que dans le cookie de
    # session : chaque reponse renvoie ce cookie (REMEMBER_COOKIE_REFRESH_EACH_REQUEST),
    # une requete longue lancee avant (prechargement du suivant) effacait la file.
    MusicQueue = db.Column(db.JSON, nullable=True)

    user = db.relationship("Users", backref=db.backref("play_state", uselist=False))
    cast_device = db.relationship("CastDevice")


class CastDevice(db.Model):
    """Enceinte/renderer decouvert sur le reseau local pour la fonctionnalite
    Cast (CDC §7/§8/§10) : Bose SoundTouch (Web API confort + DLNA pour le
    contenu) et renderers DLNA generiques (DLNA uniquement). Peuple et
    rafraichi par le job planifie cast_scan (app/scheduler.py), toutes les
    5 minutes par defaut."""

    __tablename__ = "CastDevice"

    CastDeviceId = db.Column(db.Integer, primary_key=True, autoincrement=True)
    DeviceType = db.Column(db.String(20), nullable=False)  # Soundtouch; Dlna
    Udn = db.Column(db.String(255), unique=True, nullable=False, index=True)
    IpAddress = db.Column(db.String(45), nullable=False)
    Port = db.Column(db.Integer, nullable=True)
    DescriptionUrl = db.Column(db.String(512), nullable=True)  # URL de description UPnP (SetAVTransportURI control point)
    FriendlyName = db.Column(db.String(255), nullable=False)  # nom renvoye par le device
    Label = db.Column(db.String(255), nullable=True)  # nom editable par l'utilisateur (ecran Cast), pre-rempli avec FriendlyName
    LastSeenAt = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)  # "en ligne" derive de ce champ, pas de colonne dediee


class Log(db.Model):
    """Journal d'activite : un enregistrement par affichage d'ecran."""

    __tablename__ = "Log"

    LogId = db.Column(db.Integer, primary_key=True, autoincrement=True)
    UsersId = db.Column(db.Integer, db.ForeignKey("Users.UsersId"), nullable=False, index=True)
    Frame = db.Column(db.String(50), nullable=False)
    LogDate = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    Device = db.Column(db.String(50), nullable=True)
    IPaddress = db.Column(db.String(50), nullable=True)
    OS = db.Column(db.String(50), nullable=True)


class LoginHistory(db.Model):
    """Journal des tentatives de connexion (reussies, refusees, comptes
    verrouilles) et des reprises de session via le cookie "remember me" -
    permet de detecter une activite suspecte (bruteforce, connexion depuis un
    appareil/IP inhabituel...). Distinct de Log, qui ne trace que les ecrans
    vus par un utilisateur deja authentifie."""

    __tablename__ = "LoginHistory"

    LoginHistoryId = db.Column(db.Integer, primary_key=True, autoincrement=True)
    UsersId = db.Column(db.Integer, db.ForeignKey("Users.UsersId"), nullable=True, index=True)  # null si l'email ne correspond a aucun compte
    Email = db.Column(db.String(320), nullable=False)
    EventType = db.Column(db.String(20), nullable=False)  # success; invalid_user; invalid_password; user_locked; ip_locked; cookie_resume; logout; admin_success; admin_invalid; admin_locked; account_request; reset_request; request_blocked
    EventDate = db.Column(db.DateTime, nullable=False, default=datetime.utcnow, index=True)
    IpAddress = db.Column(db.String(45), nullable=True)
    Device = db.Column(db.String(50), nullable=True)
    OS = db.Column(db.String(50), nullable=True)
