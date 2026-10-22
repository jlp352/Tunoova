from urllib.parse import urlparse

from flask import request


def safe_local_url(value: str | None, fallback: str) -> str:
    """N'autorise que des chemins locaux (pas de redirection ouverte) : accepte
    un chemin relatif ou une URL complete pointant vers cet hote (Referer).
    Retourne `fallback` sinon, ou si `value` designe la page courante."""
    if value:
        parsed = urlparse(value)
        if not parsed.netloc or parsed.netloc == request.host:
            path = parsed.path or "/"
            if path.startswith("/") and not path.startswith("//") and path != request.path:
                return path + (f"?{parsed.query}" if parsed.query else "")
    return fallback
