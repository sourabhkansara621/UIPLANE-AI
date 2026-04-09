"""
auth/jwt_handler.py
-------------------
JWT token handling and password hashing utilities.

This module provides:
- Password hashing and verification using bcrypt
- JWT token creation and decoding
- Token blacklist management for logout functionality

Production Note:
    The in-memory blacklist should be replaced with Redis or similar
    distributed cache in production to support multiple server instances.
"""

from datetime import datetime, timedelta
from typing import Optional, Dict, Any, Set
import bcrypt
from jose import JWTError, jwt
from config.settings import get_settings

settings = get_settings()

# In-memory token blacklist (use Redis in production for multi-instance deployments)
_blacklisted_tokens: Set[str] = set()


def hash_password(plain_password: str) -> str:
    """
    Hash a plain-text password using bcrypt with auto-generated salt.
    
    Args:
        plain_password: The plain-text password to hash
        
    Returns:
        UTF-8 encoded bcrypt hash string suitable for database storage
        
    Note:
        Each call generates a unique salt, so the same password will
        produce different hashes each time (this is expected behavior).
    """
    # Convert password string to bytes for bcrypt
    password_bytes = plain_password.encode("utf-8")
    # Generate a random salt for this password
    salt = bcrypt.gensalt()
    # Hash the password with the salt
    hashed = bcrypt.hashpw(password_bytes, salt)
    # Return as UTF-8 string for database storage
    return hashed.decode("utf-8")


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """
    Verify a plain password against a bcrypt hash.
    
    Args:
        plain_password: The plain-text password to verify
        hashed_password: The bcrypt hash from the database
        
    Returns:
        True if the password matches the hash, False otherwise
        
    Note:
        Returns False on any exception (invalid hash format, etc.)
        to prevent timing attacks and information leakage.
    """
    try:
        # bcrypt.checkpw handles salt extraction and comparison automatically
        return bcrypt.checkpw(
            plain_password.encode("utf-8"),
            hashed_password.encode("utf-8")
        )
    except Exception:
        # Return False for any errors (malformed hash, encoding issues, etc.)
        return False


def create_access_token(
    data: Dict[str, Any],
    expires_delta: Optional[timedelta] = None,
) -> str:
    """
    Create a signed JWT access token with expiration.
    
    Args:
        data: Payload data to encode in the token (user_id, role, etc.)
        expires_delta: Optional custom expiration time. If None, uses
                      the default from settings.jwt_expire_minutes
                      
    Returns:
        Signed JWT token string
        
    Note:
        The token includes:
        - exp: Expiration timestamp
        - iat: Issued-at timestamp
        - All fields from the data dict (sub, username, role, etc.)
    """
    # Create a copy to avoid modifying the original data dict
    payload = data.copy()
    
    # Calculate expiration time (default or custom)
    expire = datetime.utcnow() + (
        expires_delta or timedelta(minutes=settings.jwt_expire_minutes)
    )
    
    # Add standard JWT claims
    payload.update({"exp": expire, "iat": datetime.utcnow()})
    
    # Encode and sign the token
    return jwt.encode(
        payload,
        settings.jwt_secret_key,
        algorithm=settings.jwt_algorithm
    )


def decode_access_token(token: str) -> Dict[str, Any]:
    """
    Decode and verify a JWT access token.
    
    Args:
        token: The JWT token string to decode
        
    Returns:
        Dictionary containing the token payload (sub, username, role, exp, iat, etc.)
        
    Raises:
        JWTError: If the token is invalid, expired, or signature verification fails
        
    Note:
        This function automatically validates:
        - Token signature using the secret key
        - Token expiration (exp claim)
        - Token format and algorithm
    """
    return jwt.decode(
        token,
        settings.jwt_secret_key,
        algorithms=[settings.jwt_algorithm],
    )


def get_token_expiry_seconds() -> int:
    """
    Get the token expiration time in seconds.
    
    Returns:
        Number of seconds until a newly created token expires
        
    Note:
        Used by the login endpoint to inform clients when to refresh tokens.
    """
    return settings.jwt_expire_minutes * 60


def blacklist_token(token: str) -> None:
    """
    Add a token to the blacklist (used for logout).
    
    Args:
        token: The JWT token string to blacklist
        
    Note:
        In production, this should use Redis or a distributed cache
        to ensure blacklisted tokens are recognized across all server instances.
        Tokens remain blacklisted until they naturally expire (based on their exp claim).
    """
    _blacklisted_tokens.add(token)


def is_token_blacklisted(token: str) -> bool:
    """
    Check if a token has been blacklisted (logged out).
    
    Args:
        token: The JWT token string to check
        
    Returns:
        True if the token is blacklisted, False otherwise
        
    Note:
        This check should be performed before accepting any authenticated request
        to ensure logged-out users cannot continue using old tokens.
    """
    return token in _blacklisted_tokens