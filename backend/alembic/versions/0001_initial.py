# ruff: noqa: E501
"""initial: extensions, all tables, PK/FK/UNIQUE/CHECK constraints and unique indexes.

Non-unique secondary indexes (incl. GIN/trigram) are in 0002_indexes.

Revision ID: 0001
Revises: 
Create Date: 2026-10-27
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = '0001'
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
    op.execute("CREATE EXTENSION IF NOT EXISTS citext")
    op.create_table('currency_rates',
    sa.Column('id', sa.BigInteger(), sa.Identity(always=False), nullable=False),
    sa.Column('base', sa.String(length=16), nullable=False),
    sa.Column('quote', sa.String(length=16), nullable=False),
    sa.Column('rate', sa.Numeric(precision=30, scale=12), nullable=False),
    sa.Column('rate_date', sa.Date(), nullable=False),
    sa.Column('kind', sa.String(length=8), server_default='mid', nullable=False),
    sa.Column('source', sa.String(length=16), nullable=False),
    sa.Column('fetched_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("kind IN ('mid', 'sell', 'buy')", name=op.f('ck_currency_rates_kind')),
    sa.CheckConstraint("source IN ('vietcombank', 'cbr', 'coingecko', 'open_er_api', 'manual', 'identity')", name=op.f('ck_currency_rates_source')),
    sa.CheckConstraint('rate > 0', name=op.f('ck_currency_rates_rate_positive')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_currency_rates')),
    sa.UniqueConstraint('base', 'quote', 'rate_date', 'kind', 'source', name='uq_currency_rates_key')
    )
    op.create_table('users',
    sa.Column('id', sa.BigInteger(), sa.Identity(always=False), nullable=False),
    sa.Column('username', postgresql.CITEXT(), nullable=False),
    sa.Column('password_hash', sa.Text(), nullable=True),
    sa.Column('is_active', sa.Boolean(), server_default=sa.text('true'), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_users')),
    sa.UniqueConstraint('username', name=op.f('uq_users_username'))
    )
    op.create_table('accounts',
    sa.Column('id', sa.BigInteger(), sa.Identity(always=False), nullable=False),
    sa.Column('name', sa.String(length=64), nullable=False),
    sa.Column('kind', sa.String(length=16), nullable=False),
    sa.Column('currency', sa.String(length=16), nullable=True),
    sa.Column('is_multi_asset', sa.Boolean(), server_default=sa.text('false'), nullable=False),
    sa.Column('aliases', postgresql.ARRAY(sa.String(length=64)), server_default=sa.text("'{}'"), nullable=False),
    sa.Column('default_for_currencies', postgresql.ARRAY(sa.String(length=16)), server_default=sa.text("'{}'"), nullable=False),
    sa.Column('color', sa.String(length=16), nullable=True),
    sa.Column('icon', sa.String(length=32), nullable=True),
    sa.Column('sort_order', sa.Integer(), server_default=sa.text('0'), nullable=False),
    sa.Column('is_archived', sa.Boolean(), server_default=sa.text('false'), nullable=False),
    sa.Column('version', sa.Integer(), server_default=sa.text('1'), nullable=False),
    sa.Column('deleted_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('user_id', sa.BigInteger(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("kind IN ('bank', 'cash', 'card', 'crypto_wallet', 'exchange', 'other')", name=op.f('ck_accounts_kind')),
    sa.CheckConstraint('is_multi_asset OR currency IS NOT NULL', name=op.f('ck_accounts_currency_required')),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], name=op.f('fk_accounts_user_id_users'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_accounts'))
    )
    op.create_index('uq_accounts_user_name', 'accounts', ['user_id', sa.literal_column('lower(name)')], unique=True, postgresql_where=sa.text('deleted_at IS NULL'))
    op.create_table('ai_requests',
    sa.Column('id', sa.BigInteger(), sa.Identity(always=False), nullable=False),
    sa.Column('source', sa.String(length=12), nullable=False),
    sa.Column('input_text', sa.Text(), nullable=False),
    sa.Column('route', sa.String(length=12), nullable=False),
    sa.Column('model', sa.String(length=64), nullable=True),
    sa.Column('status', sa.String(length=16), nullable=False),
    sa.Column('parser_result', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('llm_request', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('llm_response', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('intents', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('error', sa.Text(), nullable=True),
    sa.Column('prompt_tokens', sa.Integer(), nullable=True),
    sa.Column('completion_tokens', sa.Integer(), nullable=True),
    sa.Column('latency_ms', sa.Integer(), nullable=True),
    sa.Column('user_id', sa.BigInteger(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("route IN ('parser', 'llm', 'parser_llm', 'none')", name=op.f('ck_ai_requests_route')),
    sa.CheckConstraint("status IN ('ok', 'invalid', 'error', 'timeout', 'unavailable', 'clarify')", name=op.f('ck_ai_requests_status')),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], name=op.f('fk_ai_requests_user_id_users'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_ai_requests'))
    )
    op.create_table('auth_challenges',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('user_id', sa.BigInteger(), nullable=True),
    sa.Column('purpose', sa.String(length=16), nullable=False),
    sa.Column('challenge', sa.LargeBinary(), nullable=False),
    sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('used_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("purpose IN ('register','login')", name=op.f('ck_auth_challenges_purpose')),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], name=op.f('fk_auth_challenges_user_id_users'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_auth_challenges'))
    )
    op.create_table('categories',
    sa.Column('id', sa.BigInteger(), sa.Identity(always=False), nullable=False),
    sa.Column('name', sa.String(length=64), nullable=False),
    sa.Column('kind', sa.String(length=8), nullable=False),
    sa.Column('is_business', sa.Boolean(), server_default=sa.text('false'), nullable=False),
    sa.Column('icon', sa.String(length=32), nullable=True),
    sa.Column('color', sa.String(length=16), nullable=True),
    sa.Column('aliases', postgresql.ARRAY(sa.String(length=64)), server_default=sa.text("'{}'"), nullable=False),
    sa.Column('is_system', sa.Boolean(), server_default=sa.text('false'), nullable=False),
    sa.Column('is_new', sa.Boolean(), server_default=sa.text('false'), nullable=False),
    sa.Column('merged_into_id', sa.BigInteger(), nullable=True),
    sa.Column('sort_order', sa.Integer(), server_default=sa.text('0'), nullable=False),
    sa.Column('is_archived', sa.Boolean(), server_default=sa.text('false'), nullable=False),
    sa.Column('version', sa.Integer(), server_default=sa.text('1'), nullable=False),
    sa.Column('user_id', sa.BigInteger(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("kind IN ('expense', 'income')", name=op.f('ck_categories_kind')),
    sa.ForeignKeyConstraint(['merged_into_id'], ['categories.id'], name=op.f('fk_categories_merged_into_id_categories'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], name=op.f('fk_categories_user_id_users'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_categories'))
    )
    op.create_index('uq_categories_user_kind_name', 'categories', ['user_id', 'kind', sa.literal_column('lower(name)')], unique=True, postgresql_where=sa.text('merged_into_id IS NULL'))
    op.create_table('device_tokens',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('name', sa.String(length=100), nullable=False),
    sa.Column('platform', sa.String(length=16), nullable=False),
    sa.Column('token_hash', sa.LargeBinary(), nullable=False),
    sa.Column('last_used_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('last_ip', postgresql.INET(), nullable=True),
    sa.Column('revoked_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('user_id', sa.BigInteger(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("platform IN ('windows','macos','linux')", name=op.f('ck_device_tokens_platform')),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], name=op.f('fk_device_tokens_user_id_users'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_device_tokens')),
    sa.UniqueConstraint('token_hash', name=op.f('uq_device_tokens_token_hash'))
    )
    op.create_table('idempotency_keys',
    sa.Column('key', sa.String(length=128), nullable=False),
    sa.Column('method', sa.String(length=8), nullable=False),
    sa.Column('path', sa.String(length=256), nullable=False),
    sa.Column('request_hash', sa.LargeBinary(), nullable=False),
    sa.Column('state', sa.String(length=12), nullable=False),
    sa.Column('response_status', sa.Integer(), nullable=True),
    sa.Column('response_body', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('user_id', sa.BigInteger(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("state IN ('in_progress', 'done')", name=op.f('ck_idempotency_keys_state')),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], name=op.f('fk_idempotency_keys_user_id_users'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('user_id', 'key', name='pk_idempotency_keys')
    )
    op.create_table('notifications_outbox',
    sa.Column('id', sa.BigInteger(), sa.Identity(always=False), nullable=False),
    sa.Column('channel', sa.String(length=16), server_default='telegram', nullable=False),
    sa.Column('kind', sa.String(length=24), nullable=False),
    sa.Column('payload', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('dedupe_key', sa.String(length=128), nullable=True),
    sa.Column('status', sa.String(length=12), server_default='pending', nullable=False),
    sa.Column('attempts', sa.Integer(), server_default=sa.text('0'), nullable=False),
    sa.Column('available_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('locked_until', sa.DateTime(timezone=True), nullable=True),
    sa.Column('sent_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('error', sa.Text(), nullable=True),
    sa.Column('user_id', sa.BigInteger(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("kind IN ('report_monthly', 'report_daily', 'report_weekly', 'recurring_reminder', 'info')", name=op.f('ck_notifications_outbox_kind')),
    sa.CheckConstraint("status IN ('pending', 'sending', 'sent', 'failed')", name=op.f('ck_notifications_outbox_status')),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], name=op.f('fk_notifications_outbox_user_id_users'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_notifications_outbox')),
    sa.UniqueConstraint('dedupe_key', name=op.f('uq_notifications_outbox_dedupe_key'))
    )
    op.create_table('passkeys',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('credential_id', sa.LargeBinary(), nullable=False),
    sa.Column('public_key', sa.LargeBinary(), nullable=False),
    sa.Column('sign_count', sa.BigInteger(), server_default=sa.text('0'), nullable=False),
    sa.Column('transports', postgresql.ARRAY(sa.String(length=16)), server_default=sa.text("'{}'"), nullable=False),
    sa.Column('aaguid', sa.String(length=36), nullable=True),
    sa.Column('name', sa.String(length=100), nullable=False),
    sa.Column('last_used_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('user_id', sa.BigInteger(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], name=op.f('fk_passkeys_user_id_users'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_passkeys')),
    sa.UniqueConstraint('credential_id', name=op.f('uq_passkeys_credential_id'))
    )
    op.create_table('preview_tokens',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('kind', sa.String(length=16), nullable=False),
    sa.Column('intent', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('affected', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('summary', sa.Text(), nullable=False),
    sa.Column('action_source', sa.String(length=12), nullable=False),
    sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('consumed_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('result_action_id', sa.UUID(), nullable=True),
    sa.Column('user_id', sa.BigInteger(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("kind IN ('bulk_update', 'bulk_delete')", name=op.f('ck_preview_tokens_kind')),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], name=op.f('fk_preview_tokens_user_id_users'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_preview_tokens'))
    )
    op.create_table('sessions',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('token_hash', sa.LargeBinary(), nullable=False),
    sa.Column('csrf_hash', sa.LargeBinary(), nullable=False),
    sa.Column('user_agent', sa.Text(), nullable=True),
    sa.Column('ip', postgresql.INET(), nullable=True),
    sa.Column('last_seen_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('revoked_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('user_id', sa.BigInteger(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], name=op.f('fk_sessions_user_id_users'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_sessions')),
    sa.UniqueConstraint('token_hash', name=op.f('uq_sessions_token_hash'))
    )
    op.create_table('user_settings',
    sa.Column('user_id', sa.BigInteger(), nullable=False),
    sa.Column('timezone', sa.String(length=64), server_default='Asia/Ho_Chi_Minh', nullable=False),
    sa.Column('default_currency', sa.String(length=16), server_default='VND', nullable=False),
    sa.Column('analytics_currency', sa.String(length=16), server_default='USD', nullable=False),
    sa.Column('summary_currency', sa.String(length=16), server_default='VND', nullable=False),
    sa.Column('small_amount_thousands', sa.Boolean(), server_default=sa.text('true'), nullable=False),
    sa.Column('theme', sa.String(length=8), server_default='system', nullable=False),
    sa.Column('desktop_hotkey', sa.String(length=64), server_default='Ctrl+Alt+Space', nullable=False),
    sa.Column('telegram_user_id', sa.BigInteger(), nullable=True),
    sa.Column('report_monthly', sa.Boolean(), server_default=sa.text('true'), nullable=False),
    sa.Column('report_daily', sa.Boolean(), server_default=sa.text('false'), nullable=False),
    sa.Column('report_weekly', sa.Boolean(), server_default=sa.text('false'), nullable=False),
    sa.Column('report_time', sa.Time(), server_default=sa.text("'10:00'"), nullable=False),
    sa.Column('onboarding_done', sa.Boolean(), server_default=sa.text('false'), nullable=False),
    sa.Column('version', sa.Integer(), server_default=sa.text('1'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("theme IN ('light','dark','system')", name=op.f('ck_user_settings_theme')),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], name=op.f('fk_user_settings_user_id_users'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('user_id', name=op.f('pk_user_settings')),
    sa.UniqueConstraint('telegram_user_id', name=op.f('uq_user_settings_telegram_user_id'))
    )
    op.create_table('account_assets',
    sa.Column('id', sa.BigInteger(), sa.Identity(always=False), nullable=False),
    sa.Column('account_id', sa.BigInteger(), nullable=False),
    sa.Column('asset', sa.String(length=16), nullable=False),
    sa.Column('opening_balance', sa.Numeric(precision=28, scale=8), nullable=True),
    sa.Column('opening_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('user_id', sa.BigInteger(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['account_id'], ['accounts.id'], name=op.f('fk_account_assets_account_id_accounts'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], name=op.f('fk_account_assets_user_id_users'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_account_assets')),
    sa.UniqueConstraint('account_id', 'asset', name='uq_account_assets_account_asset')
    )
    op.create_table('action_log',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('source', sa.String(length=12), nullable=False),
    sa.Column('kind', sa.String(length=32), nullable=False),
    sa.Column('summary', sa.Text(), nullable=False),
    sa.Column('input_text', sa.Text(), nullable=True),
    sa.Column('entity_refs', postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'[]'::jsonb"), nullable=False),
    sa.Column('undo_data', postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'[]'::jsonb"), nullable=False),
    sa.Column('is_undoable', sa.Boolean(), server_default=sa.text('true'), nullable=False),
    sa.Column('undone_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('undone_by_action_id', sa.UUID(), nullable=True),
    sa.Column('ai_request_id', sa.BigInteger(), nullable=True),
    sa.Column('idempotency_key', sa.String(length=128), nullable=True),
    sa.Column('request_hash', sa.LargeBinary(), nullable=True),
    sa.Column('response_status', sa.Integer(), nullable=True),
    sa.Column('response', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('user_id', sa.BigInteger(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("kind IN ('create_transaction', 'update_transaction', 'delete_transaction', 'restore_transaction', 'bulk_update', 'bulk_delete', 'transfer', 'exchange', 'adjust_balance', 'assign_account', 'set_opening', 'create_category', 'merge_categories', 'undo')", name=op.f('ck_action_log_kind')),
    sa.CheckConstraint("source IN ('web', 'desktop', 'telegram', 'system')", name=op.f('ck_action_log_source')),
    sa.ForeignKeyConstraint(['ai_request_id'], ['ai_requests.id'], name=op.f('fk_action_log_ai_request_id_ai_requests'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], name=op.f('fk_action_log_user_id_users'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_action_log'))
    )
    op.create_index('uq_action_log_user_idem_key', 'action_log', ['user_id', 'idempotency_key'], unique=True, postgresql_where=sa.text('idempotency_key IS NOT NULL'))
    op.create_table('merchants',
    sa.Column('id', sa.BigInteger(), sa.Identity(always=False), nullable=False),
    sa.Column('name', sa.String(length=128), nullable=False),
    sa.Column('aliases', postgresql.ARRAY(sa.String(length=64)), server_default=sa.text("'{}'"), nullable=False),
    sa.Column('handle', postgresql.CITEXT(), nullable=True),
    sa.Column('category_id', sa.BigInteger(), nullable=True),
    sa.Column('default_account_id', sa.BigInteger(), nullable=True),
    sa.Column('hits', sa.Integer(), server_default=sa.text('0'), nullable=False),
    sa.Column('last_used_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('user_id', sa.BigInteger(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['category_id'], ['categories.id'], name=op.f('fk_merchants_category_id_categories'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['default_account_id'], ['accounts.id'], name=op.f('fk_merchants_default_account_id_accounts'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], name=op.f('fk_merchants_user_id_users'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_merchants'))
    )
    op.create_index('uq_merchants_user_handle', 'merchants', ['user_id', 'handle'], unique=True, postgresql_where=sa.text('handle IS NOT NULL'))
    op.create_index('uq_merchants_user_name', 'merchants', ['user_id', sa.literal_column('lower(name)')], unique=True)
    op.create_table('my_accounts',
    sa.Column('id', sa.BigInteger(), sa.Identity(always=False), nullable=False),
    sa.Column('handle', postgresql.CITEXT(), nullable=False),
    sa.Column('label', sa.String(length=64), nullable=False),
    sa.Column('account_id', sa.BigInteger(), nullable=False),
    sa.Column('user_id', sa.BigInteger(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['account_id'], ['accounts.id'], name=op.f('fk_my_accounts_account_id_accounts'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], name=op.f('fk_my_accounts_user_id_users'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_my_accounts')),
    sa.UniqueConstraint('user_id', 'handle', name='uq_my_accounts_user_handle')
    )
    op.create_table('audit_logs',
    sa.Column('id', sa.BigInteger(), sa.Identity(always=False), nullable=False),
    sa.Column('entity_type', sa.String(length=32), nullable=False),
    sa.Column('entity_id', sa.String(length=64), nullable=False),
    sa.Column('operation', sa.String(length=16), nullable=False),
    sa.Column('before', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('after', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('action_id', sa.UUID(), nullable=True),
    sa.Column('source', sa.String(length=12), nullable=False),
    sa.Column('user_id', sa.BigInteger(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("operation IN ('create', 'update', 'delete', 'restore', 'merge')", name=op.f('ck_audit_logs_operation')),
    sa.ForeignKeyConstraint(['action_id'], ['action_log.id'], name=op.f('fk_audit_logs_action_id_action_log'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], name=op.f('fk_audit_logs_user_id_users'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_audit_logs'))
    )
    op.create_table('recurring_transactions',
    sa.Column('id', sa.BigInteger(), sa.Identity(always=False), nullable=False),
    sa.Column('title', sa.String(length=128), nullable=False),
    sa.Column('kind', sa.String(length=8), server_default='expense', nullable=False),
    sa.Column('amount', sa.Numeric(precision=28, scale=8), nullable=True),
    sa.Column('currency', sa.String(length=16), server_default='VND', nullable=False),
    sa.Column('account_id', sa.BigInteger(), nullable=True),
    sa.Column('category_id', sa.BigInteger(), nullable=True),
    sa.Column('merchant_id', sa.BigInteger(), nullable=True),
    sa.Column('description', sa.Text(), nullable=True),
    sa.Column('frequency', sa.String(length=8), nullable=False),
    sa.Column('interval', sa.Integer(), server_default=sa.text('1'), nullable=False),
    sa.Column('day_of_month', sa.SmallInteger(), nullable=True),
    sa.Column('weekday', sa.SmallInteger(), nullable=True),
    sa.Column('month', sa.SmallInteger(), nullable=True),
    sa.Column('next_due_on', sa.Date(), nullable=False),
    sa.Column('remind_time', sa.Time(), server_default=sa.text("'10:00'"), nullable=False),
    sa.Column('is_active', sa.Boolean(), server_default=sa.text('true'), nullable=False),
    sa.Column('last_reminded_on', sa.Date(), nullable=True),
    sa.Column('version', sa.Integer(), server_default=sa.text('1'), nullable=False),
    sa.Column('user_id', sa.BigInteger(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("frequency IN ('daily', 'weekly', 'monthly', 'yearly')", name=op.f('ck_recurring_transactions_frequency')),
    sa.CheckConstraint("kind IN ('expense','income')", name=op.f('ck_recurring_transactions_kind')),
    sa.CheckConstraint('amount IS NULL OR amount > 0', name=op.f('ck_recurring_transactions_amount_positive')),
    sa.CheckConstraint('day_of_month IS NULL OR day_of_month BETWEEN 1 AND 31', name=op.f('ck_recurring_transactions_dom')),
    sa.CheckConstraint('interval >= 1', name=op.f('ck_recurring_transactions_interval_positive')),
    sa.CheckConstraint('month IS NULL OR month BETWEEN 1 AND 12', name=op.f('ck_recurring_transactions_month')),
    sa.CheckConstraint('weekday IS NULL OR weekday BETWEEN 0 AND 6', name=op.f('ck_recurring_transactions_weekday')),
    sa.ForeignKeyConstraint(['account_id'], ['accounts.id'], name=op.f('fk_recurring_transactions_account_id_accounts'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['category_id'], ['categories.id'], name=op.f('fk_recurring_transactions_category_id_categories'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['merchant_id'], ['merchants.id'], name=op.f('fk_recurring_transactions_merchant_id_merchants'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], name=op.f('fk_recurring_transactions_user_id_users'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_recurring_transactions'))
    )
    op.create_table('transactions',
    sa.Column('id', sa.BigInteger(), sa.Identity(always=False), nullable=False),
    sa.Column('kind', sa.String(length=12), nullable=False),
    sa.Column('status', sa.String(length=16), server_default='posted', nullable=False),
    sa.Column('occurred_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('occurred_on', sa.Date(), nullable=False),
    sa.Column('amount', sa.Numeric(precision=28, scale=8), nullable=False),
    sa.Column('currency', sa.String(length=16), nullable=False),
    sa.Column('account_id', sa.BigInteger(), nullable=True),
    sa.Column('account_amount', sa.Numeric(precision=28, scale=8), nullable=True),
    sa.Column('account_asset', sa.String(length=16), nullable=True),
    sa.Column('account_rate', sa.Numeric(precision=30, scale=12), nullable=True),
    sa.Column('is_estimated', sa.Boolean(), server_default=sa.text('false'), nullable=False),
    sa.Column('counter_account_id', sa.BigInteger(), nullable=True),
    sa.Column('counter_amount', sa.Numeric(precision=28, scale=8), nullable=True),
    sa.Column('counter_asset', sa.String(length=16), nullable=True),
    sa.Column('category_id', sa.BigInteger(), nullable=True),
    sa.Column('is_business', sa.Boolean(), server_default=sa.text('false'), nullable=False),
    sa.Column('merchant_id', sa.BigInteger(), nullable=True),
    sa.Column('counterparty', sa.String(length=128), nullable=True),
    sa.Column('description', sa.Text(), nullable=True),
    sa.Column('amount_usd', sa.Numeric(precision=28, scale=8), nullable=True),
    sa.Column('amount_vnd', sa.Numeric(precision=28, scale=8), nullable=True),
    sa.Column('usd_rate', sa.Numeric(precision=30, scale=12), nullable=True),
    sa.Column('rate_date', sa.Date(), nullable=True),
    sa.Column('rate_source', sa.String(length=16), nullable=True),
    sa.Column('refund_of_id', sa.BigInteger(), nullable=True),
    sa.Column('recurring_id', sa.BigInteger(), nullable=True),
    sa.Column('source', sa.String(length=12), nullable=False),
    sa.Column('raw_text', sa.Text(), nullable=True),
    sa.Column('ai_request_id', sa.BigInteger(), nullable=True),
    sa.Column('created_action_id', sa.UUID(), nullable=True),
    sa.Column('version', sa.Integer(), server_default=sa.text('1'), nullable=False),
    sa.Column('deleted_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('user_id', sa.BigInteger(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("kind = 'adjustment' OR account_amount IS NULL OR account_amount > 0", name=op.f('ck_transactions_account_amount_positive')),
    sa.CheckConstraint("kind IN ('expense', 'income', 'refund', 'transfer', 'exchange', 'adjustment')", name=op.f('ck_transactions_kind')),
    sa.CheckConstraint("rate_source IS NULL OR rate_source IN ('vietcombank', 'cbr', 'coingecko', 'open_er_api', 'manual', 'identity')", name=op.f('ck_transactions_rate_source')),
    sa.CheckConstraint("source IN ('web', 'desktop', 'telegram', 'system')", name=op.f('ck_transactions_source')),
    sa.CheckConstraint("status <> 'needs_account' OR account_id IS NULL", name=op.f('ck_transactions_needs_account_no_account')),
    sa.CheckConstraint("status IN ('posted', 'needs_account')", name=op.f('ck_transactions_status')),
    sa.CheckConstraint('amount > 0', name=op.f('ck_transactions_amount_positive')),
    sa.ForeignKeyConstraint(['account_id'], ['accounts.id'], name=op.f('fk_transactions_account_id_accounts')),
    sa.ForeignKeyConstraint(['ai_request_id'], ['ai_requests.id'], name=op.f('fk_transactions_ai_request_id_ai_requests'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['category_id'], ['categories.id'], name=op.f('fk_transactions_category_id_categories')),
    sa.ForeignKeyConstraint(['counter_account_id'], ['accounts.id'], name=op.f('fk_transactions_counter_account_id_accounts')),
    sa.ForeignKeyConstraint(['merchant_id'], ['merchants.id'], name=op.f('fk_transactions_merchant_id_merchants'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['recurring_id'], ['recurring_transactions.id'], name=op.f('fk_transactions_recurring_id_recurring_transactions'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['refund_of_id'], ['transactions.id'], name=op.f('fk_transactions_refund_of_id_transactions'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], name=op.f('fk_transactions_user_id_users'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_transactions'))
    )
    op.create_table('postings',
    sa.Column('id', sa.BigInteger(), sa.Identity(always=False), nullable=False),
    sa.Column('transaction_id', sa.BigInteger(), nullable=False),
    sa.Column('account_id', sa.BigInteger(), nullable=False),
    sa.Column('asset', sa.String(length=16), nullable=False),
    sa.Column('amount', sa.Numeric(precision=28, scale=8), nullable=False),
    sa.Column('occurred_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('user_id', sa.BigInteger(), nullable=False),
    sa.CheckConstraint('amount <> 0', name=op.f('ck_postings_amount_nonzero')),
    sa.ForeignKeyConstraint(['account_id'], ['accounts.id'], name=op.f('fk_postings_account_id_accounts')),
    sa.ForeignKeyConstraint(['transaction_id'], ['transactions.id'], name=op.f('fk_postings_transaction_id_transactions'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], name=op.f('fk_postings_user_id_users'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_postings'))
    )


def downgrade() -> None:
    op.drop_table('postings')
    op.drop_table('transactions')
    op.drop_table('recurring_transactions')
    op.drop_table('audit_logs')
    op.drop_table('my_accounts')
    op.drop_index('uq_merchants_user_name', table_name='merchants')
    op.drop_index('uq_merchants_user_handle', table_name='merchants', postgresql_where=sa.text('handle IS NOT NULL'))
    op.drop_table('merchants')
    op.drop_index('uq_action_log_user_idem_key', table_name='action_log', postgresql_where=sa.text('idempotency_key IS NOT NULL'))
    op.drop_table('action_log')
    op.drop_table('account_assets')
    op.drop_table('user_settings')
    op.drop_table('sessions')
    op.drop_table('preview_tokens')
    op.drop_table('passkeys')
    op.drop_table('notifications_outbox')
    op.drop_table('idempotency_keys')
    op.drop_table('device_tokens')
    op.drop_index('uq_categories_user_kind_name', table_name='categories', postgresql_where=sa.text('merged_into_id IS NULL'))
    op.drop_table('categories')
    op.drop_table('auth_challenges')
    op.drop_table('ai_requests')
    op.drop_index('uq_accounts_user_name', table_name='accounts', postgresql_where=sa.text('deleted_at IS NULL'))
    op.drop_table('accounts')
    op.drop_table('users')
    op.drop_table('currency_rates')
    # extensions pg_trgm/citext are left installed (shared by the database)
