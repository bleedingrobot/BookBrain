from datetime import datetime
from typing import Literal

from pydantic import BaseModel


class SimilarNameMember(BaseModel):
    id: int
    name: str
    book_count: int
    file_count: int


class SimilarNameCluster(BaseModel):
    members: list[SimilarNameMember]


class SimilarCoverPair(BaseModel):
    """Two organised files that resolved to different books but whose cover
    thumbnails are within a small perceptual-hash distance — likely the same
    book re-uploaded with rewritten metadata (which sha256 dedup misses)."""

    book_a_id: int
    book_a_title: str
    file_a_name: str
    book_b_id: int
    book_b_title: str
    file_b_name: str
    distance: int


class LibraryAuditResult(BaseModel):
    similar_series: list[SimilarNameCluster]
    similar_authors: list[SimilarNameCluster]
    similar_covers: list[SimilarCoverPair] = []


class DismissClusterRequest(BaseModel):
    kind: Literal["series", "author"]
    member_ids: list[int]


class DismissedClusterInfo(BaseModel):
    id: int
    kind: Literal["series", "author"]
    member_ids: list[int]
    created_at: datetime


class TitleMergeRepairResult(BaseModel):
    books_split: int
    files_moved: int
