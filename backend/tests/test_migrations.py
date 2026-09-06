"""Schema-drift gate: the Alembic migrations must fully describe models.py.

`alembic/env.py` unconditionally overrides `sqlalchemy.url` from
`get_settings().database_url` (which is `@lru_cache`d), so the clean way to
point it at a throwaway DB is a subprocess with `DATABASE_URL` set.
"""

import os
import subprocess
import sys
from pathlib import Path

import pytest

_BACKEND = Path(__file__).resolve().parents[1]


def _alembic(args: list[str], db_url: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "alembic", *args],
        cwd=_BACKEND,
        env={**os.environ, "DATABASE_URL": db_url},
        capture_output=True,
        text=True,
    )


@pytest.fixture
def temp_db_url(tmp_path) -> str:
    return f"sqlite+aiosqlite:///{tmp_path / 'migrations.db'}"


def test_migrations_match_models(temp_db_url) -> None:
    up = _alembic(["upgrade", "head"], temp_db_url)
    assert up.returncode == 0, up.stdout + up.stderr

    check = _alembic(["check"], temp_db_url)
    assert check.returncode == 0, (
        "alembic check found drift between the models and the migrations:\n"
        + check.stdout
        + check.stderr
    )


def test_migrations_downgrade_one_roundtrips(temp_db_url) -> None:
    assert _alembic(["upgrade", "head"], temp_db_url).returncode == 0
    down = _alembic(["downgrade", "-1"], temp_db_url)
    assert down.returncode == 0, down.stdout + down.stderr
    up = _alembic(["upgrade", "head"], temp_db_url)
    assert up.returncode == 0, up.stdout + up.stderr
    assert _alembic(["check"], temp_db_url).returncode == 0
