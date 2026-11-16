"""Postfach über IMAP/SMTP (Gmail, GMX, Web.de, Yahoo, iCloud …) mit App-Passwort."""

from __future__ import annotations

import email
import imaplib
import re
import smtplib
from email.header import decode_header, make_header
from email.message import EmailMessage
from email.policy import default as default_policy
from email.utils import formataddr, getaddresses, make_msgid, parseaddr, parsedate_to_datetime

from .common import MailError, html_to_text, short_date

# Domain -> (IMAP-Server, SMTP-Server[:Port]); 465 = SSL, 587 = STARTTLS
KNOWN_HOSTS = {
    "gmail.com": ("imap.gmail.com", "smtp.gmail.com:465"),
    "googlemail.com": ("imap.gmail.com", "smtp.gmail.com:465"),
    "gmx.de": ("imap.gmx.net", "mail.gmx.net:465"),
    "gmx.net": ("imap.gmx.net", "mail.gmx.net:465"),
    "gmx.at": ("imap.gmx.net", "mail.gmx.net:465"),
    "gmx.ch": ("imap.gmx.net", "mail.gmx.net:465"),
    "web.de": ("imap.web.de", "smtp.web.de:465"),
    "yahoo.com": ("imap.mail.yahoo.com", "smtp.mail.yahoo.com:465"),
    "yahoo.de": ("imap.mail.yahoo.com", "smtp.mail.yahoo.com:465"),
    "icloud.com": ("imap.mail.me.com", "smtp.mail.me.com:587"),
    "me.com": ("imap.mail.me.com", "smtp.mail.me.com:587"),
    "t-online.de": ("secureimap.t-online.de", "securesmtp.t-online.de:465"),
}

MAX_BODY = 6000


def _decode(value: str | None) -> str:
    if not value:
        return ""
    try:
        return str(make_header(decode_header(value)))
    except Exception:  # noqa: BLE001
        return value


class ImapMailbox:
    provider = "imap"

    def __init__(self, address: str, password: str, imap_host: str | None = None, smtp_host: str | None = None):
        self.address = address
        self.password = password
        domain = address.rsplit("@", 1)[-1].lower()
        known = KNOWN_HOSTS.get(domain)
        if not (imap_host and smtp_host) and not known:
            raise MailError(f"Für {domain} kenne ich die Server nicht. Bitte MAIL_IMAP_HOST und MAIL_SMTP_HOST setzen.")
        self.imap_host = imap_host or known[0]
        smtp = smtp_host or known[1]
        host, _, port = smtp.partition(":")
        self.smtp_host, self.smtp_port = host, int(port or 465)

    # ------------------------------------------------------------------ IMAP
    def _imap(self) -> imaplib.IMAP4_SSL:
        try:
            conn = imaplib.IMAP4_SSL(self.imap_host, timeout=20)
            conn.login(self.address, self.password)
        except imaplib.IMAP4.error as e:
            raise MailError("Anmeldung am Postfach fehlgeschlagen. Stimmt das App-Passwort (MAIL_APP_PASSWORD)? "
                            f"Details: {e}") from e
        except OSError as e:
            raise MailError(f"Mailserver {self.imap_host} nicht erreichbar: {e}") from e
        return conn

    def _summaries(self, conn: imaplib.IMAP4_SSL, uids: list[bytes], limit: int) -> list[dict]:
        uids = uids[-limit:][::-1]  # neueste zuerst
        if not uids:
            return []
        typ, data = conn.uid("FETCH", b",".join(uids),
                             "(FLAGS BODY.PEEK[HEADER.FIELDS (FROM SUBJECT DATE)])")
        if typ != "OK":
            raise MailError("Mails konnten nicht geladen werden.")
        result = {}
        for item in data:
            if not isinstance(item, tuple):
                continue
            meta, header = item
            uid = re.search(rb"UID (\d+)", meta)
            if not uid:
                continue
            msg = email.message_from_bytes(header, policy=default_policy)
            name, addr = parseaddr(_decode(msg.get("From")))
            result[uid.group(1)] = {
                "id": uid.group(1).decode(),
                "from": name or addr,
                "from_address": addr,
                "subject": _decode(msg.get("Subject")) or "(kein Betreff)",
                "date": short_date(_date(msg.get("Date"))),
                "unread": b"\\Seen" not in meta,
            }
        return [result[u] for u in uids if u in result]

    def list(self, unread_only: bool = False, limit: int = 10) -> list[dict]:
        conn = self._imap()
        try:
            conn.select("INBOX", readonly=True)
            typ, data = conn.uid("SEARCH", None, "UNSEEN" if unread_only else "ALL")
            return self._summaries(conn, data[0].split() if typ == "OK" else [], limit)
        finally:
            _logout(conn)

    def search(self, query: str, limit: int = 10) -> list[dict]:
        conn = self._imap()
        try:
            conn.select("INBOX", readonly=True)
            conn.literal = query.encode("utf-8")
            typ, data = conn.uid("SEARCH", "CHARSET", "UTF-8", "TEXT")
            return self._summaries(conn, data[0].split() if typ == "OK" and data and data[0] else [], limit)
        finally:
            _logout(conn)

    def _fetch(self, msg_id: str) -> EmailMessage:
        if not str(msg_id).isdigit():
            raise MailError("Ungültige Mail-ID.")
        conn = self._imap()
        try:
            conn.select("INBOX")
            typ, data = conn.uid("FETCH", str(msg_id).encode(), "(RFC822)")  # markiert die Mail als gelesen
            raw = next((d[1] for d in data or [] if isinstance(d, tuple)), None)
            if typ != "OK" or raw is None:
                raise MailError("Diese Mail wurde nicht gefunden.")
            return email.message_from_bytes(raw, policy=default_policy)
        finally:
            _logout(conn)

    def read(self, msg_id: str) -> dict:
        msg = self._fetch(msg_id)
        name, addr = parseaddr(_decode(msg.get("From")))
        body_part = msg.get_body(preferencelist=("plain", "html"))
        body = ""
        if body_part is not None:
            body = body_part.get_content()
            if body_part.get_content_type() == "text/html":
                body = html_to_text(body)
        return {
            "id": str(msg_id),
            "from": name or addr,
            "from_address": addr,
            "to": ", ".join(a for _, a in getaddresses([_decode(msg.get("To"))])),
            "subject": _decode(msg.get("Subject")) or "(kein Betreff)",
            "date": short_date(_date(msg.get("Date"))),
            "body": body.strip()[:MAX_BODY],
        }

    # ------------------------------------------------------------------ SMTP
    def _send(self, msg: EmailMessage) -> None:
        try:
            if self.smtp_port == 587:
                with smtplib.SMTP(self.smtp_host, 587, timeout=20) as s:
                    s.starttls()
                    s.login(self.address, self.password)
                    s.send_message(msg)
            else:
                with smtplib.SMTP_SSL(self.smtp_host, self.smtp_port, timeout=20) as s:
                    s.login(self.address, self.password)
                    s.send_message(msg)
        except smtplib.SMTPAuthenticationError as e:
            raise MailError("Senden fehlgeschlagen: Anmeldung abgelehnt. Bitte App-Passwort prüfen.") from e
        except (smtplib.SMTPException, OSError) as e:
            raise MailError(f"Senden fehlgeschlagen: {e}") from e

    def send(self, to: list[str], subject: str, text: str) -> None:
        msg = EmailMessage()
        msg["From"] = self.address
        msg["To"] = ", ".join(to)
        msg["Subject"] = subject
        msg["Message-ID"] = make_msgid()
        msg.set_content(text)
        self._send(msg)

    def reply(self, msg_id: str, text: str) -> str:
        original = self._fetch(msg_id)
        reply_to = original.get("Reply-To") or original.get("From")
        name, addr = parseaddr(_decode(reply_to))
        if not addr:
            raise MailError("Die Mail hat keine Absenderadresse.")
        subject = _decode(original.get("Subject")) or ""
        if not subject.lower().startswith(("re:", "aw:")):
            subject = "Re: " + subject
        msg = EmailMessage()
        msg["From"] = self.address
        msg["To"] = formataddr((name, addr))
        msg["Subject"] = subject
        msg["Message-ID"] = make_msgid()
        if original.get("Message-ID"):
            msg["In-Reply-To"] = original["Message-ID"]
            msg["References"] = f"{original.get('References', '')} {original['Message-ID']}".strip()
        msg.set_content(text)
        self._send(msg)
        return addr


def _date(value):
    try:
        return parsedate_to_datetime(value) if value else None
    except (TypeError, ValueError):
        return None


def _logout(conn) -> None:
    try:
        conn.logout()
    except Exception:  # noqa: BLE001
        pass
