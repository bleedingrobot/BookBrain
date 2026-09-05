GOOGLE_OAUTH_TOKEN_ENC = "google_oauth_token_enc"
GOOGLE_OAUTH_SCOPE_MODE = "google_oauth_scope_mode"
DRIVE_INBOX_FOLDER_ID = "drive_inbox_folder_id"
DRIVE_INBOX_FOLDER_NAME = "drive_inbox_folder_name"
DRIVE_INBOX_FOLDER_CREATED_BY_APP = "drive_inbox_folder_created_by_app"

DRIVE_LIBRARY_FOLDER_ID = "drive_library_folder_id"
DRIVE_LIBRARY_FOLDER_NAME = "drive_library_folder_name"
DRIVE_LIBRARY_FOLDER_CREATED_BY_APP = "drive_library_folder_created_by_app"

# SPEC.md §1: dry-run defaults true until explicitly flipped (Milestone 6a gate).
ORGANIZE_DRY_RUN = "organize_dry_run"

# Nightly unattended pipeline run (scan -> auto-organize -> covers -> index).
# Off until James turns it on in Settings. Hour is 0-23 in the machine's
# local time.
NIGHTLY_RUN_ENABLED = "nightly_run_enabled"
NIGHTLY_RUN_HOUR = "nightly_run_hour"
