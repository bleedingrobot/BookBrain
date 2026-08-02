import pytest

from app.providers.drive.scopes import DRIVE_FILE_SCOPE, DRIVE_SCOPE, scope_for_folder_mode


def test_app_created_gets_narrow_scope() -> None:
    assert scope_for_folder_mode("app_created") == DRIVE_FILE_SCOPE


def test_existing_folder_gets_broad_scope() -> None:
    assert scope_for_folder_mode("existing") == DRIVE_SCOPE


def test_invalid_folder_mode_rejected() -> None:
    with pytest.raises(ValueError):
        scope_for_folder_mode("bogus")
