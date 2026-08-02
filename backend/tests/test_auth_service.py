import urllib.parse

from app.core import crypto
from app.core.settings_keys import GOOGLE_OAUTH_SCOPE_MODE, GOOGLE_OAUTH_TOKEN_ENC
from app.data.repositories.settings_repository import SettingsRepository
from app.providers.drive.scopes import DRIVE_FILE_SCOPE, DRIVE_SCOPE
from app.services.auth_service import AuthService


def _scope_in_url(url: str) -> str:
    query = urllib.parse.urlparse(url).query
    return urllib.parse.parse_qs(query)["scope"][0]


def test_start_uses_narrow_scope_for_app_created_folder() -> None:
    service = AuthService()
    url = service.start("app_created")
    assert _scope_in_url(url) == DRIVE_FILE_SCOPE


def test_start_uses_broad_scope_for_existing_folder() -> None:
    service = AuthService()
    url = service.start("existing")
    assert _scope_in_url(url) == DRIVE_SCOPE


def test_start_records_pending_state() -> None:
    service = AuthService()
    url = service.start("app_created")
    state = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)["state"][0]
    assert service._pending_states[state] == DRIVE_FILE_SCOPE


async def test_status_reflects_stored_token(db_session) -> None:
    service = AuthService()
    repo = SettingsRepository(db_session)

    status = await service.status(repo)
    assert status == {"connected": False, "scope_mode": None}

    await repo.set(GOOGLE_OAUTH_TOKEN_ENC, crypto.encrypt("{}"))
    await repo.set(GOOGLE_OAUTH_SCOPE_MODE, DRIVE_SCOPE)

    status = await service.status(repo)
    assert status == {"connected": True, "scope_mode": DRIVE_SCOPE}


async def test_disconnect_clears_stored_token(db_session) -> None:
    service = AuthService()
    repo = SettingsRepository(db_session)
    await repo.set(GOOGLE_OAUTH_TOKEN_ENC, crypto.encrypt("{}"))

    await service.disconnect(repo)

    assert await repo.get(GOOGLE_OAUTH_TOKEN_ENC) is None
