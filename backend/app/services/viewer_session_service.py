"""The sibling passcode login for the family library-viewer. Deliberately
stateless: `create_session_token` is a Fernet-signed marker with no per-user
identity in it (there are no accounts here, just "knows the household
passcode or doesn't", same trust model as the Drive-sidecar wishlist's
honesty-based `requestedBy`), so there's nothing to look up and nothing to
revoke — an expiring cookie is the whole mechanism. See app/api/deps.py's
require_viewer_session for how this gates the /api/viewer/drive/* proxy.
"""

import secrets

from app.core import crypto
from app.core.config import get_settings

COOKIE_NAME = "bb_viewer_session"

# Not a real credential to verify, just a distinct payload so a decrypted
# token can't be confused with anything else `crypto` might ever encrypt
# with the same key (currently just the Drive OAuth blob).
_PAYLOAD = "bookbrain-viewer-session:v1"


def check_passcode(candidate: str) -> bool:
    expected = get_settings().siblings_passcode
    # A blank configured passcode must never match a blank submission —
    # secrets.compare_digest("", "") is True, which would let the passcode
    # path in even though it was never actually set up.
    if not expected:
        return False
    return secrets.compare_digest(candidate, expected)


def create_session_token() -> str:
    return crypto.encrypt(_PAYLOAD)


def verify_session_token(token: str | None) -> bool:
    if not token:
        return False
    ttl = get_settings().viewer_session_days * 86400
    try:
        payload = crypto.decrypt(token, ttl_seconds=ttl)
    except Exception:  # noqa: BLE001 - expired, tampered, or just garbage
        return False
    return payload == _PAYLOAD
