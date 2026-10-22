from datetime import datetime


def short_time_ago(dt):
    """Badge court type '1d'/'1w' pour l'ecran Home (maquette page 12)."""
    if not dt:
        return None
    seconds = max((datetime.utcnow() - dt).total_seconds(), 0)
    days = seconds / 86400
    if days < 1:
        return "now"
    if days < 7:
        return f"{int(days)}d"
    weeks = days / 7
    if weeks < 5:
        return f"{int(weeks)}w"
    months = days / 30
    if months < 12:
        return f"{int(months)}mo"
    return f"{int(days / 365)}y"
