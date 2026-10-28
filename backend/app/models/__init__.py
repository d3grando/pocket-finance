"""All ORM models. Import this package to register every table on Base.metadata."""

from app.models.account import Account, AccountAsset, MyAccount
from app.models.base import Base
from app.models.category import Category, Merchant
from app.models.debt import Debt
from app.models.ops import (
    ActionLog,
    AIRequest,
    AuditLog,
    IdempotencyKey,
    NotificationOutbox,
    PreviewToken,
    RecurringTransaction,
)
from app.models.rate import CurrencyRate
from app.models.transaction import Posting, Transaction
from app.models.user import AuthChallenge, DeviceToken, Passkey, Session, User, UserSettings

__all__ = [
    "Account",
    "AccountAsset",
    "ActionLog",
    "AIRequest",
    "AuditLog",
    "AuthChallenge",
    "Base",
    "Category",
    "CurrencyRate",
    "Debt",
    "DeviceToken",
    "IdempotencyKey",
    "Merchant",
    "MyAccount",
    "NotificationOutbox",
    "Passkey",
    "Posting",
    "PreviewToken",
    "RecurringTransaction",
    "Session",
    "Transaction",
    "User",
    "UserSettings",
]
