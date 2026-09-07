from datetime import datetime

from pydantic import BaseModel


class ResolveRequest(BaseModel):
    text: str


class ResolvedBook(BaseModel):
    title: str
    author: str | None = None
    series: str | None = None
    series_number: float | None = None
    isbn13: str | None = None
    cover_url: str | None = None
    note: str | None = None


class LibraryMatch(BaseModel):
    file_id: int
    filename: str
    status: str


class ResolveResult(BaseModel):
    found: bool
    resolved: ResolvedBook | None = None
    already_in_library: LibraryMatch | None = None
    already_on_wishlist: bool = False
    note: str | None = None


class WishlistItemCreate(ResolvedBook):
    raw_request: str


class WishlistItemOut(BaseModel):
    id: int
    raw_request: str
    title: str
    author: str | None
    series: str | None
    series_number: float | None
    isbn13: str | None
    cover_url: str | None
    note: str | None
    status: str
    acquired_at: datetime | None
    acquired_file_id: int | None
    created_at: datetime


class WishlistItemUpdate(BaseModel):
    status: str
