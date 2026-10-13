import importlib
import json
import sys
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient


def analysis_without_metadata(data):
    return {
        key: value
        for key, value in data.items()
        if key not in {"analysis_run_id", "generated_at"}
    }


@pytest.fixture()
def client(tmp_path, monkeypatch):
    """Create an app client backed only by a per-test SQLite database."""
    test_db_path = tmp_path / "life_inbox_test.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{test_db_path}")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    sys.modules.pop("app.database", None)
    sys.modules.pop("app.main", None)
    main = importlib.import_module("app.main")
    database = importlib.import_module("app.database")

    assert database.engine.url.database == str(test_db_path)
    with TestClient(main.app) as test_client:
        yield test_client

    database.engine.dispose()
    sys.modules.pop("app.main", None)
    sys.modules.pop("app.database", None)


def test_home_page_loads(client):
    response = client.get("/")

    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    assert "<title>Life Inbox</title>" in response.text
    assert "Analyze Entire Inbox with AI" in response.text


@pytest.mark.parametrize(
    ("path", "content_type", "expected_content"),
    [
        ("/static/styles.css", "text/css", ".wrap"),
        ("/static/app.js", "javascript", "async function addNote()"),
    ],
)
def test_static_assets_load(client, path, content_type, expected_content):
    response = client.get(path)

    assert response.status_code == 200
    assert content_type in response.headers["content-type"]
    assert expected_content in response.text


def test_health_check(client):
    response = client.get("/api/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_create_and_list_canonical_text_item(client):
    response = client.post(
        "/api/items",
        json={"raw_text": "Canonical text item"},
    )

    assert response.status_code == 200
    item = response.json()
    assert item == {
        "id": 1,
        "item_type": "text",
        "title": None,
        "raw_text": "Canonical text item",
        "source_url": None,
        "original_filename": None,
        "mime_type": None,
        "processing_status": "ready",
        "created_at": item["created_at"],
        "updated_at": None,
    }
    assert client.get("/api/items").json() == [item]


def test_item_search_is_case_insensitive_for_raw_text_and_title(client):
    text_match = client.post(
        "/api/items",
        json={"raw_text": "Plan the SUMMER holiday"},
    ).json()
    title_match = client.post(
        "/api/items",
        json={"raw_text": "Review the figures"},
    ).json()

    database = sys.modules["app.database"]
    with database.SessionLocal() as session:
        item = session.get(database.InboxItemRecord, title_match["id"])
        item.title = "Annual BUDGET"
        session.commit()

    raw_results = client.get("/api/items", params={"q": "summer"})
    title_results = client.get("/api/items", params={"q": "budget"})

    assert raw_results.status_code == 200
    assert [item["id"] for item in raw_results.json()] == [text_match["id"]]
    assert title_results.status_code == 200
    assert [item["id"] for item in title_results.json()] == [
        title_match["id"]
    ]


def test_item_search_combines_all_filters(client):
    first = client.post(
        "/api/items",
        json={"raw_text": "Quarterly REPORT"},
    ).json()
    second = client.post(
        "/api/items",
        json={"raw_text": "Quarterly report archive"},
    ).json()
    third = client.post(
        "/api/items",
        json={"raw_text": "Personal report"},
    ).json()

    database = sys.modules["app.database"]
    with database.SessionLocal() as session:
        run = database.AnalysisRunRecord(
            started_at="2026-01-01T00:00:00+00:00",
            completed_at="2026-01-01T00:00:01+00:00",
            status="succeeded",
            mode="fallback_no_api_call",
            result_json="{}",
        )
        session.add(run)
        session.flush()
        session.add_all(
            [
                database.ItemInsightRecord(
                    item_id=first["id"],
                    analysis_run_id=run.id,
                    category="Work",
                    concise_title="Quarterly report",
                    priority="High",
                    updated_at=run.completed_at,
                ),
                database.ItemInsightRecord(
                    item_id=second["id"],
                    analysis_run_id=run.id,
                    category="Work",
                    concise_title="Archived report",
                    priority="High",
                    updated_at=run.completed_at,
                ),
                database.ItemInsightRecord(
                    item_id=third["id"],
                    analysis_run_id=run.id,
                    category="Personal",
                    concise_title="Personal report",
                    priority="Low",
                    updated_at=run.completed_at,
                ),
            ]
        )
        archived = session.get(database.InboxItemRecord, second["id"])
        archived.processing_status = "pending"
        session.commit()

    response = client.get(
        "/api/items",
        params={
            "q": "report",
            "item_type": "text",
            "category": "Work",
            "priority": "High",
            "processing_status": "ready",
        },
    )

    assert response.status_code == 200
    assert [item["id"] for item in response.json()] == [first["id"]]


def test_item_search_handles_empty_results_and_sql_characters(client):
    percent_match = client.post(
        "/api/items",
        json={"raw_text": "Discount is 50% today"},
    ).json()
    other = client.post(
        "/api/items",
        json={"raw_text": "Ordinary note"},
    ).json()

    literal_percent = client.get("/api/items", params={"q": "50%"})
    unsafe_text = client.get(
        "/api/items",
        params={"q": "%' OR 1=1 --"},
    )
    no_results = client.get("/api/items", params={"q": "not-present"})
    empty_search = client.get("/api/items", params={"q": "   "})

    assert [item["id"] for item in literal_percent.json()] == [
        percent_match["id"]
    ]
    assert unsafe_text.status_code == 200
    assert unsafe_text.json() == []
    assert no_results.status_code == 200
    assert no_results.json() == []
    assert [item["id"] for item in empty_search.json()] == [
        percent_match["id"],
        other["id"],
    ]


def test_item_search_applies_conservative_result_limit(client):
    for number in range(3):
        client.post(
            "/api/items",
            json={"raw_text": f"Matching item {number}"},
        )

    response = client.get(
        "/api/items",
        params={"q": "matching", "limit": 2},
    )

    assert response.status_code == 200
    assert [item["id"] for item in response.json()] == [1, 2]


def test_update_canonical_text_item(client):
    created = client.post(
        "/api/items",
        json={"raw_text": "Original item"},
    ).json()

    response = client.put(
        f"/api/items/{created['id']}",
        json={"raw_text": "Updated item"},
    )

    assert response.status_code == 200
    updated = response.json()
    assert updated["id"] == created["id"]
    assert updated["raw_text"] == "Updated item"
    assert updated["created_at"] == created["created_at"]
    assert updated["updated_at"] is not None
    assert client.get("/api/notes").json() == [
        {
            "id": created["id"],
            "text": "Updated item",
            "created_at": created["created_at"],
        }
    ]


@pytest.mark.parametrize("raw_text", ["", "   \t\n", "x" * 2001])
def test_canonical_item_rejects_invalid_text(client, raw_text):
    response = client.post(
        "/api/items",
        json={"raw_text": raw_text},
    )

    assert response.status_code == 422
    assert client.get("/api/items").json() == []


@pytest.mark.parametrize("raw_text", ["", "   \t\n", "x" * 2001])
def test_canonical_item_update_rejects_invalid_text(client, raw_text):
    created = client.post(
        "/api/items",
        json={"raw_text": "Keep this item"},
    ).json()

    response = client.put(
        f"/api/items/{created['id']}",
        json={"raw_text": raw_text},
    )

    assert response.status_code == 422
    assert client.get("/api/items").json() == [created]


def test_canonical_item_missing_ids_return_404(client):
    update_response = client.put(
        "/api/items/999",
        json={"raw_text": "Updated item"},
    )
    delete_response = client.delete("/api/items/999")

    assert update_response.status_code == 404
    assert update_response.json() == {"detail": "Item not found."}
    assert delete_response.status_code == 404
    assert delete_response.json() == {"detail": "Item not found."}


def test_delete_canonical_item_affects_only_requested_item(client):
    first = client.post(
        "/api/items", json={"raw_text": "Delete this item"}
    ).json()
    second = client.post(
        "/api/items", json={"raw_text": "Keep this item"}
    ).json()

    response = client.delete(f"/api/items/{first['id']}")

    assert response.status_code == 204
    assert response.content == b""
    assert client.get("/api/items").json() == [second]


def test_notes_api_uses_canonical_item_storage(client):
    note = client.post(
        "/api/notes", json={"text": "Compatibility note"}
    ).json()

    items = client.get("/api/items").json()

    assert len(items) == 1
    assert items[0]["id"] == note["id"]
    assert items[0]["item_type"] == "text"
    assert items[0]["raw_text"] == note["text"]


def test_create_note(client):
    response = client.post("/api/notes", json={"text": "Buy milk tomorrow"})

    assert response.status_code == 200
    note = response.json()
    assert note["id"] == 1
    assert note["text"] == "Buy milk tomorrow"
    assert note["created_at"]


def test_list_saved_notes(client):
    first = client.post("/api/notes", json={"text": "First note"})
    second = client.post("/api/notes", json={"text": "Second note"})

    response = client.get("/api/notes")

    assert first.status_code == 200
    assert second.status_code == 200
    assert response.status_code == 200
    assert [note["text"] for note in response.json()] == [
        "First note",
        "Second note",
    ]


def test_update_note(client):
    created = client.post(
        "/api/notes", json={"text": "Original note"}
    ).json()

    response = client.put(
        f"/api/notes/{created['id']}",
        json={"text": "Updated note"},
    )

    assert response.status_code == 200
    assert response.json() == {
        "id": created["id"],
        "text": "Updated note",
        "created_at": created["created_at"],
    }
    assert client.get("/api/notes").json() == [response.json()]

    database = sys.modules["app.database"]
    with database.SessionLocal() as session:
        stored_item = session.get(database.InboxItemRecord, created["id"])
        assert stored_item.updated_at is not None


@pytest.mark.parametrize("text", ["", "   \t\n", "x" * 2001])
def test_update_note_rejects_invalid_text(client, text):
    created = client.post(
        "/api/notes", json={"text": "Keep this note"}
    ).json()

    response = client.put(
        f"/api/notes/{created['id']}",
        json={"text": text},
    )

    assert response.status_code == 422
    assert client.get("/api/notes").json() == [created]


def test_update_nonexistent_note_returns_404(client):
    response = client.put(
        "/api/notes/999",
        json={"text": "Updated note"},
    )

    assert response.status_code == 404
    assert response.json() == {"detail": "Note not found."}


def test_delete_only_requested_note(client):
    first = client.post(
        "/api/notes", json={"text": "Delete this note"}
    ).json()
    second = client.post(
        "/api/notes", json={"text": "Keep this note"}
    ).json()

    response = client.delete(f"/api/notes/{first['id']}")

    assert response.status_code == 204
    assert response.content == b""
    assert client.get("/api/notes").json() == [second]


def test_delete_nonexistent_note_returns_404(client):
    response = client.delete("/api/notes/999")

    assert response.status_code == 404
    assert response.json() == {"detail": "Note not found."}


@pytest.mark.parametrize("text", ["", "   \t\n", "x" * 2001])
def test_invalid_note_text_is_rejected(client, text):
    response = client.post("/api/notes", json={"text": text})

    assert response.status_code == 422
    assert client.get("/api/notes").json() == []


def test_latest_analysis_is_empty_before_first_success(client):
    response = client.get("/api/analyze/latest")

    assert response.status_code == 204
    assert response.content == b""


def test_analyze_empty_inbox(client):
    response = client.post("/api/analyze")

    assert response.status_code == 200
    data = response.json()
    assert data["analysis_run_id"] == 1
    assert data["generated_at"]
    assert analysis_without_metadata(data) == {
        "buckets": [],
        "todos": [],
        "priority_view": {"High": [], "Medium": [], "Low": []},
        "mode": "empty_inbox",
    }
    assert client.get("/api/analyze/latest").json() == data


def test_analyze_uses_deterministic_fallback_without_openai_key(client):
    note_text = "Call the team tomorrow"
    create_response = client.post("/api/notes", json={"text": note_text})

    response = client.post("/api/analyze")

    assert create_response.status_code == 200
    assert response.status_code == 200
    data = response.json()
    assert data["analysis_run_id"] == 1
    assert data["generated_at"]
    assert analysis_without_metadata(data) == {
        "buckets": [
            {
                "name": "Work",
                "notes": [
                    {
                        "id": 1,
                        "title": note_text,
                        "priority": "High",
                    }
                ],
            }
        ],
        "todos": [
            {
                "task": note_text,
                "priority": "High",
                "source_note_ids": [1],
                "when": None,
            }
        ],
        "priority_view": {
            "High": [{"id": 1, "title": note_text}],
            "Medium": [],
            "Low": [],
        },
        "mode": "fallback_no_api_call",
    }


def test_successful_analysis_persists_run_and_item_insight(client):
    created = client.post(
        "/api/items",
        json={"raw_text": "Call the team tomorrow"},
    ).json()

    analysis = client.post("/api/analyze").json()

    database = sys.modules["app.database"]
    with database.SessionLocal() as session:
        run = session.get(
            database.AnalysisRunRecord,
            analysis["analysis_run_id"],
        )
        insight = session.get(
            database.ItemInsightRecord,
            created["id"],
        )
        assert run.status == "succeeded"
        assert run.mode == "fallback_no_api_call"
        assert run.started_at
        assert run.completed_at == analysis["generated_at"]
        assert json.loads(run.result_json) == analysis_without_metadata(
            analysis
        )
        assert insight.analysis_run_id == run.id
        assert insight.category == "Work"
        assert insight.concise_title == "Call the team tomorrow"
        assert insight.priority == "High"


def test_analysis_creates_open_task_with_source_link(client):
    created = client.post(
        "/api/items",
        json={"raw_text": "Call the team tomorrow"},
    ).json()

    client.post("/api/analyze")
    response = client.get("/api/tasks")

    assert response.status_code == 200
    tasks = response.json()
    assert len(tasks) == 1
    assert tasks[0]["text"] == "Call the team tomorrow"
    assert tasks[0]["priority"] == "High"
    assert tasks[0]["timing_text"] is None
    assert tasks[0]["status"] == "open"
    assert tasks[0]["source_item_ids"] == [created["id"]]
    assert tasks[0]["created_at"]
    assert tasks[0]["updated_at"]


def test_task_statuses_survive_reload_and_reanalysis_without_duplicates(client):
    client.post(
        "/api/items",
        json={"raw_text": "Call the team tomorrow"},
    )
    client.post("/api/analyze")
    task = client.get("/api/tasks").json()[0]

    completed = client.post(
        f"/api/tasks/{task['id']}/complete"
    ).json()

    assert completed["status"] == "completed"
    assert client.get("/api/tasks").json() == []
    assert client.get("/api/tasks?status=completed").json() == [completed]

    client.post("/api/analyze")
    all_tasks = client.get("/api/tasks?status=all").json()
    assert len(all_tasks) == 1
    assert all_tasks[0]["id"] == task["id"]
    assert all_tasks[0]["status"] == "completed"

    reopened = client.post(
        f"/api/tasks/{task['id']}/reopen"
    ).json()
    assert reopened["status"] == "open"
    assert client.get("/api/tasks").json() == [reopened]

    dismissed = client.post(
        f"/api/tasks/{task['id']}/dismiss"
    ).json()
    assert dismissed["status"] == "dismissed"
    assert client.get("/api/tasks").json() == []
    assert client.get("/api/tasks?status=dismissed").json() == [dismissed]

    client.post("/api/analyze")
    final_tasks = client.get("/api/tasks?status=all").json()
    assert len(final_tasks) == 1
    assert final_tasks[0]["id"] == task["id"]
    assert final_tasks[0]["status"] == "dismissed"


def test_reanalysis_updates_task_by_source_without_changing_status(
    client, monkeypatch
):
    item = client.post(
        "/api/items",
        json={"raw_text": "Prepare the class notes"},
    ).json()
    main = sys.modules["app.main"]
    analyses = iter(
        [
            ("Prepare class notes", "Low"),
            ("Prepare notes for class", "High"),
        ]
    )

    def analyze(_notes):
        task_text, priority = next(analyses)
        return {
            "buckets": [
                {
                    "name": "Work",
                    "notes": [
                        {
                            "id": item["id"],
                            "title": task_text,
                            "priority": priority,
                        }
                    ],
                }
            ],
            "todos": [
                {
                    "task": task_text,
                    "priority": priority,
                    "source_note_ids": [item["id"]],
                    "when": None,
                }
            ],
            "priority_view": {
                "High": (
                    [{"id": item["id"], "title": task_text}]
                    if priority == "High"
                    else []
                ),
                "Medium": [],
                "Low": (
                    [{"id": item["id"], "title": task_text}]
                    if priority == "Low"
                    else []
                ),
            },
            "mode": "openai",
        }

    monkeypatch.setattr(main, "analyze_inbox_with_openai", analyze)

    client.post("/api/analyze")
    original = client.get("/api/tasks").json()[0]
    client.post(f"/api/tasks/{original['id']}/complete")

    client.post("/api/analyze")
    tasks = client.get("/api/tasks?status=all").json()

    assert len(tasks) == 1
    assert tasks[0]["id"] == original["id"]
    assert tasks[0]["text"] == "Prepare notes for class"
    assert tasks[0]["priority"] == "High"
    assert tasks[0]["status"] == "completed"
    assert tasks[0]["source_item_ids"] == [item["id"]]


def test_generated_task_preserves_multiple_source_links(client, monkeypatch):
    first = client.post(
        "/api/items", json={"raw_text": "First source"}
    ).json()
    second = client.post(
        "/api/items", json={"raw_text": "Second source"}
    ).json()
    main = sys.modules["app.main"]

    monkeypatch.setattr(
        main,
        "analyze_inbox_with_openai",
        lambda _notes: {
            "buckets": [
                {
                    "name": "Work",
                    "notes": [
                        {
                            "id": first["id"],
                            "title": "First source",
                            "priority": "Medium",
                        },
                        {
                            "id": second["id"],
                            "title": "Second source",
                            "priority": "Medium",
                        },
                    ],
                }
            ],
            "todos": [
                {
                    "task": "Follow up on both sources",
                    "priority": "Medium",
                    "source_note_ids": [second["id"], first["id"]],
                    "when": "tomorrow",
                }
            ],
            "priority_view": {
                "High": [],
                "Medium": [
                    {"id": first["id"], "title": "First source"},
                    {"id": second["id"], "title": "Second source"},
                ],
                "Low": [],
            },
            "mode": "openai",
        },
    )

    response = client.post("/api/analyze")

    assert response.status_code == 200
    task = client.get("/api/tasks").json()[0]
    assert task["text"] == "Follow up on both sources"
    assert task["timing_text"] == "tomorrow"
    assert task["source_item_ids"] == [first["id"], second["id"]]

    database = sys.modules["app.database"]
    with database.SessionLocal() as session:
        assert session.query(database.TaskSourceItemRecord).count() == 2


@pytest.mark.parametrize("action", ["complete", "reopen", "dismiss"])
def test_task_status_actions_return_404_for_unknown_id(client, action):
    response = client.post(f"/api/tasks/999/{action}")

    assert response.status_code == 404
    assert response.json() == {"detail": "Task not found."}


def test_task_list_rejects_invalid_status(client):
    response = client.get("/api/tasks?status=invalid")

    assert response.status_code == 422


def test_failed_analysis_keeps_previous_success(client, monkeypatch):
    client.post(
        "/api/items",
        json={"raw_text": "Call the team tomorrow"},
    )
    previous = client.post("/api/analyze").json()
    main = sys.modules["app.main"]

    def fail_analysis(_notes):
        raise RuntimeError("simulated provider failure")

    monkeypatch.setattr(main, "analyze_inbox_with_openai", fail_analysis)

    failed_response = client.post("/api/analyze")

    assert failed_response.status_code == 500
    assert client.get("/api/analyze/latest").json() == previous

    database = sys.modules["app.database"]
    with database.SessionLocal() as session:
        runs = session.query(database.AnalysisRunRecord).order_by(
            database.AnalysisRunRecord.id
        ).all()
        insight = session.get(database.ItemInsightRecord, 1)
        assert [run.status for run in runs] == ["succeeded", "failed"]
        assert runs[1].mode == "error"
        assert runs[1].result_json is None
        assert insight.analysis_run_id == runs[0].id


def test_invalid_analysis_result_is_not_saved(client, monkeypatch):
    client.post(
        "/api/items",
        json={"raw_text": "Call the team tomorrow"},
    )
    main = sys.modules["app.main"]

    monkeypatch.setattr(
        main,
        "analyze_inbox_with_openai",
        lambda _notes: {
            "buckets": [],
            "todos": [],
            "priority_view": {"High": [], "Medium": [], "Low": []},
            "mode": "openai",
        },
    )

    response = client.post("/api/analyze")

    assert response.status_code == 500
    assert client.get("/api/analyze/latest").status_code == 204

    database = sys.modules["app.database"]
    with database.SessionLocal() as session:
        run = session.get(database.AnalysisRunRecord, 1)
        assert run.status == "failed"
        assert run.result_json is None
        assert session.query(database.ItemInsightRecord).count() == 0


def test_create_note_returns_friendly_error(client, monkeypatch):
    main = sys.modules["app.main"]

    def fail_to_save(_text):
        raise RuntimeError("database password should not be exposed")

    monkeypatch.setattr(main, "add_note_to_db", fail_to_save)

    response = client.post("/api/notes", json={"text": "Keep this text"})

    assert response.status_code == 500
    assert response.json() == {
        "detail": "Unable to save note. Please try again."
    }
    assert "password" not in response.text


def test_list_notes_returns_friendly_error(client, monkeypatch):
    main = sys.modules["app.main"]

    def fail_to_list():
        raise RuntimeError("private database location")

    monkeypatch.setattr(main, "list_notes_from_db", fail_to_list)

    response = client.get("/api/notes")

    assert response.status_code == 500
    assert response.json() == {
        "detail": "Unable to load notes. Please try again."
    }
    assert "private database" not in response.text


def test_update_note_returns_friendly_error(client, monkeypatch):
    main = sys.modules["app.main"]

    def fail_to_update(_note_id, _text):
        raise RuntimeError("private database location")

    monkeypatch.setattr(main, "update_note_in_db", fail_to_update)

    response = client.put(
        "/api/notes/1",
        json={"text": "Updated note"},
    )

    assert response.status_code == 500
    assert response.json() == {
        "detail": "Unable to update note. Please try again."
    }
    assert "private database" not in response.text


def test_delete_note_returns_friendly_error(client, monkeypatch):
    main = sys.modules["app.main"]

    def fail_to_delete(_note_id):
        raise RuntimeError("private database location")

    monkeypatch.setattr(main, "delete_note_from_db", fail_to_delete)

    response = client.delete("/api/notes/1")

    assert response.status_code == 500
    assert response.json() == {
        "detail": "Unable to delete note. Please try again."
    }
    assert "private database" not in response.text


def test_analyze_returns_friendly_error(client, monkeypatch):
    main = sys.modules["app.main"]

    def fail_to_analyze(_notes):
        raise RuntimeError("OPENAI_API_KEY=super-secret")

    monkeypatch.setattr(main, "analyze_inbox_with_openai", fail_to_analyze)

    response = client.post("/api/analyze")

    assert response.status_code == 500
    assert response.json() == {
        "detail": "Unable to analyze inbox. Please try again."
    }
    assert "super-secret" not in response.text


def test_valid_mocked_openai_output_is_validated(monkeypatch):
    import openai

    from app import organizer

    model_output = {
        "buckets": [
            {
                "name": "Work",
                "notes": [
                    {
                        "id": 1,
                        "title": "Send summary",
                        "priority": "Medium",
                    }
                ],
            }
        ],
        "todos": [
            {
                "task": "Send the meeting summary",
                "priority": "Medium",
                "source_note_ids": [1],
                "when": None,
            }
        ],
        "priority_view": {
            "High": [],
            "Medium": [{"id": 1, "title": "Send summary"}],
            "Low": [],
        },
    }
    fake_responses = SimpleNamespace(
        create=lambda **_kwargs: SimpleNamespace(
            output_text=json.dumps(model_output)
        )
    )
    monkeypatch.setenv("OPENAI_API_KEY", "configured-for-test")
    monkeypatch.setattr(
        openai,
        "OpenAI",
        lambda: SimpleNamespace(responses=fake_responses),
    )

    result = organizer.analyze_inbox_with_openai(
        [
            {
                "id": 1,
                "text": "Send the meeting summary",
                "created_at": "2026-01-01T00:00:00+00:00",
            }
        ]
    )

    assert result["mode"] == "openai"
    assert result["buckets"][0]["notes"][0]["priority"] == "Medium"


def test_malformed_mocked_openai_output_uses_fallback(monkeypatch):
    import openai

    from app import organizer

    fake_responses = SimpleNamespace(
        create=lambda **_kwargs: SimpleNamespace(
            output_text="not valid JSON"
        )
    )
    monkeypatch.setenv("OPENAI_API_KEY", "configured-for-test")
    monkeypatch.setattr(
        openai,
        "OpenAI",
        lambda: SimpleNamespace(responses=fake_responses),
    )

    result = organizer.analyze_inbox_with_openai(
        [
            {
                "id": 1,
                "text": "Call the team tomorrow",
                "created_at": "2026-01-01T00:00:00+00:00",
            }
        ]
    )

    assert result["mode"] == "fallback_after_api_error"
    assert result["buckets"][0]["notes"][0]["id"] == 1


def test_openai_failure_fallback_omits_technical_details(monkeypatch):
    import openai

    from app import organizer

    def fail_client_creation():
        raise RuntimeError("OPENAI_API_KEY=super-secret")

    monkeypatch.setenv("OPENAI_API_KEY", "configured-for-test")
    monkeypatch.setattr(openai, "OpenAI", fail_client_creation)

    result = organizer.analyze_inbox_with_openai(
        [
            {
                "id": 1,
                "text": "Call the team tomorrow",
                "created_at": "2026-01-01T00:00:00+00:00",
            }
        ]
    )

    assert result["mode"] == "fallback_after_api_error"
    assert "error" not in result
    assert "super-secret" not in repr(result)
