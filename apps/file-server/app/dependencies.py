from fastapi import Request

from app.minio_client import StorageService


def get_storage(request: Request) -> StorageService:
    return request.app.state.storage
