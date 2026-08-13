import httplib2
from google.oauth2.credentials import Credentials
from google_auth_httplib2 import AuthorizedHttp
from googleapiclient.discovery import Resource, build

FOLDER_MIME_TYPE = "application/vnd.google-apps.folder"
EPUB_MIME_TYPE = "application/epub+zip"

# httplib2's default Http() has no timeout at all, so a stalled connection
# (a transient network blip, Drive throttling silently instead of erroring,
# etc.) hangs the calling thread forever. Every call here goes through
# asyncio.to_thread, so a hung request doesn't block the event loop
# directly — but FolderPathCache's lock means one hung folder resolution
# can still stall every other file waiting on that lock, and repeated hung
# calls across retries eventually exhaust the thread pool. A finite timeout
# turns "hangs forever" into "fails after 30s and the caller's own
# error-handling (organize's per-file try/except) takes over."
_HTTP_TIMEOUT_SECONDS = 30


def build_drive_service(creds: Credentials) -> Resource:
    http = AuthorizedHttp(creds, http=httplib2.Http(timeout=_HTTP_TIMEOUT_SECONDS))
    return build("drive", "v3", http=http, cache_discovery=False)
