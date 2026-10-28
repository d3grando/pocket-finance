"""Account API schemas."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import Field

from app.schemas.common import Aliases, Color, CurrencyCode, Icon, Id, Money, SafeDateTime, Schema
from app.schemas.transactions import CleanInput

AccountKind = Literal["bank", "cash", "card", "crypto_wallet", "exchange", "other"]
#: output only: «debt» service accounts (docs/DEBTS.md) are created by the debts service, never
#: via POST /accounts, and are not listed by /accounts unless include_debts=true
AccountKindOut = Literal["bank", "cash", "card", "crypto_wallet", "exchange", "other", "debt"]


class AssetBalanceOut(Schema):
    asset: CurrencyCode
    opening_balance: Money | None
    opening_at: datetime | None
    balance: Money | None = Field(description="null = остаток не задан")
    approx_usd: Money | None = None
    approx_vnd: Money | None = None


class AccountOut(Schema):
    id: int
    name: str
    kind: AccountKindOut
    currency: CurrencyCode | None
    is_multi_asset: bool
    aliases: list[str]
    default_for_currencies: list[str]
    color: str | None = None
    icon: str | None = None
    sort_order: int
    is_archived: bool
    version: int
    assets: list[AssetBalanceOut] = Field(default_factory=list)


class AccountCreate(CleanInput):
    name: str = Field(min_length=1, max_length=64)
    kind: AccountKind
    currency: CurrencyCode | None = None
    is_multi_asset: bool = False
    aliases: Aliases = Field(default_factory=list)
    default_for_currencies: list[CurrencyCode] = Field(default_factory=list, max_length=50)
    color: Color | None = None
    icon: Icon | None = None
    opening_balance: Money | None = None


class AccountPatch(CleanInput):
    version: int
    name: str | None = Field(default=None, min_length=1, max_length=64)
    kind: AccountKind | None = None
    aliases: Aliases | None = None
    default_for_currencies: list[CurrencyCode] | None = Field(default=None, max_length=50)
    color: Color | None = None
    icon: Icon | None = None
    sort_order: int | None = Field(default=None, ge=-(2**31), lt=2**31)
    is_archived: bool | None = None


class OpeningBalanceIn(CleanInput):
    """Set/clear the opening balance of one asset. ``amount`` None → «не задан»."""

    asset: CurrencyCode
    amount: Money | None
    opening_at: SafeDateTime | None = None


class OnboardingItem(CleanInput):
    account_id: Id
    asset: CurrencyCode
    amount: Money


class OnboardingBalancesIn(CleanInput):
    """First-run screen: any account may be skipped (omitted)."""

    items: list[OnboardingItem] = Field(max_length=200)


class BalancesOut(Schema):
    accounts: list[AccountOut]
    total_vnd: Money | None = Field(description="≈ sum of known balances, display only")
    total_usd: Money | None = None
    unknown_count: int = 0


class MyAccountOut(Schema):
    id: int
    handle: str
    label: str
    account_id: int


class MyAccountCreate(CleanInput):
    handle: str = Field(min_length=1, max_length=64, pattern=r"^@?[A-Za-z0-9_]{1,64}$")
    label: str | None = Field(default=None, max_length=64)
    account_id: Id | None = Field(default=None, description="None → create a wallet")
