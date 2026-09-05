import pytest
from cryptography.fernet import Fernet
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.config import get_settings
from app.data.db import Base
from app.data import models  # noqa: F401  (registers models on Base.metadata)
from app.jobs.nightly import reset_nightly_lock
from app.services import book_repository
from app.services.metadata_writeback_service import reset_write_lock as reset_metadata_writeback_lock
from app.services.organize_service import get_folder_path_cache
from app.services.series_merge_service import reset_series_merge_write_lock


@pytest.fixture(autouse=True)
def _reset_shared_singletons():
    # Both of these are process-wide in production (a single event loop for
    # the app's whole life), but pytest-asyncio gives each test function its
    # own event loop by default — reusing the same asyncio.Lock across tests
    # raises "bound to a different event loop" the moment a second test's
    # loop actually acquires it. Reset before every test so each gets a
    # cleanly-unbound lock/cache regardless of what earlier tests touched.
    book_repository.reset_book_write_lock()
    get_folder_path_cache().clear()
    reset_series_merge_write_lock()
    reset_nightly_lock()
    reset_metadata_writeback_lock()


@pytest.fixture(autouse=True)
def _configure_test_settings(monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "token_encryption_key", Fernet.generate_key().decode())
    monkeypatch.setattr(settings, "google_client_id", "test-client-id")
    monkeypatch.setattr(settings, "google_client_secret", "test-client-secret")
    monkeypatch.setattr(
        settings, "google_oauth_redirect_uri", "http://localhost:8000/api/auth/callback"
    )


@pytest.fixture
async def db_session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        yield session
    await engine.dispose()
