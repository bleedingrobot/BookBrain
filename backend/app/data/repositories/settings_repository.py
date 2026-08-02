from sqlalchemy.ext.asyncio import AsyncSession

from app.data.models import Setting


class SettingsRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get(self, key: str) -> str | None:
        row = await self._session.get(Setting, key)
        return row.value if row else None

    async def set(self, key: str, value: str) -> None:
        row = await self._session.get(Setting, key)
        if row is None:
            self._session.add(Setting(key=key, value=value))
        else:
            row.value = value
        await self._session.commit()

    async def delete(self, key: str) -> None:
        row = await self._session.get(Setting, key)
        if row is not None:
            await self._session.delete(row)
            await self._session.commit()
