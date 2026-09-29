"""File management endpoints for the UI's upload/list/delete panel.

Separate from the data agent's own list_data_sources tool: that one describes schemas for the
LLM (columns, dtypes, which tool reads them); this one is the plain human-facing CRUD surface
the UI's file manager talks to. Both end up reading the same blob container.
"""
from __future__ import annotations

from azure.core.exceptions import ResourceNotFoundError
from azure.identity import DefaultAzureCredential
from azure.storage.blob import BlobServiceClient
from fastapi import APIRouter, HTTPException, UploadFile

from . import config

router = APIRouter(prefix="/files", tags=["files"])

_ALLOWED_SUFFIXES = (".csv", ".xlsx", ".xls")


def _container():
    if not config.AZURE_STORAGE_ACCOUNT_URL:
        raise HTTPException(
            status_code=503,
            detail="Blob storage is not configured (AZURE_STORAGE_ACCOUNT_NAME is unset).",
        )
    client = BlobServiceClient(
        account_url=config.AZURE_STORAGE_ACCOUNT_URL, credential=DefaultAzureCredential()
    )
    return client.get_container_client(config.AZURE_STORAGE_CONTAINER_NAME)


@router.get("")
async def list_files() -> list[dict]:
    container = _container()
    return [{"name": b.name, "size": b.size} for b in container.list_blobs()]


@router.post("")
async def upload_file(file: UploadFile) -> dict:
    if not file.filename.lower().endswith(_ALLOWED_SUFFIXES):
        raise HTTPException(status_code=400, detail=f"Only {_ALLOWED_SUFFIXES} files are accepted.")
    container = _container()
    data = await file.read()
    container.upload_blob(name=file.filename, data=data, overwrite=True)
    return {"name": file.filename, "size": len(data)}


@router.delete("/{name}")
async def delete_file(name: str) -> dict:
    container = _container()
    try:
        container.delete_blob(name)
    except ResourceNotFoundError:
        raise HTTPException(status_code=404, detail=f"No file named '{name}'.")
    return {"ok": True}
