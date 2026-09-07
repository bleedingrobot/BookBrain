from pydantic import BaseModel


class BackupResult(BaseModel):
    db_name: str
    total_bytes: int  # the .db.gz + .sql.gz together
    kept: int  # dated snapshots remaining in backups/ after retention
    trashed: int  # old snapshots trashed this run


class BackupInfo(BaseModel):
    name: str
    size_bytes: int
    created_at: str
    view_url: str | None = None
