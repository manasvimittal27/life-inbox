"""Create per-day task progress.

Revision ID: 0008_create_task_progress
Revises: 0007_add_structured_task_fields
"""

from typing import Optional, Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0008_create_task_progress"
down_revision: Optional[str] = "0007_add_structured_task_fields"
branch_labels: Optional[Union[str, Sequence[str]]] = None
depends_on: Optional[Union[str, Sequence[str]]] = None


TASK_PROGRESS_COLUMNS = {
    "task_id",
    "progress_date",
    "pct",
    "note",
    "updated_at",
}


def _validate_columns(inspector, table_name: str, required: set) -> None:
    columns = {column["name"] for column in inspector.get_columns(table_name)}
    missing = required - columns
    if missing:
        names = ", ".join(sorted(missing))
        raise RuntimeError(
            f"Existing {table_name} table is incompatible; missing: {names}"
        )


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    tables = set(inspector.get_table_names())

    if "task_progress" not in tables:
        op.create_table(
            "task_progress",
            sa.Column("task_id", sa.Integer(), nullable=False),
            sa.Column("progress_date", sa.Text(), nullable=False),
            sa.Column("pct", sa.Integer(), nullable=False),
            sa.Column("note", sa.Text(), nullable=True),
            sa.Column("updated_at", sa.Text(), nullable=False),
            sa.ForeignKeyConstraint(
                ["task_id"],
                ["tasks.id"],
                ondelete="CASCADE",
            ),
            sa.PrimaryKeyConstraint("task_id", "progress_date"),
        )
    else:
        _validate_columns(inspector, "task_progress", TASK_PROGRESS_COLUMNS)


def downgrade() -> None:
    raise RuntimeError(
        "Downgrade is disabled to avoid deleting persistent progress data"
    )
