"""BlobStore on the local filesystem: objects live at root / key and are served under url_prefix."""
from __future__ import annotations

import os
import tempfile
from pathlib import Path

from app.storage.base import BlobStore


class LocalBlobStore(BlobStore):
    def __init__(self, root: Path, url_prefix: str = "/media") -> None:
        self.root = Path(root)
        self.url_prefix = url_prefix

    def path_for(self, key: str) -> Path:
        """The file path for a key (ValueError for unsafe keys). Every other method goes through it."""
        if not key or ".." in key or key.startswith("/") or "\\" in key or "\x00" in key:
            raise ValueError(f"invalid blob key: {key!r}")
        return self.root / key

    def put(self, key: str, data: bytes, content_type: str) -> None:
        path = self.path_for(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".tmp-", suffix=path.suffix)
        try:
            with os.fdopen(fd, "wb") as f:
                f.write(data)
            os.replace(tmp, path)  # readers never see a half-written file
        except BaseException:
            Path(tmp).unlink(missing_ok=True)
            raise

    def url_for(self, key: str) -> str:
        self.path_for(key)
        return f"{self.url_prefix}/{key}"

    def exists(self, key: str) -> bool:
        return self.path_for(key).is_file()

    def delete(self, key: str) -> None:
        self.path_for(key).unlink(missing_ok=True)
