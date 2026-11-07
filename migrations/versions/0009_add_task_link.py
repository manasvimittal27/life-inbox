"""Add an optional meeting/call link to tasks.

Revision ID: 0009_add_task_link
Revises: 0008_create_task_progress
"""

from typing import Optional, Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0009_add_task_link"
down_revision: Optional[str] = "0008_create_task_progress"
branch_labels: Optional[Union[str, Sequence[str]]] = None
depends_on: Optional[Union[str, Sequence[str]]] = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if "tasks" not in set(inspector.get_table_names()):
        return
    existing = {column["name"] for column in inspector.get_columns("tasks")}
    if "link" not in existing:
        op.add_column("tasks", sa.Column("link", sa.Text(), nullable=True))


def downgrade() -> None:
    raise RuntimeError(
        "Downgrade is disabled to avoid deleting persistent task data"
    )
