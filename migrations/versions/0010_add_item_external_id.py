"""Add an external source id to inbox items (e.g. Gmail message id).

Revision ID: 0010_add_item_external_id
Revises: 0009_add_task_link
"""

from typing import Optional, Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0010_add_item_external_id"
down_revision: Optional[str] = "0009_add_task_link"
branch_labels: Optional[Union[str, Sequence[str]]] = None
depends_on: Optional[Union[str, Sequence[str]]] = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if "inbox_items" not in set(inspector.get_table_names()):
        # Fresh databases get the full table (including this column) from
        # Base.metadata.create_all; nothing to migrate here.
        return
    existing = {
        column["name"] for column in inspector.get_columns("inbox_items")
    }
    if "external_id" not in existing:
        op.add_column(
            "inbox_items",
            sa.Column("external_id", sa.Text(), nullable=True),
        )


def downgrade() -> None:
    raise RuntimeError(
        "Downgrade is disabled to avoid deleting persistent inbox data"
    )
