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


class LibraryAuditResult(BaseModel):
    similar_series: list[SimilarNameCluster]
    similar_authors: list[SimilarNameCluster]


class DismissClusterRequest(BaseModel):
    kind: Literal["series", "author"]
    member_ids: list[int]


class DismissedClusterInfo(BaseModel):
    id: int
    kind: Literal["series", "author"]
    member_ids: list[int]
    created_at: datetime
