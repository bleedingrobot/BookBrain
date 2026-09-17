from pydantic import BaseModel


class PasscodeRequest(BaseModel):
    passcode: str


class ViewerSessionStatus(BaseModel):
    authenticated: bool


class DriveFileOut(BaseModel):
    id: str
    name: str


class SidecarMetaOut(BaseModel):
    id: str
    modifiedTime: str


class SidecarWriteResult(BaseModel):
    id: str


class CopyRequest(BaseModel):
    destinationFolderId: str
