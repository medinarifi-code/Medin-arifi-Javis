"""Postfach über IMAP/SMTP (Gmail, GMX, Web.de, Yahoo, iCloud …) mit App-Passwort."""

from __future__ import annotations

import email
import imaplib
import logging
import re
import smtplib
from email.header import decode_header, make_header
from email.message import EmailMessage
from email.policy import default as default_policy
from email.utils import formataddr, getaddresses, make_msgid, parseaddr, parsedate_to_datetime

from xml.etree import ElementTree

import httpx

from .common import MailError, html_to_text, short_date

log = logging.getLogger("jarvis.mail")

# Domain -> (IMAP-Server[:Port], SMTP-Server[:Port]); IMAP 993 = SSL; SMTP 465 = SSL, 587 = STARTTLS
_APPLE = ("imap.mail.me.com", "smtp.mail.me.com:587")
_GMX = ("imap.gmx.net", "mail.gmx.net:465")
_YAHOO = ("imap.mail.yahoo.com", "smtp.mail.yahoo.com:465")
KNOWN_HOSTS = {
    "gmail.com": ("imap.gmail.com", "smtp.gmail.com:465"),
    "googlemail.com": ("imap.gmail.com", "smtp.gmail.com:465"),
    "gmx.de": _GMX, "gmx.net": _GMX, "gmx.at": _GMX, "gmx.ch": _GMX,
    "gmx.com": ("imap.gmx.com", "mail.gmx.com:465"),
    "mail.com": ("imap.mail.com", "smtp.mail.com:465"),
    "web.de": ("imap.web.de", "smtp.web.de:465"),
    "yahoo.com": _YAHOO, "yahoo.de": _YAHOO, "yahoo.at": _YAHOO, "yahoo.ch": _YAHOO,
    "yahoo.co.uk": _YAHOO, "yahoo.fr": _YAHOO, "ymail.com": _YAHOO,
    "icloud.com": _APPLE, "me.com": _APPLE, "mac.com": _APPLE,
    "t-online.de": ("secureimap.t-online.de", "securesmtp.t-online.de:465"),
    "aol.com": ("imap.aol.com", "smtp.aol.com:465"), "aol.de": ("imap.aol.com", "smtp.aol.com:465"),
    "freenet.de": ("mx.freenet.de", "mx.freenet.de:465"),
    "posteo.de": ("posteo.de", "posteo.de:465"), "posteo.net": ("posteo.de", "posteo.de:465"),
    "mailbox.org": ("imap.mailbox.org", "smtp.mailbox.org:465"),
    "bluewin.ch": ("imaps.bluewin.ch", "smtpauths.bluewin.ch:465"),
}
APPLE_DOMAINS = {"icloud.com", "me.com", "mac.com"}
MICROSOFT_PREFIXES = ("outlook.", "hotmail.", "live.", "msn.", "windowslive.")

_INVISIBLE = re.compile("[\\s\u00a0\u00ad\u200b-\u200f\u2028-\u202f\u2060\ufeff]")
_ADDRESS = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
_discovered: dict[str, tuple[str, str]] = {}

MAX_BODY = 6000


def clean_address(raw: str | None) -> str:
    """Säubert MAIL_ADDRESS (Leerzeichen, unsichtbare Zeichen, Anführungszeichen, mailto: …)."""
    text = _INVISIBLE.sub("", raw or "").replace("mailto:", "")
    match = _ADDRESS.search(text)
    if not match:
        shown = (raw or "").strip()[:40]
        raise MailError("MAIL_ADDRESS muss die ganze E-Mail-Adresse sein, z. B. name@icloud.com "
                        f"(eingetragen ist: '{shown}'). Bitte bei Render korrigieren.")
    local, domain = match.group(0).rsplit("@", 1)
    return f"{local}@{domain.lower()}"


def discover(domain: str, http: httpx.Client | None = None) -> tuple[str, str]:
    """Findet IMAP/SMTP-Server einer unbekannten Domain (Mozilla-Anbieterdatenbank, sonst Standardnamen)."""
    if domain in _discovered:
        return _discovered[domain]
    result = (f"imap.{domain}", f"smtp.{domain}:465")
    try:
        client = http or httpx.Client(timeout=8)
        r = client.get(f"https://autoconfig.thunderbird.net/v1.1/{domain}")
        if r.status_code == 200:
            root = ElementTree.fromstring(r.content)
            imap = next((s for s in root.iter("incomingServer") if s.get("type") == "imap"
                         and (s.findtext("socketType") or "").upper() == "SSL"), None)
            smtp = next((s for s in root.iter("outgoingServer") if s.get("type") == "smtp"), None)
            if imap is not None and smtp is not None:
                fill = lambda h: (h or "").replace("%EMAILDOMAIN%", domain)  # noqa: E731
                imap_host = f"{fill(imap.findtext('hostname'))}:{imap.findtext('port') or 993}"
                smtp_host = f"{fill(smtp.findtext('hostname'))}:{smtp.findtext('port') or 465}"
                result = (imap_host, smtp_host)
    except Exception as e:  # noqa: BLE001 - dann eben Standardnamen
        log.info("Server-Erkennung für %s fehlgeschlagen: %s", domain, e)
    _discovered[domain] = result
    return result


def _decode(value: str | None) -> str:
    if not value:
        return ""
    try:
        return str(make_header(decode_header(value)))
    except Exception:  # noqa: BLE001
        return value


def _split(hostport: str, default: int) -> tuple[str, int]:
    host, _, port = hostport.partition(":")
    return host, int(port or default)


class ImapMailbox:
    provider = "imap"

    def __init__(self, address: str, password: str, imap_host: str | None = None, smtp_host: str | None = None):
        self.address = clean_address(address)
        self.password = password
        self.domain = self.address.rsplit("@", 1)[1]
        if not (imap_host and smtp_host):
            if self.domain.startswith(MICROSOFT_PREFIXES):
                raise MailError("Microsoft (Outlook/Hotmail/Live) erlaubt keine Anmeldung mit Passwort mehr. "
                                "Bitte MAIL_ADDRESS/MAIL_APP_PASSWORD leer lassen und stattdessen in JARVIS "
                                "✉️ → „Outlook verbinden“ nutzen (dafür MS_CLIENT_ID eintragen, siehe Anleitung).")
            known = KNOWN_HOSTS.get(self.domain) or discover(self.domain)
            imap_host, smtp_host = imap_host or known[0], smtp_host or known[1]
        self.imap_host, self.imap_port = _split(imap_host, 993)
        self.smtp_host, self.smtp_port = _split(smtp_host, 465)
        self.apple = self.domain in APPLE_DOMAINS
        self._user: str | None = None  # Benutzername, mit dem die Anmeldung geklappt hat

    def _usernames(self) -> list[str]:
        if self._user:
            return [self._user]
        names = [self.address]
        if self.apple:  # iCloud akzeptiert oft nur den Teil vor dem @
            names.append(self.address.split("@", 1)[0])
        return names

    def _login_error(self, detail: object) -> MailError:
        if self.apple:
            return MailError("iCloud lehnt die Anmeldung ab. Du brauchst ein app-spezifisches Passwort: "
                             "appleid.apple.com → Anmelden & Sicherheit → App-spezifische Passwörter → „JARVIS“. "
                             f"Dieses bei Render als MAIL_APP_PASSWORD eintragen. (Details: {detail})")
        return MailError("Anmeldung am Postfach fehlgeschlagen. Stimmt das App-Passwort (MAIL_APP_PASSWORD)? "
                         f"Details: {detail}")

    # ------------------------------------------------------------------ IMAP
    def _imap(self) -> imaplib.IMAP4_SSL:
        last: object = None
        for user in self._usernames():
            try:
                conn = imaplib.IMAP4_SSL(self.imap_host, self.imap_port, timeout=20)
            except OSError as e:
                raise MailError(f"Mailserver {self.imap_host} nicht erreichbar ({e}). Stimmt die Adresse "
                                f"{self.address}? Sonst MAIL_IMAP_HOST setzen.") from e
            try:
                conn.login(user, self.password)
                self._user = user
                return conn
            except imaplib.IMAP4.error as e:
                last = e
                _logout(conn)
        raise self._login_error(last)

    def test(self) -> str:
        conn = self._imap()
        try:
            conn.select("INBOX", readonly=True)
        finally:
            _logout(conn)
        return f"Verbunden – Postfach {self.address} ist erreichbar."

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
        last: object = None
        for user in self._usernames():
            try:
                if self.smtp_port == 587:
                    with smtplib.SMTP(self.smtp_host, 587, timeout=20) as s:
                        s.starttls()
                        s.login(user, self.password)
                        s.send_message(msg)
                else:
                    with smtplib.SMTP_SSL(self.smtp_host, self.smtp_port, timeout=20) as s:
                        s.login(user, self.password)
                        s.send_message(msg)
                self._user = user
                return
            except smtplib.SMTPAuthenticationError as e:
                last = e
            except (smtplib.SMTPException, OSError) as e:
                raise MailError(f"Senden fehlgeschlagen: {e}") from e
        raise self._login_error(last)

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
