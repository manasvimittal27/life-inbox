"""Tests for inbound Gmail sync.

The Gmail API is always faked here — no live calls, no Google credentials, and
no network. A ``FakeGmailService`` mimics the handful of client methods we use
(``users().messages().list()/get().execute()``), and ``analyze_single_note`` is
stubbed so task creation is deterministic.
"""

import base64
import importlib
import sys

import pytest
from fastapi.testclient import TestClient


def _b64(text: str) -> str:
    return base64.urlsafe_b64encode(text.encode("utf-8")).decode("utf-8")


def _raw_message(msg_id, subject, sender, body, snippet="snippet"):
    """A Gmail ``messages.get`` payload with a plain-text body."""
    return {
        "id": msg_id,
        "snippet": snippet,
        "payload": {
            "mimeType": "text/plain",
            "headers": [
                {"name": "Subject", "value": subject},
                {"name": "From", "value": sender},
            ],
            "body": {"data": _b64(body)},
        },
    }


class FakeGmailService:
    """Minimal stand-in for a googleapiclient Gmail service."""

    def __init__(self, messages):
        # messages: list of raw ``get`` payloads, newest first.
        self._by_id = {m["id"]: m for m in messages}
        self._order = [m["id"] for m in messages]
        self.list_calls = []

    # The real client chains .users().messages().list(...).execute()
    def users(self):
        return self

    def messages(self):
        return self

    def list(self, **kwargs):
        self.list_calls.append(kwargs)
        max_results = kwargs.get("maxResults", len(self._order))
        ids = self._order[:max_results]
        return _Exec({"messages": [{"id": i} for i in ids]})

    def get(self, **kwargs):
        return _Exec(self._by_id[kwargs["id"]])


class _Exec:
    def __init__(self, value):
        self._value = value

    def execute(self):
        return self._value


@pytest.fixture()
def gmail_env(tmp_path, monkeypatch):
    """App wired to a per-test SQLite DB, with gmail_sync bound to it.

    ``app.gmail_sync`` is popped as well as database/main so it re-binds to the
    freshly imported database module.
    """
    test_db_path = tmp_path / "gmail_test.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{test_db_path}")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    # Keep token/secret lookups pointed at an empty tmp dir, never the repo.
    monkeypatch.setenv("GMAIL_TOKEN_FILE", str(tmp_path / "token.json"))
    monkeypatch.setenv(
        "GOOGLE_CLIENT_SECRET_FILE", str(tmp_path / "client_secret.json")
    )

    # Pop the cached modules AND clear them from the ``app`` package namespace.
    # ``from app import gmail_sync`` returns a stale submodule if only
    # sys.modules is cleared (the package still holds the old attribute), which
    # would leave main's gmail_sync out of sync with the one tests patch.
    import app as app_pkg

    for name in ("app.gmail_sync", "app.main", "app.database"):
        sys.modules.pop(name, None)
        app_pkg.__dict__.pop(name.split(".", 1)[1], None)

    main = importlib.import_module("app.main")
    database = importlib.import_module("app.database")
    gmail_sync = importlib.import_module("app.gmail_sync")

    assert database.engine.url.database == str(test_db_path)

    # Deterministic parser: one P1 task per email, echoing the subject.
    def fake_analyze(text, today=None):
        subject = ""
        for line in text.splitlines():
            if line.startswith("Subject: "):
                subject = line[len("Subject: "):]
        return [
            {
                "task": f"Reply about {subject}".strip(),
                "bucket": "P1",
                "due_date": None,
                "task_type": "one_off",
                "recurrence": None,
                "importance": "normal",
                "effort_min": 30,
                "link": None,
            }
        ]

    monkeypatch.setattr(gmail_sync, "analyze_single_note", fake_analyze)

    with TestClient(main.app) as client:
        yield {
            "client": client,
            "database": database,
            "gmail_sync": gmail_sync,
            "main": main,
        }

    database.engine.dispose()
    for name in ("app.gmail_sync", "app.main", "app.database"):
        sys.modules.pop(name, None)


# --- Parsing ----------------------------------------------------------------


def test_parse_message_extracts_fields_and_url(gmail_env):
    gmail_sync = gmail_env["gmail_sync"]
    raw = _raw_message(
        "abc123", "Invoice due Friday", "Acme <billing@acme.com>", "Pay $40."
    )

    parsed = gmail_sync.parse_message(raw)

    assert parsed["id"] == "abc123"
    assert parsed["subject"] == "Invoice due Friday"
    assert parsed["sender"] == "Acme <billing@acme.com>"
    assert parsed["body"] == "Pay $40."
    assert parsed["url"] == (
        "https://mail.google.com/mail/u/0/#inbox/abc123"
    )


def test_parse_message_reads_multipart_plain_text(gmail_env):
    gmail_sync = gmail_env["gmail_sync"]
    raw = {
        "id": "multi1",
        "snippet": "s",
        "payload": {
            "mimeType": "multipart/alternative",
            "headers": [
                {"name": "Subject", "value": "Hi"},
                {"name": "From", "value": "a@b.com"},
            ],
            "parts": [
                {
                    "mimeType": "text/plain",
                    "body": {"data": _b64("Plain body wins")},
                },
                {
                    "mimeType": "text/html",
                    "body": {"data": _b64("<p>HTML body</p>")},
                },
            ],
        },
    }

    parsed = gmail_sync.parse_message(raw)
    assert parsed["body"] == "Plain body wins"


def test_message_to_note_text_includes_sender_and_subject(gmail_env):
    gmail_sync = gmail_env["gmail_sync"]
    text = gmail_sync.message_to_note_text(
        {
            "sender": "boss@work.com",
            "subject": "Submit report",
            "body": "By tomorrow please.",
        }
    )
    assert "boss@work.com" in text
    assert "Subject: Submit report" in text
    assert "By tomorrow please." in text


# --- DB dedup helpers -------------------------------------------------------


def test_email_item_exists_tracks_external_id(gmail_env):
    database = gmail_env["database"]
    assert database.email_item_exists("gmail-1") is False

    item = database.add_email_item_to_db(
        raw_text="Email from x", external_id="gmail-1", source_url="http://x"
    )
    assert item["item_type"] == "email"
    assert database.email_item_exists("gmail-1") is True
    # A different id is independent.
    assert database.email_item_exists("gmail-2") is False


# --- Full sync --------------------------------------------------------------


def test_sync_creates_tasks_with_email_links(gmail_env):
    gmail_sync = gmail_env["gmail_sync"]
    service = FakeGmailService(
        [
            _raw_message("m1", "Pay rent", "landlord@x.com", "Due soon"),
            _raw_message("m2", "Team lunch", "pal@x.com", "Friday?"),
        ]
    )

    result = gmail_sync.sync_recent_emails(service=service)

    assert result["emails_scanned"] == 2
    assert result["new_emails"] == 2
    assert result["skipped"] == 0
    tasks = result["created_tasks"]
    assert len(tasks) == 2
    texts = {t["text"] for t in tasks}
    assert texts == {"Reply about Pay rent", "Reply about Team lunch"}
    # Every task links back to its email, not to any URL inside the body.
    links = {t["link"] for t in tasks}
    assert links == {
        "https://mail.google.com/mail/u/0/#inbox/m1",
        "https://mail.google.com/mail/u/0/#inbox/m2",
    }
    for t in tasks:
        assert t["bucket"] == "P1"


def test_resync_is_idempotent(gmail_env):
    gmail_sync = gmail_env["gmail_sync"]
    database = gmail_env["database"]
    messages = [_raw_message("dup1", "Renew license", "dmv@x.com", "soon")]

    first = gmail_sync.sync_recent_emails(service=FakeGmailService(messages))
    assert first["new_emails"] == 1
    assert len(first["created_tasks"]) == 1

    # Re-syncing the same message creates nothing new.
    second = gmail_sync.sync_recent_emails(service=FakeGmailService(messages))
    assert second["new_emails"] == 0
    assert second["skipped"] == 1
    assert second["created_tasks"] == []

    # Exactly one email item and one task exist.
    with database.SessionLocal() as session:
        email_items = (
            session.query(database.InboxItemRecord)
            .filter(database.InboxItemRecord.item_type == "email")
            .count()
        )
        tasks = session.query(database.TaskRecord).count()
    assert email_items == 1
    assert tasks == 1


# --- Routes -----------------------------------------------------------------


def test_status_reports_configured_and_connected(gmail_env, monkeypatch):
    client = gmail_env["client"]
    gmail_sync = gmail_env["gmail_sync"]

    # Not configured by default (no client secret in the tmp dir).
    assert client.get("/api/gmail/status").json() == {
        "configured": False,
        "connected": False,
    }

    monkeypatch.setattr(gmail_sync, "client_secret_configured", lambda: True)
    monkeypatch.setattr(gmail_sync, "is_connected", lambda: True)
    assert client.get("/api/gmail/status").json() == {
        "configured": True,
        "connected": True,
    }


def test_sync_route_organizes_new_emails(gmail_env, monkeypatch):
    client = gmail_env["client"]
    gmail_sync = gmail_env["gmail_sync"]
    service = FakeGmailService(
        [_raw_message("r1", "Sign the form", "hr@x.com", "today")]
    )
    monkeypatch.setattr(gmail_sync, "build_service", lambda creds=None: service)

    response = client.post("/api/gmail/sync")

    assert response.status_code == 200
    data = response.json()
    assert data["new_emails"] == 1
    assert data["created_tasks"][0]["text"] == "Reply about Sign the form"

    # The synced email shows up as an email inbox item.
    items = client.get("/api/items", params={"item_type": "email"}).json()
    assert len(items) == 1
    assert items[0]["item_type"] == "email"
    assert items[0]["source_url"] == (
        "https://mail.google.com/mail/u/0/#inbox/r1"
    )

    # The created task is visible in the normal task list with its link.
    tasks = client.get("/api/tasks").json()
    assert any(
        t["link"] == "https://mail.google.com/mail/u/0/#inbox/r1"
        for t in tasks
    )


def test_sync_route_returns_409_when_not_connected(gmail_env, monkeypatch):
    client = gmail_env["client"]
    gmail_sync = gmail_env["gmail_sync"]

    def not_connected(creds=None):
        raise gmail_sync.GmailNotConnected("nope")

    monkeypatch.setattr(gmail_sync, "build_service", not_connected)

    response = client.post("/api/gmail/sync")
    assert response.status_code == 409
