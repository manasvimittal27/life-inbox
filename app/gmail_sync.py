"""Inbound Gmail sync (read-only).

Connects the user's Gmail with a read-only OAuth scope, pulls recent inbox
messages, and turns each one into daily actionables using the SAME parser the
rest of the app uses (``analyze_single_note``). Re-syncing is idempotent: each
email is stored as an inbox item keyed by its Gmail message id, so a message is
never turned into tasks twice.

Nothing here sends, modifies, or deletes email — the only scope requested is
``gmail.readonly``.

Google client libraries are imported lazily inside the functions that need the
live API, so unit tests can inject a fake service object without any Google
dependency or network call.
"""

import base64
import json
import logging
import os
from pathlib import Path
from typing import Optional

from app import database
from app.organizer import analyze_single_note


logger = logging.getLogger(__name__)

# Read-only: list and read messages, never send or modify.
SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]

_DATA_DIR = Path(__file__).resolve().parent.parent / "data"

# Where the user's OAuth token is cached (gitignored).
TOKEN_PATH = Path(
    os.getenv("GMAIL_TOKEN_FILE", str(_DATA_DIR / "gmail_token.json"))
)
# The OAuth client secret downloaded from Google Cloud (gitignored).
CLIENT_SECRET_PATH = Path(
    os.getenv(
        "GOOGLE_CLIENT_SECRET_FILE",
        str(_DATA_DIR / "gmail_client_secret.json"),
    )
)

# How many recent inbox messages a single sync pulls by default.
DEFAULT_MAX_RESULTS = 10


class GmailNotConfigured(Exception):
    """Raised when the OAuth client secret is missing."""


class GmailNotConnected(Exception):
    """Raised when there is no usable stored token for the user."""


# --- OAuth ------------------------------------------------------------------


def client_secret_configured() -> bool:
    return CLIENT_SECRET_PATH.exists()


def _build_flow(redirect_uri: str):
    """Build an OAuth flow from the downloaded client-secret file."""
    if not client_secret_configured():
        raise GmailNotConfigured(
            "Gmail client secret is not configured. Add the OAuth client "
            f"secret JSON at {CLIENT_SECRET_PATH}."
        )
    from google_auth_oauthlib.flow import Flow

    flow = Flow.from_client_secrets_file(
        str(CLIENT_SECRET_PATH),
        scopes=SCOPES,
        redirect_uri=redirect_uri,
    )
    return flow


def build_authorization_url(redirect_uri: str) -> str:
    """Return the Google consent-screen URL to send the user to."""
    flow = _build_flow(redirect_uri)
    auth_url, _state = flow.authorization_url(
        access_type="offline",
        include_granted_scopes="true",
        prompt="consent",
    )
    return auth_url


def exchange_code(redirect_uri: str, code: str) -> None:
    """Exchange an OAuth callback code for a token and cache it on disk."""
    flow = _build_flow(redirect_uri)
    flow.fetch_token(code=code)
    _save_credentials(flow.credentials)


def _save_credentials(creds) -> None:
    TOKEN_PATH.parent.mkdir(parents=True, exist_ok=True)
    TOKEN_PATH.write_text(creds.to_json(), encoding="utf-8")
    try:
        os.chmod(TOKEN_PATH, 0o600)
    except OSError:
        pass


def load_credentials():
    """Load cached credentials, refreshing them if needed. Returns None if
    there is no usable token."""
    if not TOKEN_PATH.exists():
        return None

    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials

    try:
        creds = Credentials.from_authorized_user_info(
            json.loads(TOKEN_PATH.read_text(encoding="utf-8")),
            SCOPES,
        )
    except (ValueError, json.JSONDecodeError):
        logger.warning("Stored Gmail token is unreadable; ignoring it.")
        return None

    if creds and creds.expired and creds.refresh_token:
        try:
            creds.refresh(Request())
            _save_credentials(creds)
        except Exception:
            logger.exception("Failed to refresh Gmail token")
            return None

    if not creds or not creds.valid:
        return None
    return creds


def is_connected() -> bool:
    return load_credentials() is not None


def build_service(creds=None):
    """Build a Gmail API service. Falls back to stored credentials."""
    creds = creds or load_credentials()
    if creds is None:
        raise GmailNotConnected("Gmail is not connected.")
    from googleapiclient.discovery import build

    return build("gmail", "v1", credentials=creds, cache_discovery=False)


# --- Message parsing --------------------------------------------------------


def gmail_message_url(message_id: str) -> str:
    """Deep link that opens this message in the Gmail web UI."""
    return f"https://mail.google.com/mail/u/0/#inbox/{message_id}"


def _header(headers: list[dict], name: str) -> str:
    target = name.lower()
    for header in headers or []:
        if (header.get("name") or "").lower() == target:
            return header.get("value") or ""
    return ""


def _decode_b64(data: str) -> str:
    if not data:
        return ""
    try:
        return base64.urlsafe_b64decode(data.encode("utf-8")).decode(
            "utf-8", errors="replace"
        )
    except Exception:
        return ""


def _extract_plain_body(payload: dict) -> str:
    """Pull the text/plain body out of a Gmail message payload."""
    if not payload:
        return ""

    mime = payload.get("mimeType") or ""
    body_data = (payload.get("body") or {}).get("data")
    if mime == "text/plain" and body_data:
        return _decode_b64(body_data)

    # Walk multipart payloads, preferring text/plain over text/html.
    plain = ""
    html = ""
    for part in payload.get("parts") or []:
        part_mime = part.get("mimeType") or ""
        if part_mime.startswith("multipart/"):
            nested = _extract_plain_body(part)
            if nested:
                plain = plain or nested
        elif part_mime == "text/plain":
            plain = plain or _decode_b64((part.get("body") or {}).get("data"))
        elif part_mime == "text/html":
            html = html or _decode_b64((part.get("body") or {}).get("data"))

    if plain:
        return plain
    if html:
        # Crude tag strip so the parser sees readable text, not markup.
        import re

        return re.sub(r"<[^>]+>", " ", html)
    return ""


def parse_message(raw: dict) -> dict:
    """Normalize a Gmail ``messages.get`` payload into the fields we use."""
    message_id = raw.get("id") or ""
    payload = raw.get("payload") or {}
    headers = payload.get("headers") or []
    subject = _header(headers, "Subject").strip()
    sender = _header(headers, "From").strip()
    snippet = (raw.get("snippet") or "").strip()
    body = _extract_plain_body(payload).strip()
    return {
        "id": message_id,
        "subject": subject,
        "sender": sender,
        "snippet": snippet,
        "body": body,
        "url": gmail_message_url(message_id),
    }


# Keep the text we feed the parser bounded so a huge email thread doesn't blow
# past the note length the analyzer expects.
_MAX_BODY_CHARS = 1500


def message_to_note_text(parsed: dict) -> str:
    """Build the free-text note the parser reads for one email."""
    subject = parsed.get("subject") or "(no subject)"
    sender = parsed.get("sender") or "unknown sender"
    body = (parsed.get("body") or parsed.get("snippet") or "").strip()
    if len(body) > _MAX_BODY_CHARS:
        body = body[:_MAX_BODY_CHARS] + "…"
    parts = [f"Email from {sender}.", f"Subject: {subject}"]
    if body:
        parts.append(body)
    return "\n".join(parts)


# --- Fetch + sync -----------------------------------------------------------


def list_recent_messages(
    service, max_results: int = DEFAULT_MAX_RESULTS
) -> list[dict]:
    """List recent primary-inbox messages and return parsed dicts, newest
    first. ``service`` is a Gmail API service (real or a test fake)."""
    listed = (
        service.users()
        .messages()
        .list(
            userId="me",
            maxResults=max_results,
            labelIds=["INBOX"],
            q="category:primary",
        )
        .execute()
    )
    messages = listed.get("messages") or []
    parsed: list[dict] = []
    for stub in messages:
        raw = (
            service.users()
            .messages()
            .get(userId="me", id=stub["id"], format="full")
            .execute()
        )
        parsed.append(parse_message(raw))
    return parsed


def sync_recent_emails(
    max_results: int = DEFAULT_MAX_RESULTS,
    service=None,
) -> dict:
    """Pull recent emails, organize the new ones into tasks, and report.

    Idempotent: an email already stored (by Gmail message id) is skipped, so
    its tasks are never re-created.
    """
    service = service or build_service()
    messages = list_recent_messages(service, max_results)

    created_tasks: list[dict] = []
    new_emails = 0
    skipped = 0

    for parsed in messages:
        message_id = parsed.get("id")
        if not message_id:
            continue
        if database.email_item_exists(message_id):
            skipped += 1
            continue

        item = database.add_email_item_to_db(
            raw_text=message_to_note_text(parsed),
            external_id=message_id,
            title=parsed.get("subject") or None,
            source_url=parsed.get("url"),
        )
        new_emails += 1

        todos = analyze_single_note(item["raw_text"]) or []
        # The task should link back to the email, not to any URL inside it.
        for todo in todos:
            todo["link"] = parsed.get("url")
        created_tasks.extend(
            database.create_tasks_for_item(item["id"], todos)
        )

    return {
        "emails_scanned": len(messages),
        "new_emails": new_emails,
        "skipped": skipped,
        "created_tasks": created_tasks,
    }
