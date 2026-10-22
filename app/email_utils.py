import os
import smtplib
from email.message import EmailMessage


def send_email(to_addr: str, subject: str, body: str) -> bool:
    """Envoie un email via SMTP si configure (.env), sinon journalise le
    contenu sur la sortie standard (utile en developpement tant que le SMTP
    n'est pas branche)."""
    host = os.environ.get("SMTP_HOST")
    if not host:
        print(f"[email:not-sent] to={to_addr} subject={subject!r}\n{body}")
        return False

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = os.environ.get("SMTP_FROM", "tunoova@example.com")
    msg["To"] = to_addr
    msg.set_content(body)

    port = int(os.environ.get("SMTP_PORT", "587"))
    user = os.environ.get("SMTP_USER")
    password = os.environ.get("SMTP_PASSWORD")

    try:
        with smtplib.SMTP(host, port) as server:
            server.starttls()
            if user:
                server.login(user, password or "")
            server.send_message(msg)
        return True
    except (smtplib.SMTPException, OSError) as exc:
        # Un envoi SMTP en echec ne doit jamais faire planter la requete
        # utilisateur (ex: notification admin) - on journalise et on continue.
        print(f"[email:error] to={to_addr} subject={subject!r} error={exc}")
        return False


def notify_admin(subject: str, body: str) -> None:
    admin_email = os.environ.get("ADMIN_EMAIL", "")
    if admin_email:
        send_email(admin_email, subject, body)
