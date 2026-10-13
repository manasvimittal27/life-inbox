import sqlite3
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import IntegrityError


PROJECT_ROOT = Path(__file__).resolve().parent.parent


def alembic_config(database_path: Path) -> Config:
    config = Config(str(PROJECT_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(PROJECT_ROOT / "migrations"))
    config.set_main_option("sqlalchemy.url", f"sqlite:///{database_path}")
    return config


def test_upgrade_creates_inbox_items_in_empty_database(tmp_path, monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    database_path = tmp_path / "empty.db"

    command.upgrade(alembic_config(database_path), "head")

    engine = create_engine(f"sqlite:///{database_path}")
    try:
        inspector = inspect(engine)
        assert {
            "alembic_version",
            "analysis_runs",
            "inbox_items",
            "item_insights",
            "notes",
            "task_source_items",
            "tasks",
        } <= set(inspector.get_table_names())
        columns = {
            column["name"]: column
            for column in inspector.get_columns("inbox_items")
        }
        assert set(columns) == {
            "id",
            "item_type",
            "title",
            "raw_text",
            "source_url",
            "original_filename",
            "mime_type",
            "processing_status",
            "created_at",
            "updated_at",
        }
        assert columns["updated_at"]["nullable"] is True
        assert {
            column["name"]
            for column in inspector.get_columns("analysis_runs")
        } == {
            "id",
            "started_at",
            "completed_at",
            "status",
            "mode",
            "result_json",
        }
        assert {
            column["name"]
            for column in inspector.get_columns("item_insights")
        } == {
            "item_id",
            "analysis_run_id",
            "category",
            "concise_title",
            "priority",
            "updated_at",
        }
        assert {
            column["name"]
            for column in inspector.get_columns("tasks")
        } == {
            "id",
            "text",
            "priority",
            "timing_text",
            "status",
            "dedup_key",
            "created_at",
            "updated_at",
        }
        assert {
            column["name"]
            for column in inspector.get_columns("task_source_items")
        } == {"task_id", "item_id"}
        with engine.connect() as connection:
            revision = connection.scalar(
                text("SELECT version_num FROM alembic_version")
            )
        assert revision == "0006_deduplicate_tasks_by_source"
    finally:
        engine.dispose()


def test_upgrade_adopts_legacy_database_without_changing_notes(
    tmp_path, monkeypatch
):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    database_path = tmp_path / "legacy.db"
    existing_notes = [
        (4, "Existing note", "2026-01-01T10:00:00+00:00"),
        (9, "Another existing note", "2026-01-02T10:00:00+00:00"),
    ]

    with sqlite3.connect(database_path) as connection:
        connection.execute(
            """
            CREATE TABLE notes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                text TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            """
        )
        connection.executemany(
            "INSERT INTO notes (id, text, created_at) VALUES (?, ?, ?)",
            existing_notes,
        )
        connection.commit()

    command.upgrade(alembic_config(database_path), "head")

    engine = create_engine(f"sqlite:///{database_path}")
    try:
        assert {"inbox_items", "notes"} <= set(
            inspect(engine).get_table_names()
        )
        with engine.connect() as connection:
            migrated_notes = connection.execute(
                text(
                    "SELECT id, text, created_at FROM notes ORDER BY id"
                )
            ).all()
            revision = connection.scalar(
                text("SELECT version_num FROM alembic_version")
            )
            updated_values = connection.execute(
                text("SELECT updated_at FROM notes ORDER BY id")
            ).scalars().all()
            migrated_items = connection.execute(
                text(
                    """
                    SELECT
                        id,
                        item_type,
                        title,
                        raw_text,
                        source_url,
                        original_filename,
                        mime_type,
                        processing_status,
                        created_at,
                        updated_at
                    FROM inbox_items
                    ORDER BY id
                    """
                )
            ).all()
            next_id = connection.execute(
                text(
                    """
                    INSERT INTO inbox_items (
                        item_type,
                        raw_text,
                        processing_status,
                        created_at
                    )
                    VALUES ('text', 'New item', 'ready', '2026-01-03')
                    """
                )
            ).lastrowid
        assert [tuple(note) for note in migrated_notes] == existing_notes
        assert updated_values == [None, None]
        assert [tuple(item) for item in migrated_items] == [
            (
                note_id,
                "text",
                None,
                note_text,
                None,
                None,
                None,
                "ready",
                created_at,
                None,
            )
            for note_id, note_text, created_at in existing_notes
        ]
        assert next_id == 10
        assert revision == "0006_deduplicate_tasks_by_source"
    finally:
        engine.dispose()


def test_upgrade_consolidates_tasks_that_share_a_source(tmp_path, monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    database_path = tmp_path / "duplicate_tasks.db"
    config = alembic_config(database_path)
    command.upgrade(config, "0005_create_tasks")

    engine = create_engine(f"sqlite:///{database_path}")
    with engine.begin() as connection:
        connection.execute(
            text(
                """
                INSERT INTO inbox_items (
                    id, item_type, raw_text, processing_status, created_at
                )
                VALUES (1, 'text', 'Prepare notes', 'ready', '2026-01-01')
                """
            )
        )
        connection.execute(
            text(
                """
                INSERT INTO tasks (
                    id, text, priority, timing_text, status, dedup_key,
                    created_at, updated_at
                )
                VALUES
                    (
                        1, 'Prepare class notes', 'Low', NULL, 'completed',
                        'old-key', '2026-01-01', '2026-01-02'
                    ),
                    (
                        2, 'Prepare notes for class', 'High', NULL, 'open',
                        'new-key', '2026-01-03', '2026-01-04'
                    )
                """
            )
        )
        connection.execute(
            text(
                """
                INSERT INTO task_source_items (task_id, item_id)
                VALUES (1, 1), (2, 1)
                """
            )
        )

    command.upgrade(config, "head")

    try:
        with engine.connect() as connection:
            tasks = connection.execute(
                text(
                    """
                    SELECT id, text, priority, status, created_at, updated_at
                    FROM tasks
                    """
                )
            ).all()
            links = connection.execute(
                text(
                    """
                    SELECT task_id, item_id
                    FROM task_source_items
                    """
                )
            ).all()
            revision = connection.scalar(
                text("SELECT version_num FROM alembic_version")
            )

        assert [tuple(task) for task in tasks] == [
            (
                1,
                "Prepare notes for class",
                "High",
                "completed",
                "2026-01-01",
                "2026-01-04",
            )
        ]
        assert [tuple(link) for link in links] == [(1, 1)]
        assert revision == "0006_deduplicate_tasks_by_source"

        with pytest.raises(IntegrityError):
            with engine.begin() as connection:
                connection.execute(
                    text(
                        """
                        INSERT INTO tasks (
                            id, text, priority, status, dedup_key,
                            created_at, updated_at
                        )
                        VALUES (
                            2, 'Another task', 'Medium', 'open',
                            'another-key', '2026-01-05', '2026-01-05'
                        )
                        """
                    )
                )
                connection.execute(
                    text(
                        """
                        INSERT INTO task_source_items (task_id, item_id)
                        VALUES (2, 1)
                        """
                    )
                )
    finally:
        engine.dispose()
