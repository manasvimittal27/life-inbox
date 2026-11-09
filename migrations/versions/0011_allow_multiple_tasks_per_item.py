"""Allow one inbox item to be the source of several tasks.

Drops the UNIQUE(item_id) restriction on task_source_items so a single note or
email can produce multiple tasks that each link back to their source. The
composite primary key (task_id, item_id) still prevents duplicate pairs.

Revision ID: 0011_allow_multiple_tasks_per_item
Revises: 0010_add_item_external_id
"""

from typing import Optional, Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0011_allow_multiple_tasks_per_item"
down_revision: Optional[str] = "0010_add_item_external_id"
branch_labels: Optional[Union[str, Sequence[str]]] = None
depends_on: Optional[Union[str, Sequence[str]]] = None


def _target_table(meta: sa.MetaData) -> sa.Table:
    """task_source_items WITHOUT the UNIQUE(item_id) constraint."""
    return sa.Table(
        "task_source_items",
        meta,
        sa.Column(
            "task_id",
            sa.Integer(),
            sa.ForeignKey("tasks.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "item_id",
            sa.Integer(),
            sa.ForeignKey("inbox_items.id", ondelete="CASCADE"),
            primary_key=True,
        ),
    )


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "task_source_items" not in set(inspector.get_table_names()):
        return

    indexes = inspector.get_indexes("task_source_items")

    # Databases built by migration 0006 carry a NAMED unique index, which shows
    # up in get_indexes() and can be dropped directly.
    if any(ix["name"] == "uq_task_source_items_item_id" for ix in indexes):
        op.drop_index(
            "uq_task_source_items_item_id",
            table_name="task_source_items",
        )
        return

    # Databases built by Base.metadata.create_all instead carry an anonymous
    # UNIQUE(item_id) TABLE CONSTRAINT. On SQLite that is an auto-index which
    # get_indexes() hides, so it surfaces only via get_unique_constraints().
    # Either way, if something enforces uniqueness on item_id alone, rebuild the
    # table without it.
    def _is_item_unique(entry) -> bool:
        return list(entry.get("column_names") or []) == ["item_id"] and (
            entry.get("unique", True)
        )

    has_item_unique = any(
        _is_item_unique(u)
        for u in inspector.get_unique_constraints("task_source_items")
    ) or any(_is_item_unique(ix) for ix in indexes if ix.get("unique"))

    if has_item_unique:
        with op.batch_alter_table(
            "task_source_items",
            copy_from=_target_table(sa.MetaData()),
            recreate="always",
        ):
            pass


def downgrade() -> None:
    raise RuntimeError(
        "Downgrade is disabled: multiple tasks per item cannot be safely "
        "collapsed back to one."
    )
