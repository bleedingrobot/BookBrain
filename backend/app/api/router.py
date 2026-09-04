from fastapi import APIRouter

from app.api.routes import (
    auth,
    drive,
    duplicates,
    files,
    health,
    library,
    library_audit,
    local_scan,
    operations,
    organize,
    reviews,
    scan,
    settings,
    wishlist,
)

api_router = APIRouter(prefix="/api")
api_router.include_router(health.router)
api_router.include_router(scan.router)
api_router.include_router(auth.router)
api_router.include_router(drive.router)
api_router.include_router(organize.router)
api_router.include_router(settings.router)
api_router.include_router(reviews.router)
api_router.include_router(duplicates.router)
api_router.include_router(operations.router)
api_router.include_router(files.router)
api_router.include_router(library.router)
api_router.include_router(library_audit.router)
api_router.include_router(local_scan.router)
api_router.include_router(wishlist.router)
