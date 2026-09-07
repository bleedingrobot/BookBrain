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

# prompts/15 Stage I — optional soft-hold before an auto-eligible file is
# organized. Default "0" == today's exact behaviour (organize the instant a
# file clears the confidence bar). When > 0, a file that cleared the bar waits
# this many hours in `inbox` (measured from `files.discovered_at`) before the
# organize pass will move it — long enough for a human to glance at the
# "Recently auto-organized" tray and correct a rare miss before any Drive move.
# It is a plain WHERE filter, not a queue: a held file simply isn't eligible
# yet and flows on the next organize/nightly pass once its time is up.
ORGANIZE_HOLD_HOURS = "organize_hold_hours"

# Nightly unattended pipeline run (scan -> auto-organize -> covers -> index).
# Off until James turns it on in Settings. Hour is 0-23 in the machine's
# local time.
NIGHTLY_RUN_ENABLED = "nightly_run_enabled"
NIGHTLY_RUN_HOUR = "nightly_run_hour"

# Scheduled DB backup to Drive (backup_service), on its own toggle + hour so
# it can run without the full nightly pipeline. The nightly run also takes a
# backup as its first step — with both on, a same-day backup just replaces the
# earlier file, so it's harmless.
BACKUP_RUN_ENABLED = "backup_run_enabled"
BACKUP_RUN_HOUR = "backup_run_hour"

# Cached Bulk Re-identify Audit report (reident_audit_service). A JSON blob —
# expensive to build (a provider lookup per organised book), so it's stored
# and only regenerated on demand, like the nightly job_runs trail. Carries
# its own generated_at.
REIDENT_REPORT_JSON = "reident_report_json"
