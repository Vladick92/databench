"""The logged-in user's file manager (list, upload, delete); the data agent reads the same files via tools/scope.py."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, UploadFile

from . import config
from .agents.data_agent.tools.connectors import read_table_bytes
from .auth import current_user
from .userfiles import UserFiles, get_user_files, table_file_name

router = APIRouter(prefix="/files", tags=["files"])


@router.get("")
def list_files(username: str = Depends(current_user), files: UserFiles = Depends(get_user_files)) -> list[dict]:
    return [{"name": f.name, "size": f.size} for f in files.list_tables(username)]


@router.post("")
def upload_file(file: UploadFile, username: str = Depends(current_user), files: UserFiles = Depends(get_user_files)) -> dict:
    try:
        name = table_file_name(file.filename)
    except ValueError as e:
        raise HTTPException(400, str(e)) from None
    limit = config.MAX_UPLOAD_MB * 1024 * 1024
    data = file.file.read(limit + 1)
    if len(data) > limit:
        raise HTTPException(413, f"Files can be at most {config.MAX_UPLOAD_MB} MB.")
    try:  # a file the agent can't read would sit in the manager, invisible to every question
        read_table_bytes(name, data, nrows=1)
    except Exception as e:
        raise HTTPException(400, f"{name} can't be read as a table ({type(e).__name__}: {str(e)[:150]}).") from None
    files.write(username, name, data)
    return {"name": name, "size": len(data)}


@router.delete("/{name}")
def delete_file(name: str, username: str = Depends(current_user), files: UserFiles = Depends(get_user_files)) -> dict:
    try:
        name = table_file_name(name)
    except ValueError:
        raise HTTPException(404, f"No file named '{name}'.") from None
    if not files.delete(username, name):
        raise HTTPException(404, f"No file named '{name}'.")
    return {"ok": True}
