from google.oauth2.credentials import Credentials
from googleapiclient.discovery import Resource, build

FOLDER_MIME_TYPE = "application/vnd.google-apps.folder"


def build_drive_service(creds: Credentials) -> Resource:
    return build("drive", "v3", credentials=creds, cache_discovery=False)
