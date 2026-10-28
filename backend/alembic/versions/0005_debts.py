# ruff: noqa: E501
"""debts: debts table, transactions.debt_op, debt-reminder settings, widened CHECKs.

Additive only (docs/DEBTS.md §3): new table + nullable/defaulted columns; CHECK constraints are
widened (every existing row stays valid). Existing operations are not touched.

Revision ID: 0005
Revises: 0004
Create Date: 2026-10-27
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = '0005'
down_revision: str | None = '0004'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_ACC_OLD = "kind IN ('bank', 'cash', 'card', 'crypto_wallet', 'exchange', 'other')"
_ACC_NEW = "kind IN ('bank', 'cash', 'card', 'crypto_wallet', 'exchange', 'other', 'debt')"
_ACT_BASE = ("'create_transaction', 'update_transaction', 'delete_transaction', 'restore_transaction', "
             "'bulk_update', 'bulk_delete', 'transfer', 'exchange', 'adjust_balance', 'assign_account', "
             "'set_opening', 'create_category', 'merge_categories'")
_ACT_OLD = f"kind IN ({_ACT_BASE}, 'undo')"
_ACT_NEW = f"kind IN ({_ACT_BASE}, 'debt', 'undo')"
_OUT_OLD = "kind IN ('report_monthly', 'report_daily', 'report_weekly', 'recurring_reminder', 'info')"
_OUT_NEW = "kind IN ('report_monthly', 'report_daily', 'report_weekly', 'recurring_reminder', 'debt_reminder', 'info')"
_NA_OLD = "status <> 'needs_account' OR account_id IS NULL"
_NA_NEW = "status <> 'needs_account' OR account_id IS NULL OR debt_op IN ('repay_in','borrow')"
_DEBT_OP = "debt_op IS NULL OR debt_op IN ('lend', 'repay_in', 'borrow', 'repay_out', 'forgive')"


def _recheck(name: str, table: str, sql: str) -> None:
    op.drop_constraint(op.f(name), table, type_='check')
    op.create_check_constraint(op.f(name), table, sql)


def upgrade() -> None:
    op.create_table(
        'debts',
        sa.Column('id', sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column('account_id', sa.BigInteger(), nullable=False),
        sa.Column('name', sa.String(length=64), nullable=False),
        sa.Column('name_key', sa.String(length=64), nullable=False),
        sa.Column('aliases', postgresql.ARRAY(sa.String(length=64)), server_default=sa.text("'{}'"), nullable=False),
        sa.Column('handle', postgresql.CITEXT(), nullable=True),
        sa.Column('note', sa.Text(), nullable=True),
        sa.Column('is_archived', sa.Boolean(), server_default=sa.text('false'), nullable=False),
        sa.Column('remind_snoozed_until', sa.Date(), nullable=True),
        sa.Column('last_reminded_on', sa.Date(), nullable=True),
        sa.Column('version', sa.Integer(), server_default=sa.text('1'), nullable=False),
        sa.Column('user_id', sa.BigInteger(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['account_id'], ['accounts.id'], name=op.f('fk_debts_account_id_accounts'), ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], name=op.f('fk_debts_user_id_users'), ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_debts')),
        sa.UniqueConstraint('account_id', name=op.f('uq_debts_account_id')),
    )
    op.create_index('ix_debts_aliases', 'debts', ['aliases'], unique=False, postgresql_using='gin')
    op.create_index(op.f('ix_debts_user_id'), 'debts', ['user_id'], unique=False)
    op.create_index('ix_debts_user_name_key', 'debts', ['user_id', 'name_key'], unique=False)
    op.create_index('uq_debts_user_handle', 'debts', ['user_id', 'handle'], unique=True,
                    postgresql_where=sa.text('handle IS NOT NULL'))
    op.create_index('uq_debts_user_name', 'debts', ['user_id', sa.literal_column('lower(name)')], unique=True)

    op.add_column('transactions', sa.Column('debt_op', sa.String(length=12), nullable=True))
    op.create_check_constraint(op.f('ck_transactions_debt_op'), 'transactions', _DEBT_OP)
    _recheck('ck_transactions_needs_account_no_account', 'transactions', _NA_NEW)

    op.add_column('user_settings', sa.Column('debt_reminders_enabled', sa.Boolean(), server_default=sa.text('false'), nullable=False))
    op.add_column('user_settings', sa.Column('debt_reminder_days', sa.SmallInteger(), server_default=sa.text('30'), nullable=False))
    op.create_check_constraint(op.f('ck_user_settings_debt_reminder_days'), 'user_settings', 'debt_reminder_days BETWEEN 1 AND 365')

    _recheck('ck_accounts_kind', 'accounts', _ACC_NEW)
    _recheck('ck_action_log_kind', 'action_log', _ACT_NEW)
    _recheck('ck_notifications_outbox_kind', 'notifications_outbox', _OUT_NEW)


def downgrade() -> None:
    bind = op.get_bind()
    used = bind.execute(sa.text(
        "SELECT (SELECT count(*) FROM debts) + (SELECT count(*) FROM transactions WHERE debt_op IS NOT NULL)"
        " + (SELECT count(*) FROM accounts WHERE kind = 'debt')"
        " + (SELECT count(*) FROM action_log WHERE kind = 'debt')"
    )).scalar()
    if used:
        raise RuntimeError('0005 downgrade refused: debt data exists (would be lost)')
    op.execute("DELETE FROM notifications_outbox WHERE kind = 'debt_reminder'")
    _recheck('ck_notifications_outbox_kind', 'notifications_outbox', _OUT_OLD)
    _recheck('ck_action_log_kind', 'action_log', _ACT_OLD)
    _recheck('ck_accounts_kind', 'accounts', _ACC_OLD)

    op.drop_constraint(op.f('ck_user_settings_debt_reminder_days'), 'user_settings', type_='check')
    op.drop_column('user_settings', 'debt_reminder_days')
    op.drop_column('user_settings', 'debt_reminders_enabled')

    _recheck('ck_transactions_needs_account_no_account', 'transactions', _NA_OLD)
    op.drop_constraint(op.f('ck_transactions_debt_op'), 'transactions', type_='check')
    op.drop_column('transactions', 'debt_op')

    op.drop_index('uq_debts_user_name', table_name='debts')
    op.drop_index('uq_debts_user_handle', table_name='debts', postgresql_where=sa.text('handle IS NOT NULL'))
    op.drop_index('ix_debts_user_name_key', table_name='debts')
    op.drop_index(op.f('ix_debts_user_id'), table_name='debts')
    op.drop_index('ix_debts_aliases', table_name='debts', postgresql_using='gin')
    op.drop_table('debts')
