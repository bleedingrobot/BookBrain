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
