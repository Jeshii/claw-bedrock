import os
import base64
import secrets
from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC


def _derive_key_from_password(password: bytes, salt: bytes) -> bytes:
    """Derive a Fernet-compatible key from a password using PBKDF2."""
    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=32,
        salt=salt,
        iterations=480_000,
    )
    return base64.urlsafe_b64encode(kdf.derive(password))


def _get_encryption_key() -> bytes:
    """
    Return a Fernet-compatible encryption key.

    Priority:
      1. ENCRYPTION_KEY env var — must be a 32-byte URL-safe base64 string
         (generate one with: python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())")
      2. ENCRYPTION_PASSWORD env var — derived via PBKDF2 with a random salt stored
         alongside the encrypted data (salt is generated once and persisted)
      3. Development fallback — a deterministic key derived from a hardcoded password.
         Prints a warning. NOT suitable for production.
    """
    # Option 1: Direct Fernet key from environment
    key_b64 = os.environ.get("ENCRYPTION_KEY")
    if key_b64:
        try:
            key = key_b64.encode() if isinstance(key_b64, str) else key_b64
            # Validate it's a proper Fernet key
            Fernet(key)
            return key
        except Exception:
            raise ValueError(
                "ENCRYPTION_KEY is set but is not a valid Fernet key. "
                'Generate one with: python -c "from cryptography.fernet import Fernet; '
                'print(Fernet.generate_key().decode())"'
            )

    # Option 2: Derive from a password
    password = os.environ.get("ENCRYPTION_PASSWORD")
    if password:
        salt_path = os.path.join(
            os.environ.get("CONFIG_DIR", "/app"), ".encryption_salt"
        )
        if os.path.exists(salt_path):
            salt = open(salt_path, "rb").read()
        else:
            salt = secrets.token_bytes(16)
            os.makedirs(os.path.dirname(salt_path), exist_ok=True)
            with open(salt_path, "wb") as f:
                f.write(salt)
            os.chmod(salt_path, 0o600)
        return _derive_key_from_password(password.encode("utf-8"), salt)

    # Option 3: Development fallback (NOT for production)
    import warnings

    warnings.warn(
        "Using development encryption key. Set ENCRYPTION_KEY or ENCRYPTION_PASSWORD in production!",
        stacklevel=2,
    )
    return _derive_key_from_password(b"claw-bedrock-dev-only", b"fixed-dev-salt")


def encrypt_data(data: str) -> str:
    """Encrypt a string using Fernet symmetric encryption."""
    if not data:
        return data
    f = Fernet(_get_encryption_key())
    return f.encrypt(data.encode("utf-8")).decode("utf-8")


def decrypt_data(token: str) -> str:
    """Decrypt a Fernet-encrypted string. Returns original on failure for backward compat."""
    if not token:
        return token
    try:
        f = Fernet(_get_encryption_key())
        return f.decrypt(token.encode("utf-8")).decode("utf-8")
    except (InvalidToken, Exception):
        return token
