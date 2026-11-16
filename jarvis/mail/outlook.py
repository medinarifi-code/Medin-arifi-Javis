"""Outlook/Hotmail über Microsoft Graph.

Anmeldung per Gerätecode: JARVIS zeigt einen Code, der Nutzer gibt ihn auf
microsoft.com/devicelogin ein und meldet sich dort an. JARVIS sieht das Passwort nie.
Der Token-Cache wird verschlüsselt im Browser gespeichert, damit die Verbindung
Neustarts des Servers übersteht.
"""

from __future__ import annotations

import base64
import hashlib
import logging
import threading
from datetime import datetime

import httpx
import msal
from cryptography.fernet import Fernet, InvalidToken

from .common import MailError, html_to_text, short_date

log = logging.getLogger("jarvis.outlook")

GRAPH = "https://graph.microsoft.com/v1.0"
SCOPES = ["Mail.Read", "Mail.Send", "User.Read"]
AUTHORITY = "https://login.microsoftonline.com/common"
MAX_BODY = 6000
_SELECT = "id,subject,from,receivedDateTime,isRead,bodyPreview"


# --------------------------------------------------------------------------- Verschlüsselung
def _fernet(secret: str, client_id: str) -> Fernet:
    digest = hashlib.sha256(f"{secret}|{client_id}|jarvis-outlook".encode()).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def encrypt_cache(data: str, secret: str, client_id: str) -> str:
    return _fernet(secret, client_id).encrypt(data.encode()).decode()


def decrypt_cache(token: str, secret: str, client_id: str) -> str | None:
    try:
        return _fernet(secret, client_id).decrypt(token.encode()).decode()
    except (InvalidToken, ValueError):
        return None


# --------------------------------------------------------------------------- Anmeldung
class OutlookAuth:
    """Anmeldestatus einer Browser-Sitzung."""

    def __init__(self, client_id: str, secret: str):
        self.client_id = client_id
        self.secret = secret
        self.cache = msal.SerializableTokenCache()
        self.app = msal.PublicClientApplication(client_id, authority=AUTHORITY, token_cache=self.cache)
        self.flow: dict | None = None
        self.error: str | None = None
        self._lock = threading.Lock()

    # Cache <-> Browser
    def restore(self, token: str | None) -> bool:
        if not token or self.connected:
            return self.connected
        data = decrypt_cache(token, self.secret, self.client_id)
        if data:
            self.cache.deserialize(data)
        return self.connected

    def export(self) -> str:
        return encrypt_cache(self.cache.serialize(), self.secret, self.client_id)

    def changed(self) -> bool:
        if self.cache.has_state_changed:
            self.cache.has_state_changed = False
            return True
        return False

    @property
    def account(self) -> dict | None:
        accounts = self.app.get_accounts()
        return accounts[0] if accounts else None

    @property
    def connected(self) -> bool:
        return self.account is not None

    @property
    def address(self) -> str | None:
        acc = self.account
        return acc.get("username") if acc else None

    def start(self) -> dict:
        """Startet die Gerätecode-Anmeldung und wartet im Hintergrund auf den Nutzer."""
        with self._lock:
            flow = self.app.initiate_device_flow(scopes=SCOPES)
            if "user_code" not in flow:
                raise MailError(f"Microsoft-Anmeldung konnte nicht gestartet werden: "
                                f"{flow.get('error_description') or flow.get('error')}")
            self.flow, self.error = flow, None
        threading.Thread(target=self._wait, args=(flow,), daemon=True).start()
        return {"user_code": flow["user_code"], "verification_uri": flow["verification_uri"],
                "expires_in": flow.get("expires_in", 900)}

    def _wait(self, flow: dict) -> None:
        result = self.app.acquire_token_by_device_flow(flow)  # blockiert bis Anmeldung/Ablauf
        with self._lock:
            if self.flow is flow:
                self.flow = None
                if "access_token" not in result:
                    self.error = result.get("error_description") or result.get("error") or "Anmeldung abgebrochen"
                    log.warning("Outlook-Anmeldung fehlgeschlagen: %s", self.error)

    def pending(self) -> dict | None:
        f = self.flow
        return {"user_code": f["user_code"], "verification_uri": f["verification_uri"]} if f else None

    def disconnect(self) -> None:
        for acc in self.app.get_accounts():
            self.app.remove_account(acc)
        self.flow = None

    def access_token(self) -> str:
        acc = self.account
        if acc is None:
            raise MailError("Outlook ist nicht verbunden. Bitte oben auf das Brief-Symbol tippen und anmelden.")
        result = self.app.acquire_token_silent(SCOPES, account=acc)
        if not result or "access_token" not in result:
            raise MailError("Die Outlook-Anmeldung ist abgelaufen. Bitte über das Brief-Symbol neu verbinden.")
        return result["access_token"]


# --------------------------------------------------------------------------- Postfach
class OutlookMailbox:
    provider = "outlook"

    def __init__(self, auth: OutlookAuth, http: httpx.Client | None = None):
        self.auth = auth
        self.http = http or httpx.Client(timeout=20)

    @property
    def address(self) -> str:
        return self.auth.address or ""

    def _call(self, method: str, path: str, **kwargs) -> dict:
        headers = {"Authorization": f"Bearer {self.auth.access_token()}",
                   "Prefer": 'outlook.body-content-type="text"', **kwargs.pop("headers", {})}
        try:
            r = self.http.request(method, GRAPH + path, headers=headers, **kwargs)
        except httpx.HTTPError as e:
            raise MailError(f"Outlook nicht erreichbar: {e}") from e
        if r.status_code >= 400:
            try:
                detail = r.json().get("error", {}).get("message", "")
            except ValueError:
                detail = r.text[:200]
            if r.status_code in (401, 403):
                raise MailError(f"Outlook verweigert den Zugriff ({detail}). Bitte über das Brief-Symbol neu verbinden.")
            raise MailError(f"Outlook-Fehler {r.status_code}: {detail}")
        return r.json() if r.content else {}

    @staticmethod
    def _summary(m: dict) -> dict:
        sender = (m.get("from") or {}).get("emailAddress") or {}
        return {
            "id": m["id"],
            "from": sender.get("name") or sender.get("address", ""),
            "from_address": sender.get("address", ""),
            "subject": m.get("subject") or "(kein Betreff)",
            "date": short_date(_parse(m.get("receivedDateTime"))),
            "unread": not m.get("isRead", True),
            "preview": (m.get("bodyPreview") or "")[:200],
        }

    def list(self, unread_only: bool = False, limit: int = 10) -> list[dict]:
        params = {"$top": str(limit), "$select": _SELECT, "$orderby": "receivedDateTime desc"}
        if unread_only:
            params["$filter"] = "isRead eq false"
        data = self._call("GET", "/me/mailFolders/inbox/messages", params=params)
        return [self._summary(m) for m in data.get("value", [])]

    def search(self, query: str, limit: int = 10) -> list[dict]:
        safe = query.replace('"', " ")
        data = self._call("GET", "/me/messages", params={"$search": f'"{safe}"', "$top": str(limit), "$select": _SELECT})
        return [self._summary(m) for m in data.get("value", [])]

    def read(self, msg_id: str) -> dict:
        m = self._call("GET", f"/me/messages/{_safe_id(msg_id)}",
                       params={"$select": "id,subject,from,toRecipients,receivedDateTime,body,isRead"})
        body = (m.get("body") or {}).get("content", "")
        if (m.get("body") or {}).get("contentType") == "html":
            body = html_to_text(body)
        info = self._summary(m)
        info.pop("preview", None)
        info["to"] = ", ".join(r["emailAddress"]["address"] for r in m.get("toRecipients", []))
        info["body"] = body.strip()[:MAX_BODY]
        return info

    def reply(self, msg_id: str, text: str) -> str:
        info = self.read(msg_id)
        self._call("POST", f"/me/messages/{_safe_id(msg_id)}/reply", json={"comment": text})
        return info["from_address"]

    def send(self, to: list[str], subject: str, text: str) -> None:
        self._call("POST", "/me/sendMail", json={
            "message": {
                "subject": subject,
                "body": {"contentType": "Text", "content": text},
                "toRecipients": [{"emailAddress": {"address": a}} for a in to],
            },
            "saveToSentItems": True,
        })


def _safe_id(msg_id: str) -> str:
    if not msg_id or any(c in msg_id for c in "/?#&"):
        raise MailError("Ungültige Mail-ID.")
    return msg_id


def _parse(value: str | None):
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
