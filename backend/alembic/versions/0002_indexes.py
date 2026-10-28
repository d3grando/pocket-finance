# ruff: noqa: E501
"""indexes: non-unique secondary indexes (lists, filters, search trigram, GIN aliases).

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-27
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = '0002'
down_revision: str | None = '0001'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_index('ix_currency_rates_pair_date', 'currency_rates', ['base', 'quote', sa.literal_column('rate_date DESC')], unique=False)
    op.create_index('ix_accounts_aliases', 'accounts', ['aliases'], unique=False, postgresql_using='gin')
    op.create_index(op.f('ix_accounts_user_id'), 'accounts', ['user_id'], unique=False)
    op.create_index(op.f('ix_ai_requests_user_id'), 'ai_requests', ['user_id'], unique=False)
    op.create_index('ix_ai_requests_user_time', 'ai_requests', ['user_id', sa.literal_column('created_at DESC')], unique=False)
    op.create_index('ix_categories_aliases', 'categories', ['aliases'], unique=False, postgresql_using='gin')
    op.create_index(op.f('ix_categories_user_id'), 'categories', ['user_id'], unique=False)
    op.create_index(op.f('ix_device_tokens_user_id'), 'device_tokens', ['user_id'], unique=False)
    op.create_index('ix_idempotency_keys_expires', 'idempotency_keys', ['expires_at'], unique=False)
    op.create_index(op.f('ix_idempotency_keys_user_id'), 'idempotency_keys', ['user_id'], unique=False)
    op.create_index(op.f('ix_notifications_outbox_user_id'), 'notifications_outbox', ['user_id'], unique=False)
    op.create_index('ix_outbox_status_available', 'notifications_outbox', ['status', 'available_at'], unique=False)
    op.create_index(op.f('ix_passkeys_user_id'), 'passkeys', ['user_id'], unique=False)
    op.create_index(op.f('ix_preview_tokens_user_id'), 'preview_tokens', ['user_id'], unique=False)
    op.create_index('ix_sessions_active_user', 'sessions', ['user_id'], unique=False, postgresql_where=sa.text('revoked_at IS NULL'))
    op.create_index(op.f('ix_sessions_user_id'), 'sessions', ['user_id'], unique=False)
    op.create_index(op.f('ix_account_assets_user_id'), 'account_assets', ['user_id'], unique=False)
    op.create_index('ix_action_log_undone_by', 'action_log', ['undone_by_action_id'], unique=False)
    op.create_index(op.f('ix_action_log_user_id'), 'action_log', ['user_id'], unique=False)
    op.create_index('ix_action_log_user_time', 'action_log', ['user_id', sa.literal_column('created_at DESC')], unique=False)
    op.create_index('ix_merchants_aliases', 'merchants', ['aliases'], unique=False, postgresql_using='gin')
    op.create_index(op.f('ix_merchants_user_id'), 'merchants', ['user_id'], unique=False)
    op.create_index(op.f('ix_my_accounts_user_id'), 'my_accounts', ['user_id'], unique=False)
    op.create_index('ix_audit_logs_entity', 'audit_logs', ['user_id', 'entity_type', 'entity_id', 'created_at'], unique=False)
    op.create_index(op.f('ix_audit_logs_user_id'), 'audit_logs', ['user_id'], unique=False)
    op.create_index('ix_recurring_due', 'recurring_transactions', ['user_id', 'next_due_on'], unique=False, postgresql_where=sa.text('is_active'))
    op.create_index(op.f('ix_recurring_transactions_user_id'), 'recurring_transactions', ['user_id'], unique=False)
    op.create_index('ix_transactions_merchant', 'transactions', ['merchant_id'], unique=False)
    op.create_index('ix_transactions_needs_account', 'transactions', ['user_id'], unique=False, postgresql_where=sa.text("status = 'needs_account' AND deleted_at IS NULL"))
    op.create_index('ix_transactions_search_trgm', 'transactions', [sa.literal_column("(coalesce(description,'') || ' ' || coalesce(counterparty,'') || ' ' || coalesce(raw_text,'')) gin_trgm_ops")], unique=False, postgresql_using='gin')
    op.create_index('ix_transactions_user_account', 'transactions', ['user_id', 'account_id'], unique=False)
    op.create_index('ix_transactions_user_category_date', 'transactions', ['user_id', 'category_id', 'occurred_on'], unique=False)
    op.create_index('ix_transactions_user_created', 'transactions', ['user_id', sa.literal_column('created_at DESC')], unique=False)
    op.create_index('ix_transactions_user_date', 'transactions', ['user_id', sa.literal_column('occurred_on DESC'), sa.literal_column('id DESC')], unique=False, postgresql_where=sa.text('deleted_at IS NULL'))
    op.create_index(op.f('ix_transactions_user_id'), 'transactions', ['user_id'], unique=False)
    op.create_index('ix_transactions_user_kind_date', 'transactions', ['user_id', 'kind', 'occurred_on'], unique=False)
    op.create_index('ix_postings_account_asset_time', 'postings', ['account_id', 'asset', 'occurred_at'], unique=False)
    op.create_index(op.f('ix_postings_transaction_id'), 'postings', ['transaction_id'], unique=False)
    op.create_index(op.f('ix_postings_user_id'), 'postings', ['user_id'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_postings_user_id'), table_name='postings')
    op.drop_index(op.f('ix_postings_transaction_id'), table_name='postings')
    op.drop_index('ix_postings_account_asset_time', table_name='postings')
    op.drop_index('ix_transactions_user_kind_date', table_name='transactions')
    op.drop_index(op.f('ix_transactions_user_id'), table_name='transactions')
    op.drop_index('ix_transactions_user_date', table_name='transactions', postgresql_where=sa.text('deleted_at IS NULL'))
    op.drop_index('ix_transactions_user_created', table_name='transactions')
    op.drop_index('ix_transactions_user_category_date', table_name='transactions')
    op.drop_index('ix_transactions_user_account', table_name='transactions')
    op.drop_index('ix_transactions_search_trgm', table_name='transactions', postgresql_using='gin')
    op.drop_index('ix_transactions_needs_account', table_name='transactions', postgresql_where=sa.text("status = 'needs_account' AND deleted_at IS NULL"))
    op.drop_index('ix_transactions_merchant', table_name='transactions')
    op.drop_index(op.f('ix_recurring_transactions_user_id'), table_name='recurring_transactions')
    op.drop_index('ix_recurring_due', table_name='recurring_transactions', postgresql_where=sa.text('is_active'))
    op.drop_index(op.f('ix_audit_logs_user_id'), table_name='audit_logs')
    op.drop_index('ix_audit_logs_entity', table_name='audit_logs')
    op.drop_index(op.f('ix_my_accounts_user_id'), table_name='my_accounts')
    op.drop_index(op.f('ix_merchants_user_id'), table_name='merchants')
    op.drop_index('ix_merchants_aliases', table_name='merchants', postgresql_using='gin')
    op.drop_index('ix_action_log_user_time', table_name='action_log')
    op.drop_index(op.f('ix_action_log_user_id'), table_name='action_log')
    op.drop_index('ix_action_log_undone_by', table_name='action_log')
    op.drop_index(op.f('ix_account_assets_user_id'), table_name='account_assets')
    op.drop_index(op.f('ix_sessions_user_id'), table_name='sessions')
    op.drop_index('ix_sessions_active_user', table_name='sessions', postgresql_where=sa.text('revoked_at IS NULL'))
    op.drop_index(op.f('ix_preview_tokens_user_id'), table_name='preview_tokens')
    op.drop_index(op.f('ix_passkeys_user_id'), table_name='passkeys')
    op.drop_index('ix_outbox_status_available', table_name='notifications_outbox')
    op.drop_index(op.f('ix_notifications_outbox_user_id'), table_name='notifications_outbox')
    op.drop_index(op.f('ix_idempotency_keys_user_id'), table_name='idempotency_keys')
    op.drop_index('ix_idempotency_keys_expires', table_name='idempotency_keys')
    op.drop_index(op.f('ix_device_tokens_user_id'), table_name='device_tokens')
    op.drop_index(op.f('ix_categories_user_id'), table_name='categories')
    op.drop_index('ix_categories_aliases', table_name='categories', postgresql_using='gin')
    op.drop_index('ix_ai_requests_user_time', table_name='ai_requests')
    op.drop_index(op.f('ix_ai_requests_user_id'), table_name='ai_requests')
    op.drop_index(op.f('ix_accounts_user_id'), table_name='accounts')
    op.drop_index('ix_accounts_aliases', table_name='accounts', postgresql_using='gin')
    op.drop_index('ix_currency_rates_pair_date', table_name='currency_rates')
