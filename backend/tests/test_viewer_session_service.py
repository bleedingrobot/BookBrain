from app.core.config import get_settings
from app.services import viewer_session_service as svc


def test_check_passcode_rejects_when_not_configured(monkeypatch) -> None:
    monkeypatch.setattr(get_settings(), "siblings_passcode", "")
    assert svc.check_passcode("") is False
    assert svc.check_passcode("anything") is False


def test_check_passcode_matches_exact_value(monkeypatch) -> None:
    monkeypatch.setattr(get_settings(), "siblings_passcode", "siblings")
    assert svc.check_passcode("siblings") is True
    assert svc.check_passcode("wrong") is False
    assert svc.check_passcode("Siblings") is False


def test_create_and_verify_round_trip(monkeypatch) -> None:
    monkeypatch.setattr(get_settings(), "viewer_session_days", 30)
    token = svc.create_session_token()
    assert svc.verify_session_token(token) is True


def test_verify_rejects_missing_or_garbage_token() -> None:
    assert svc.verify_session_token(None) is False
    assert svc.verify_session_token("") is False
    assert svc.verify_session_token("not-a-real-token") is False


def test_verify_rejects_expired_token(monkeypatch) -> None:
    # A token minted when sessions lasted 30 days must not still verify once
    # the configured lifetime has shrunk to something already in the past —
    # the whole point of this being stateless is that "expired" is decided
    # fresh on every check, not baked in at creation time. Fernet's ttl check
    # has whole-second resolution, so this needs a real (if tiny) time gap
    # rather than ttl=0 racing the clock within the same second.
    import time

    monkeypatch.setattr(get_settings(), "viewer_session_days", 30)
    token = svc.create_session_token()
    time.sleep(1.1)

    monkeypatch.setattr(get_settings(), "viewer_session_days", 0)
    assert svc.verify_session_token(token) is False


def test_verify_rejects_a_token_from_a_different_key(monkeypatch) -> None:
    from cryptography.fernet import Fernet

    monkeypatch.setattr(get_settings(), "viewer_session_days", 30)
    token = svc.create_session_token()

    monkeypatch.setattr(get_settings(), "token_encryption_key", Fernet.generate_key().decode())
    assert svc.verify_session_token(token) is False
