"""Minimal software WebAuthn authenticator for tests (ES256, attestation "none").

Builds RegistrationResponseJSON / AuthenticationResponseJSON exactly like a browser + platform
authenticator (Face ID) would, so py_webauthn verifies them for real.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
from typing import Any

import cbor2
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec

FLAG_UP = 0x01
FLAG_UV = 0x04
FLAG_BE = 0x08
FLAG_BS = 0x10
FLAG_AT = 0x40


def b64u(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def b64u_dec(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


class SoftAuthenticator:
    def __init__(self, *, counter: bool = True, aaguid: bytes = b"\x01" * 16) -> None:
        self.key = ec.generate_private_key(ec.SECP256R1())
        self.credential_id = os.urandom(32)
        self.aaguid = aaguid
        self.counter = counter  # False → always 0 like Apple passkeys
        self.sign_count = 0
        self.user_handle: str | None = None

    @property
    def credential_id_b64(self) -> str:
        return b64u(self.credential_id)

    def _cose_public_key(self) -> bytes:
        nums = self.key.public_key().public_numbers()
        return cbor2.dumps(
            {1: 2, 3: -7, -1: 1, -2: nums.x.to_bytes(32, "big"), -3: nums.y.to_bytes(32, "big")}
        )

    @staticmethod
    def _client_data(kind: str, challenge: str, origin: str) -> bytes:
        return json.dumps(
            {"type": kind, "challenge": challenge, "origin": origin, "crossOrigin": False},
            separators=(",", ":"),
        ).encode()

    def create(
        self,
        options: dict[str, Any],
        *,
        origin: str,
        rp_id: str | None = None,
        uv: bool = True,
    ) -> dict[str, Any]:
        """navigator.credentials.create() → RegistrationResponseJSON."""
        rp_id = rp_id or options["rp"]["id"]
        self.user_handle = options["user"]["id"]
        client_data = self._client_data("webauthn.create", options["challenge"], origin)
        flags = FLAG_UP | FLAG_AT | FLAG_BE | FLAG_BS | (FLAG_UV if uv else 0)
        auth_data = (
            hashlib.sha256(rp_id.encode()).digest()
            + bytes([flags])
            + self.sign_count.to_bytes(4, "big")
            + self.aaguid
            + len(self.credential_id).to_bytes(2, "big")
            + self.credential_id
            + self._cose_public_key()
        )
        att_obj = cbor2.dumps({"fmt": "none", "attStmt": {}, "authData": auth_data})
        return {
            "id": self.credential_id_b64,
            "rawId": self.credential_id_b64,
            "type": "public-key",
            "response": {
                "clientDataJSON": b64u(client_data),
                "attestationObject": b64u(att_obj),
                "transports": ["internal", "hybrid"],
            },
            "clientExtensionResults": {},
            "authenticatorAttachment": "platform",
        }

    def get(
        self,
        options: dict[str, Any],
        *,
        origin: str,
        rp_id: str | None = None,
        uv: bool = True,
    ) -> dict[str, Any]:
        """navigator.credentials.get() → AuthenticationResponseJSON."""
        rp_id = rp_id or options["rpId"]
        if self.counter:
            self.sign_count += 1
        client_data = self._client_data("webauthn.get", options["challenge"], origin)
        flags = FLAG_UP | FLAG_BE | FLAG_BS | (FLAG_UV if uv else 0)
        auth_data = (
            hashlib.sha256(rp_id.encode()).digest()
            + bytes([flags])
            + self.sign_count.to_bytes(4, "big")
        )
        signature = self.key.sign(
            auth_data + hashlib.sha256(client_data).digest(), ec.ECDSA(hashes.SHA256())
        )
        return {
            "id": self.credential_id_b64,
            "rawId": self.credential_id_b64,
            "type": "public-key",
            "response": {
                "clientDataJSON": b64u(client_data),
                "authenticatorData": b64u(auth_data),
                "signature": b64u(signature),
                "userHandle": self.user_handle,
            },
            "clientExtensionResults": {},
            "authenticatorAttachment": "platform",
        }
