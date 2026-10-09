"""Answer-key embargo. Today's gold is committed encrypted (plus a public
SHA-256 of the plaintext) and revealed after the embargo, so a model with a
fetch tool can't read today's answers off GitHub mid-benchmark, while anyone
can later verify the revealed key is the one committed on the day."""

from __future__ import annotations

import base64
import hashlib

from cryptography.fernet import Fernet


def _fernet(passphrase: str) -> Fernet:
    key = base64.urlsafe_b64encode(hashlib.sha256(passphrase.encode()).digest())
    return Fernet(key)


def seal(plaintext: bytes, passphrase: str) -> bytes:
    return _fernet(passphrase).encrypt(plaintext)


def unseal(token: bytes, passphrase: str) -> bytes:
    return _fernet(passphrase).decrypt(token)


def commitment(plaintext: bytes) -> str:
    return hashlib.sha256(plaintext).hexdigest()
