import os
import bcrypt

# Cost factor for bcrypt - higher is more secure but slower
# 12 is a good balance for most applications
BCRYPT_COST_FACTOR = int(os.environ.get("BCRYPT_COST_FACTOR", "12"))


def hash_password(password: str) -> str:
    """
    Hash a password using bcrypt with salt.

    Args:
        password: The plain text password to hash

    Returns:
        bcrypt hash as a string
    """
    # Generate salt and hash the password
    salt = bcrypt.gensalt(rounds=BCRYPT_COST_FACTOR)
    hashed = bcrypt.hashpw(password.encode("utf-8"), salt)
    return hashed.decode("utf-8")


def verify_password(password: str, hashed: str) -> bool:
    """
    Verify a password against a bcrypt hash.

    Args:
        password: The plain text password to verify
        hashed: The bcrypt hash to verify against

    Returns:
        True if password matches the hash, False otherwise
    """
    try:
        return bcrypt.checkpw(password.encode("utf-8"), hashed.encode("utf-8"))
    except Exception:
        # If there's any error in verification, return False
        return False


def is_hash_valid(hash_str: str) -> bool:
    """
    Check if a string looks like a valid bcrypt hash.

    Args:
        hash_str: String to check

    Returns:
        True if it appears to be a valid bcrypt hash format
    """
    # Bcrypt hashes start with $2b$, $2a$, or $2y$ followed by cost, salt, and hash
    return (
        hash_str.startswith("$2b$")
        or hash_str.startswith("$2a$")
        or hash_str.startswith("$2y$")
    )
