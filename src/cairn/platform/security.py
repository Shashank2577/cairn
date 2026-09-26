"""Secrets: password hashing, opaque tokens, the server key, CSRF tokens and webhook signatures.

Nothing secret is stored as-is. Passwords are scrypt hashes with a per-user salt. API tokens, session
cookies and invitation links are 190+ bit random values, stored only as SHA-256 digests (a fast hash is
the right tool for high-entropy secrets). Per-project webhook secrets are never stored at all: they are
derived on demand from the server key and a per-project nonce, and only their digest is kept.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import os
import re
import secrets
import string
import unicodedata
from pathlib import Path

SCRYPT_N = 2 ** 15
SCRYPT_R = 8
SCRYPT_P = 1
SCRYPT_DKLEN = 32
SALT_BYTES = 16
MIN_PASSWORD = 10
MAX_PASSWORD = 1024

KEY_FILE = "secret.key"
KEY_ENV = "CAIRN_SECRET_KEY"

TOKEN_PREFIX = "cairn_"
TOKEN_RE = re.compile(r"^cairn_([a-z0-9]{8})_([A-Za-z0-9]{40})$")
_ALNUM = string.ascii_letters + string.digits
_LOWER = string.ascii_lowercase + string.digits
_OTP = "abcdefghjkmnpqrstuvwxyz23456789"  # no 0/o, 1/l/i


# ---- passwords -------------------------------------------------------------------------------------------
def _b64(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).decode().rstrip("=")


def _unb64(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def _pw_bytes(password: str) -> bytes:
    return unicodedata.normalize("NFKC", password).encode("utf-8")


def _scrypt(pw: bytes, salt: bytes, n: int, r: int, p: int, dklen: int) -> bytes:
    return hashlib.scrypt(pw, salt=salt, n=n, r=r, p=p, dklen=dklen, maxmem=max(64 << 20, 256 * n * r))


def hash_password(password: str) -> str:
    """``scrypt$N$r$p$salt$hash`` with a fresh 16-byte salt."""
    salt = secrets.token_bytes(SALT_BYTES)
    dk = _scrypt(_pw_bytes(password), salt, SCRYPT_N, SCRYPT_R, SCRYPT_P, SCRYPT_DKLEN)
    return f"scrypt${SCRYPT_N}${SCRYPT_R}${SCRYPT_P}${_b64(salt)}${_b64(dk)}"


def _parse(stored: str | None) -> tuple[int, int, int, bytes, bytes] | None:
    if not stored:
        return None
    parts = stored.split("$")
    if len(parts) != 6 or parts[0] != "scrypt":
        return None
    try:
        n, r, p = int(parts[1]), int(parts[2]), int(parts[3])
        salt, dk = _unb64(parts[4]), _unb64(parts[5])
    except (ValueError, TypeError):
        return None
    if not (2 <= n <= 2 ** 20 and 1 <= r <= 32 and 1 <= p <= 16 and salt and 16 <= len(dk) <= 64):
        return None
    return n, r, p, salt, dk


def verify_password(password: str, stored: str | None) -> bool:
    """Constant-time check. A missing or unusable hash still costs one full scrypt at the current parameters,
    so absent accounts and accounts without a password can't be told apart by timing."""
    parsed = _parse(stored)
    real = parsed is not None and isinstance(password, str) and len(password) <= MAX_PASSWORD
    if not real:
        parsed = (SCRYPT_N, SCRYPT_R, SCRYPT_P, secrets.token_bytes(SALT_BYTES), secrets.token_bytes(SCRYPT_DKLEN))
        password = password[:MAX_PASSWORD] if isinstance(password, str) else ""
    n, r, p, salt, expected = parsed  # type: ignore[misc]
    actual = _scrypt(_pw_bytes(password), salt, n, r, p, len(expected))
    return hmac.compare_digest(actual, expected) and real


def needs_rehash(stored: str | None) -> bool:
    parsed = _parse(stored)
    return parsed is None or parsed[:3] != (SCRYPT_N, SCRYPT_R, SCRYPT_P) or len(parsed[4]) != SCRYPT_DKLEN


def one_time_password() -> str:
    """Readable generated password (~98 bits): ``xxxx-xxxx-xxxx-xxxx-xxxx``."""
    return "-".join("".join(secrets.choice(_OTP) for _ in range(4)) for _ in range(5))


# ---- opaque tokens -----------------------------------------------------------------------------------------
def digest(value: str | bytes) -> str:
    data = value.encode("utf-8") if isinstance(value, str) else value
    return hashlib.sha256(data).hexdigest()


def new_id(prefix: str) -> str:
    return f"{prefix}_{secrets.token_hex(8)}"


def new_opaque_token() -> str:
    return secrets.token_urlsafe(32)


def new_api_token() -> tuple[str, str, str]:
    """Return ``(prefix, secret, full)``; ``full`` is ``cairn_<prefix>_<secret>`` and is shown exactly once."""
    prefix = "".join(secrets.choice(_LOWER) for _ in range(8))
    secret = "".join(secrets.choice(_ALNUM) for _ in range(40))
    return prefix, secret, f"{TOKEN_PREFIX}{prefix}_{secret}"


def parse_api_token(value: str | None) -> tuple[str, str] | None:
    m = TOKEN_RE.match((value or "").strip())
    return (m.group(1), m.group(2)) if m else None


def parse_bearer(header: str | None) -> str | None:
    if not header:
        return None
    scheme, _, value = header.strip().partition(" ")
    if scheme.lower() != "bearer":
        return None
    return value.strip() or None


# ---- server key --------------------------------------------------------------------------------------------
def load_server_key(home: Path) -> bytes:
    """32-byte key from ``CAIRN_SECRET_KEY`` or ``$CAIRN_HOME/secret.key`` (created 0600 on first use)."""
    env = os.environ.get(KEY_ENV)
    if env:
        if len(env) < 32:
            raise ValueError(f"{KEY_ENV} must be at least 32 characters")
        return hashlib.sha256(env.encode("utf-8")).digest()
    path = home / KEY_FILE
    if not path.exists():
        tmp = home / f".{KEY_FILE}.{secrets.token_hex(4)}"
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w") as fh:
            fh.write(secrets.token_bytes(32).hex() + "\n")
        try:
            os.link(tmp, path)  # atomic create-if-absent: a concurrent creator wins cleanly
        except FileExistsError:
            pass
        finally:
            tmp.unlink(missing_ok=True)
    try:
        key = bytes.fromhex(path.read_text().strip())
    except ValueError as exc:
        raise ValueError(f"{path} is not a valid key file") from exc
    if len(key) < 32:
        raise ValueError(f"{path} holds a key shorter than 32 bytes")
    return key


def derive(key: bytes, label: str, *parts: str) -> bytes:
    msg = "\x1f".join((label, *parts)).encode("utf-8")
    return hmac.new(key, msg, hashlib.sha256).digest()


def csrf_token(key: bytes, session_id: str) -> str:
    return _b64(derive(key, "csrf", session_id))


def webhook_secret(key: bytes, project_id: str, nonce: str) -> str:
    return "whsec_" + _b64(derive(key, "webhook", project_id, nonce))


def same(a: str, b: str) -> bool:
    return hmac.compare_digest(a.encode("utf-8", "surrogatepass"), b.encode("utf-8", "surrogatepass"))


def sign_body(secret: str, body: bytes) -> str:
    """The ``X-Hub-Signature-256`` value a forge sends for ``body``."""
    return "sha256=" + hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()


def verify_signature(secret: str, body: bytes, header: str) -> bool:
    """HMAC-SHA256 check for ``sha256=<hex>`` (GitHub, Gitea, Bitbucket) or bare hex (Gitea's own header)."""
    value = (header or "").strip()
    if value[:7].lower() == "sha256=":
        value = value[7:]
    expected = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    return same(expected, value.lower())
