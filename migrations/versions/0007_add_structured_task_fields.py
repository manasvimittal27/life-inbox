"""Add structured scheduling fields to tasks.

Revision ID: 0007_add_structured_task_fields
Revises: 0006_deduplicate_tasks_by_source
"""

from typing import Optional, Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0007_add_structured_task_fields"
down_revision: Optional[str] = "0006_deduplicate_tasks_by_source"
branch_labels: Optional[Union[str, Sequence[str]]] = None
depends_on: Optional[Union[str, Sequence[str]]] = None


# New columns introduced by this revision, with the DDL used to add them to an
# existing tasks table. Timestamps/text stay TEXT (ISO strings) to match the
# rest of the schema; effort_min is the only integer.
NEW_TASK_COLUMNS = {
    "bucket": sa.Column("bucket", sa.Text(), nullable=True),
    "due_date": sa.Column("due_date", sa.Text(), nullable=True),
    "task_type": sa.Column(
        "task_type", sa.Text(), nullable=False, server_default="one_off"
    ),
    "recurrence": sa.Column("recurrence", sa.Text(), nullable=True),
    "importance": sa.Column(
        "importance", sa.Text(), nullable=False, server_default="normal"
    ),
    "effort_min": sa.Column(
        "effort_min", sa.Integer(), nullable=False, server_default="60"
    ),
}

BASE_TASK_COLUMNS = {
    "id",
    "text",
    "priority",
    "timing_text",
    "status",
    "dedup_key",
    "created_at",
    "updated_at",
}


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    tables = set(inspector.get_table_names())

    if "tasks" not in tables:
        # Fresh databases get the full table (including these columns) from
        # Base.metadata.create_all; nothing to migrate here.
        return

    existing = {column["name"] for column in inspector.get_columns("tasks")}
    missing_base = BASE_TASK_COLUMNS - existing
    if missing_base:
        names = ", ".join(sorted(missing_base))
        raise RuntimeError(
            f"Existing tasks table is incompatible; missing: {names}"
        )

    for name, column in NEW_TASK_COLUMNS.items():
        if name not in existing:
            op.add_column("tasks", column)


def downgrade() -> None:
    raise RuntimeError(
        "Downgrade is disabled to avoid deleting persistent task data"
    )
