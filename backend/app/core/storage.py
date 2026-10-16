"""File storage behind a small interface so S3 can replace the local disk later.

Layout under STORAGE_ROOT:
  public/   served openly (avatars, listing photos)
  private/  served only through authenticated endpoints (ID documents)
"""
from pathlib import Path
from typing import Protocol

from app.core.config import get_settings


class Storage(Protocol):
    def save(self, relative_path: str, data: bytes) -> str: ...
    def read(self, relative_path: str) -> bytes: ...
    def delete(self, relative_path: str) -> None: ...
    def resolve(self, relative_path: str) -> Path: ...


class LocalStorage:
    def __init__(self, root: str):
        self.root = Path(root).resolve()

    def resolve(self, relative_path: str) -> Path:
        path = (self.root / relative_path).resolve()
        if self.root != path and self.root not in path.parents:
            raise ValueError("path escapes storage root")
        return path

    def save(self, relative_path: str, data: bytes) -> str:
        path = self.resolve(relative_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return relative_path

    def read(self, relative_path: str) -> bytes:
        return self.resolve(relative_path).read_bytes()

    def delete(self, relative_path: str) -> None:
        self.resolve(relative_path).unlink(missing_ok=True)


def get_storage() -> Storage:
    return LocalStorage(get_settings().storage_root)
