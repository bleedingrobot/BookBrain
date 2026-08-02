import json

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow

from app.core import crypto
from app.core.config import get_settings
from app.core.settings_keys import GOOGLE_OAUTH_SCOPE_MODE, GOOGLE_OAUTH_TOKEN_ENC
from app.data.repositories.settings_repository import SettingsRepository
from app.providers.drive.scopes import scope_for_folder_mode


class AuthService:
    """OAuth flow orchestration. SPEC.md §10: the scope is chosen from the
    folder_mode answer *before* the consent screen is shown, not discovered
    afterward at folder-pick time."""

    def __init__(self) -> None:
        self._pending_states: dict[str, str] = {}

    def _client_config(self) -> dict:
        settings = get_settings()
        return {
            "web": {
                "client_id": settings.google_client_id,
                "client_secret": settings.google_client_secret,
                "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                "token_uri": "https://oauth2.googleapis.com/token",
                "redirect_uris": [settings.google_oauth_redirect_uri],
            }
        }

    def _flow(self, scope: str) -> Flow:
        settings = get_settings()
        flow = Flow.from_client_config(self._client_config(), scopes=[scope])
        flow.redirect_uri = settings.google_oauth_redirect_uri
        return flow

    def start(self, folder_mode: str) -> str:
        scope = scope_for_folder_mode(folder_mode)
        flow = self._flow(scope)
        authorization_url, state = flow.authorization_url(
            access_type="offline",
            prompt="consent",
            include_granted_scopes="false",
        )
        self._pending_states[state] = scope
        return authorization_url

    async def handle_callback(
        self, code: str, state: str, settings_repo: SettingsRepository
    ) -> None:
        scope = self._pending_states.pop(state, None)
        if scope is None:
            raise ValueError("unknown or expired OAuth state")

        flow = self._flow(scope)
        flow.fetch_token(code=code)
        await self._persist(flow.credentials, settings_repo)
        await settings_repo.set(GOOGLE_OAUTH_SCOPE_MODE, scope)

    async def _persist(self, creds: Credentials, settings_repo: SettingsRepository) -> None:
        blob = json.dumps(
            {
                "token": creds.token,
                "refresh_token": creds.refresh_token,
                "token_uri": creds.token_uri,
                "scopes": creds.scopes,
                "expiry": creds.expiry.isoformat() if creds.expiry else None,
            }
        )
        await settings_repo.set(GOOGLE_OAUTH_TOKEN_ENC, crypto.encrypt(blob))

    async def get_credentials(self, settings_repo: SettingsRepository) -> Credentials | None:
        enc = await settings_repo.get(GOOGLE_OAUTH_TOKEN_ENC)
        if enc is None:
            return None

        data = json.loads(crypto.decrypt(enc))
        settings = get_settings()
        creds = Credentials(
            token=data["token"],
            refresh_token=data["refresh_token"],
            token_uri=data["token_uri"],
            client_id=settings.google_client_id,
            client_secret=settings.google_client_secret,
            scopes=data["scopes"],
        )

        if creds.expired and creds.refresh_token:
            creds.refresh(Request())
            await self._persist(creds, settings_repo)

        return creds

    async def status(self, settings_repo: SettingsRepository) -> dict:
        enc = await settings_repo.get(GOOGLE_OAUTH_TOKEN_ENC)
        scope_mode = await settings_repo.get(GOOGLE_OAUTH_SCOPE_MODE)
        return {"connected": enc is not None, "scope_mode": scope_mode}

    async def disconnect(self, settings_repo: SettingsRepository) -> None:
        await settings_repo.delete(GOOGLE_OAUTH_TOKEN_ENC)
        await settings_repo.delete(GOOGLE_OAUTH_SCOPE_MODE)


_auth_service = AuthService()


def get_auth_service() -> AuthService:
    return _auth_service
