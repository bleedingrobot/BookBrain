from pydantic import BaseModel


class LibraryExportResult(BaseModel):
    name: str
    url: str
