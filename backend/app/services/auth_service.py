import json
import time

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow

from app.core import crypto
from app.core.config import get_settings
from app.core.settings_keys import (
    DRIVE_INBOX_FOLDER_CREATED_BY_APP,
    DRIVE_INBOX_FOLDER_ID,
    DRIVE_INBOX_FOLDER_NAME,
    DRIVE_LIBRARY_FOLDER_CREATED_BY_APP,
    DRIVE_LIBRARY_FOLDER_ID,
    DRIVE_LIBRARY_FOLDER_NAME,
    GOOGLE_OAUTH_SCOPE_MODE,
    GOOGLE_OAUTH_TOKEN_ENC,
)
from app.data.repositories.settings_repository import SettingsRepository
from app.providers.drive.scopes import scope_for_folder_mode

# An abandoned/incomplete sign-in (closed tab, browser back button, etc.)
# leaves its entry in _pending_states forever otherwise — nothing ever pops
# it except a successful callback for that exact state. A real auth flow
# completes in well under this window; anything older is dead weight.
_PENDING_STATE_TTL_SECONDS = 600


class AuthService:
    """OAuth flow orchestration. SPEC.md §10: the scope is chosen from the
    folder_mode answer *before* the consent screen is shown, not discovered
    afterward at folder-pick time."""

    def __init__(self) -> None:
        # state -> (scope, code_verifier, created_at). The PKCE code_verifier
        # generated for the authorization_url must be reused verbatim in the
        # token exchange — a fresh one there fails with "Missing code
        # verifier". created_at (time.monotonic()) is only for pruning
        # abandoned entries in _prune_pending_states, not part of the OAuth
        # protocol itself.
        self._pending_states: dict[str, tuple[str, str, float]] = {}

    def _prune_pending_states(self) -> None:
        now = time.monotonic()
        expired = [
            state
            for state, (_, _, created_at) in self._pending_states.items()
            if now - created_at > _PENDING_STATE_TTL_SECONDS
        ]
        for state in expired:
            del self._pending_states[state]

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

    def _flow(self, scope: str, code_verifier: str | None = None) -> Flow:
        settings = get_settings()
        flow = Flow.from_client_config(
            self._client_config(), scopes=[scope], code_verifier=code_verifier
        )
        flow.redirect_uri = settings.google_oauth_redirect_uri
        return flow

    def start(self, folder_mode: str) -> str:
        self._prune_pending_states()
        scope = scope_for_folder_mode(folder_mode)
        flow = self._flow(scope)
        authorization_url, state = flow.authorization_url(
            access_type="offline",
            prompt="consent",
            include_granted_scopes="false",
        )
        self._pending_states[state] = (scope, flow.code_verifier, time.monotonic())
        return authorization_url

    async def handle_callback(
        self, code: str, state: str, settings_repo: SettingsRepository
    ) -> None:
        pending = self._pending_states.pop(state, None)
        if pending is None:
            raise ValueError("unknown or expired OAuth state")
        scope, code_verifier, _ = pending

        flow = self._flow(scope, code_verifier=code_verifier)
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
        # A reconnect can point at a different Google account, or the user
        # may deliberately be switching folder modes (app-created vs
        # existing) — either way, folder selections made under the old
        # connection are meaningless (and previously caused the folder
        # picker to flash and immediately hide behind stale settings).
        for key in (
            GOOGLE_OAUTH_TOKEN_ENC,
            GOOGLE_OAUTH_SCOPE_MODE,
            DRIVE_INBOX_FOLDER_ID,
            DRIVE_INBOX_FOLDER_NAME,
            DRIVE_INBOX_FOLDER_CREATED_BY_APP,
            DRIVE_LIBRARY_FOLDER_ID,
            DRIVE_LIBRARY_FOLDER_NAME,
            DRIVE_LIBRARY_FOLDER_CREATED_BY_APP,
        ):
            await settings_repo.delete(key)


_auth_service = AuthService()


def get_auth_service() -> AuthService:
    return _auth_service
