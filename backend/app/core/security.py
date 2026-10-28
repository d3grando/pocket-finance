"""Token/password primitives.

* Raw tokens never touch the DB: only ``token_hash`` = HMAC-SHA256(SECRET_KEY, token).
* Passwords: argon2id (argon2-cffi defaults = RFC 9106 low-memory profile).
* CSRF token of a cookie session is derived from the session token with HMAC (signed
  double-submit): it can always be re-issued for a live session, and an attacker who cannot read
  the HttpOnly session cookie cannot compute it.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import secrets

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

from app.config import get_settings

SESSION_COOKIE = "pf_session"
CSRF_COOKIE = "pf_csrf"
#: «This browser has logged in successfully before» marker (OWASP device cookie): exempts the
#: browser from the global failed-login cap, never authenticates anything by itself.
KNOWN_DEVICE_COOKIE = "pf_known"
CSRF_HEADER = "X-CSRF-Token"
DEVICE_TOKEN_PREFIX = "pfd_"
SESSION_TOKEN_PREFIX = "pfs_"

#: Minimum length of a new password (API: new_password ≥ 12).
MIN_PASSWORD_LENGTH = 12

_PASSWORD_ALPHABET = "abcdefghijkmnpqrstuvwxyz23456789"  # no l/o/0/1 — easy to retype

_hasher = PasswordHasher()  # argon2id, t=3, m=64 MiB, p=4
# A real hash used to equalize timing when the user does not exist / has no password.
_DUMMY_HASH: str | None = None


def b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def new_token(nbytes: int = 32) -> str:
    """URL-safe random token (secrets.token_urlsafe)."""
    return secrets.token_urlsafe(nbytes)


def new_device_token() -> str:
    """``pfd_`` + 32 random bytes base64url (ARCH §6)."""
    return DEVICE_TOKEN_PREFIX + new_token(32)


def new_session_token() -> str:
    return SESSION_TOKEN_PREFIX + new_token(32)


def _key() -> bytes:
    return get_settings().secret_key.encode("utf-8")


def token_hash(token: str) -> bytes:
    """HMAC-SHA256(SECRET_KEY, token) — what is stored in DB."""
    return hmac.new(_key(), token.encode("utf-8"), hashlib.sha256).digest()


def csrf_token_for(session_token: str) -> str:
    """CSRF token bound to a session token (HMAC with a domain-separation label)."""
    mac = hmac.new(_key(), b"pf-csrf\x00" + session_token.encode("utf-8"), hashlib.sha256)
    return b64url(mac.digest())


def webauthn_user_handle(user_id: int) -> bytes:
    """Opaque, stable WebAuthn user.id (does not reveal the numeric id)."""
    return hmac.new(_key(), f"pf-webauthn-user:{user_id}".encode(), hashlib.sha256).digest()[:16]


def _known_device_sig(nonce: str) -> str:
    mac = hmac.new(_key(), b"pf-known-device\x00" + nonce.encode("utf-8"), hashlib.sha256)
    return b64url(mac.digest())[:32]


def new_known_device_value(nonce: str | None = None) -> str:
    """Signed ``<nonce>.<hmac>`` value of the ``pf_known`` cookie (stateless, no DB)."""
    nonce = nonce or new_token(16)
    return f"{nonce}.{_known_device_sig(nonce)}"


def verify_known_device_value(value: str | None) -> str | None:
    """Nonce of a genuine ``pf_known`` cookie value, else None."""
    if not value or len(value) > 128:
        return None
    nonce, sep, sig = value.partition(".")
    if not sep or not nonce or not sig:
        return None
    if not constant_time_equals(sig, _known_device_sig(nonce)):
        return None
    return nonce


def fingerprint(value: str) -> str:
    """Short non-reversible key for in-memory maps (rate-limit buckets)."""
    return hashlib.sha256(value.encode("utf-8", "replace")).hexdigest()[:24]


def constant_time_equals(a: str | bytes | None, b: str | bytes | None) -> bool:
    if a is None or b is None:
        return False
    if isinstance(a, str):
        a = a.encode("utf-8")
    if isinstance(b, str):
        b = b.encode("utf-8")
    return hmac.compare_digest(a, b)


# ---------------------------------------------------------------- passwords


def hash_password(password: str) -> str:
    """argon2id hash."""
    return _hasher.hash(password)


def verify_password(password_hash: str | None, password: str) -> bool:
    """Constant-ish time verification; a missing hash still burns one argon2 verify."""
    global _DUMMY_HASH
    if not password_hash:
        if _DUMMY_HASH is None:
            _DUMMY_HASH = _hasher.hash(secrets.token_urlsafe(16))
        try:
            _hasher.verify(_DUMMY_HASH, password)
        except (VerificationError, InvalidHashError):
            pass
        return False
    try:
        return _hasher.verify(password_hash, password)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


def password_needs_rehash(password_hash: str) -> bool:
    try:
        return _hasher.check_needs_rehash(password_hash)
    except InvalidHashError:
        return True


async def hash_password_async(password: str) -> str:
    """argon2 is CPU-bound (~50 ms): keep it off the event loop."""
    return await asyncio.to_thread(hash_password, password)


async def verify_password_async(password_hash: str | None, password: str) -> bool:
    return await asyncio.to_thread(verify_password, password_hash, password)


def generate_password(groups: int = 4, group_len: int = 5) -> str:
    """Readable random password, e.g. ``k7m2x-p9qrt-a3dfh-w8ze4`` (~100 bits)."""
    return "-".join(
        "".join(secrets.choice(_PASSWORD_ALPHABET) for _ in range(group_len)) for _ in range(groups)
    )


def check_password_policy(password: str) -> str | None:
    """Return a Russian error message or None."""
    if len(password) < MIN_PASSWORD_LENGTH:
        return f"Пароль должен быть не короче {MIN_PASSWORD_LENGTH} символов"
    if len(password) > 256:
        return "Пароль слишком длинный"
    if password.strip() != password:
        return "Пароль не должен начинаться или заканчиваться пробелом"
    return None
