# ruff: noqa: E501
"""fk indexes: indexes on foreign-key columns used by ON DELETE checks (wipe, cleanup).

Without them deleting N parent rows does N sequential scans of transactions / audit_logs
(wipe of 18k operations took 36 s).

Revision ID: 0004
Revises: 0003
Create Date: 2026-10-27
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = '0004'
down_revision: str | None = '0003'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_INDEXES = (
    ('ix_transactions_account', 'transactions', 'account_id'),
    ('ix_transactions_counter_account', 'transactions', 'counter_account_id'),
    ('ix_transactions_category', 'transactions', 'category_id'),
    ('ix_transactions_refund_of', 'transactions', 'refund_of_id'),
    ('ix_transactions_recurring', 'transactions', 'recurring_id'),
    ('ix_transactions_ai_request', 'transactions', 'ai_request_id'),
    ('ix_action_log_ai_request', 'action_log', 'ai_request_id'),
    ('ix_audit_logs_action', 'audit_logs', 'action_id'),
)


def upgrade() -> None:
    for name, table, col in _INDEXES:
        op.create_index(name, table, [col], unique=False,
                        postgresql_where=sa.text(f'{col} IS NOT NULL'))


def downgrade() -> None:
    for name, table, _col in reversed(_INDEXES):
        op.drop_index(name, table_name=table, postgresql_where=sa.text(f'{_col} IS NOT NULL'))
