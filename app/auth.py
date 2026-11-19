"""Password hashing (PBKDF2-SHA256 from the standard library) and role rules."""
import hashlib
import hmac
import os

ROLES = {
    "salesman": "Salesman",
    "admin": "Factory admin",
    "production": "Production supervisor",
    "accountant": "Accountant / auditor",
    "owner": "Owner",
}

# Where each role lands after login.
HOME = {"salesman": "/m/", "admin": "/orders", "production": "/production", "accountant": "/reports",
        "owner": "/dashboard"}


def hash_password(pw: str) -> str:
    salt = os.urandom(16)
    dk = hashlib.pbkdf2_hmac("sha256", pw.encode(), salt, 200_000)
    return f"pbkdf2${salt.hex()}${dk.hex()}"


def verify_password(pw: str, stored: str) -> bool:
    try:
        _, salt, dk = stored.split("$")
    except ValueError:
        return False
    test = hashlib.pbkdf2_hmac("sha256", pw.encode(), bytes.fromhex(salt), 200_000)
    return hmac.compare_digest(test.hex(), dk)
