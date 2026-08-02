DRIVE_FILE_SCOPE = "https://www.googleapis.com/auth/drive.file"
DRIVE_SCOPE = "https://www.googleapis.com/auth/drive"

FOLDER_MODES = ("existing", "app_created")


def scope_for_folder_mode(folder_mode: str) -> str:
    """SPEC.md §10: drive.file when the app creates the inbox folder itself
    (it only ever touches files it created), drive when the user points the
    app at a folder it didn't create (app-restricted by app logic, not by
    the OAuth scope itself)."""
    if folder_mode not in FOLDER_MODES:
        raise ValueError(f"folder_mode must be one of {FOLDER_MODES}, got {folder_mode!r}")
    return DRIVE_FILE_SCOPE if folder_mode == "app_created" else DRIVE_SCOPE
