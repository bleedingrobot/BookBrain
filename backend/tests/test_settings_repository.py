from app.data.repositories.settings_repository import SettingsRepository


async def test_set_get_delete_round_trip(db_session) -> None:
    repo = SettingsRepository(db_session)

    assert await repo.get("missing") is None

    await repo.set("k", "v1")
    assert await repo.get("k") == "v1"

    await repo.set("k", "v2")
    assert await repo.get("k") == "v2"

    await repo.delete("k")
    assert await repo.get("k") is None
