"""Auth housekeeping for the worker."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select

from app.models import AuthChallenge, DeviceToken, IdempotencyKey, Session
from app.services.auth.cleanup import cleanup_auth


async def test_cleanup_auth(session, user):
    now = datetime.now(UTC)
    old = now - timedelta(days=200)
    session.add_all(
        [
            AuthChallenge(purpose="login", challenge=b"x", expires_at=old),
            AuthChallenge(purpose="login", challenge=b"y", expires_at=now + timedelta(minutes=5)),
            Session(user_id=user.id, token_hash=b"a", csrf_hash=b"a", expires_at=old),
            Session(
                user_id=user.id,
                token_hash=b"b",
                csrf_hash=b"b",
                expires_at=now + timedelta(days=10),
                revoked_at=old,
            ),
            Session(
                user_id=user.id,
                token_hash=b"c",
                csrf_hash=b"c",
                expires_at=now + timedelta(days=10),
            ),
            DeviceToken(
                user_id=user.id, name="old", platform="windows", token_hash=b"d", revoked_at=old
            ),
            DeviceToken(user_id=user.id, name="live", platform="windows", token_hash=b"e"),
            IdempotencyKey(
                user_id=user.id,
                key="k1",
                method="POST",
                path="/x",
                request_hash=b"h",
                state="done",
                expires_at=now - timedelta(hours=1),
            ),
            IdempotencyKey(
                user_id=user.id,
                key="k2",
                method="POST",
                path="/x",
                request_hash=b"h",
                state="done",
                expires_at=now + timedelta(hours=1),
            ),
        ]
    )
    await session.flush()
    counts = await cleanup_auth(session, now)
    assert counts == {
        "auth_challenges": 1,
        "sessions": 2,
        "device_tokens": 1,
        "idempotency_keys": 1,
    }
    for model, left in ((AuthChallenge, 1), (Session, 1), (DeviceToken, 1), (IdempotencyKey, 1)):
        assert await session.scalar(select(func.count()).select_from(model)) == left
