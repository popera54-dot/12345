from __future__ import annotations

import shutil
import threading
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable


MEDIA_EXTS = {".mp3", ".wav", ".flac", ".m4a", ".aac", ".ogg", ".opus", ".mp4", ".mkv", ".webm", ".mov", ".avi", ".m4v"}


def resolve(path: str | Path) -> Path:
    p = Path(path)
    try:
        return p.resolve()
    except OSError:
        return p.absolute()


def under(path: Path, root: Path) -> bool:
    try:
        resolve(path).relative_to(resolve(root))
        return True
    except ValueError:
        return False


def safe_name(value: str) -> str:
    invalid = '<>:/\\|?*"'
    text = "".join("-" if c in invalid or ord(c) < 32 else c for c in value).strip().rstrip(".")
    return text or "Untitled"


@dataclass
class Operation:
    kind: str
    label: str
    old: str = ""
    new: str = ""
    path: str = ""
    payload: dict = field(default_factory=dict)
    id: str = field(default_factory=lambda: uuid.uuid4().hex)
    status: str = "pending"
    error: str = ""


class LibraryModel:
    """Virtual library state. The physical filesystem is never modified by staging."""

    def __init__(self, roots: list[str] | None = None):
        self.roots = [str(resolve(r)) for r in (roots or [])]
        self.pending: list[Operation] = []
        self.history: list[Operation] = []
        self._lock = threading.RLock()

    def allowed(self, path: str | Path) -> bool:
        p = resolve(path)
        return any(p == resolve(r) or under(p, resolve(r)) for r in self.roots)

    def stage(self, op: Operation) -> Operation:
        if op.kind in {"mkdir", "delete", "move", "rename"}:
            target = op.path or op.new or op.old
            if not self.allowed(target):
                raise PermissionError("The path is outside an accessible library")
        with self._lock:
            self.pending.append(op)
        return op

    def undo_last(self) -> Operation | None:
        with self._lock:
            return self.pending.pop() if self.pending else None

    def pending_for(self, path: str | Path) -> str:
        p = resolve(path)
        for op in reversed(self.pending):
            for candidate in (op.path, op.new, op.old):
                if candidate and resolve(candidate) == p:
                    if op.kind == "delete":
                        return "delete"
                    if op.kind in {"move", "rename"}:
                        return "rename"
                    return "pending"
        return ""

    def virtual_entries(self, folder: str | Path) -> list[tuple[Path, str]]:
        """Return a UI view that includes staged mkdir/rename/move/delete changes."""
        folder = resolve(folder)
        visible: dict[str, tuple[Path, str]] = {}
        if folder.exists():
            try:
                for p in folder.iterdir():
                    visible[str(p)] = (p, "")
            except OSError:
                pass

        for op in self.pending:
            if op.kind == "mkdir":
                p = resolve(op.path)
                if resolve(p.parent) == folder:
                    visible[str(p)] = (p, "pending")
            elif op.kind == "delete":
                p = resolve(op.path)
                visible.pop(str(p), None)
            elif op.kind in {"rename", "move"}:
                old = resolve(op.old)
                new = resolve(op.new or op.path)
                visible.pop(str(old), None)
                if resolve(new.parent) == folder:
                    visible[str(new)] = (new, "pending")

        # Apply staged ordering to resolve chains such as A -> B -> C.
        changed = True
        while changed:
            changed = False
            for op in self.pending:
                if op.kind in {"rename", "move"}:
                    old = resolve(op.old)
                    new = resolve(op.new or op.path)
                    if str(old) in visible:
                        value = visible.pop(str(old))
                        visible[str(new)] = (new, "pending")
                        changed = True
        return sorted(visible.values(), key=lambda x: (not x[0].is_dir(), x[0].name.lower()))


class ApplyEngine:
    """Applies staged filesystem operations sequentially and reports per-operation results."""

    def __init__(self, model: LibraryModel):
        self.model = model
        self.cancel_event = threading.Event()

    def cancel(self) -> None:
        self.cancel_event.set()

    def apply(self, progress: Callable[[Operation, int, int], None] | None = None) -> list[Operation]:
        with self.model._lock:
            batch = list(self.model.pending)
        results: list[Operation] = []
        total = len(batch)
        for index, op in enumerate(batch, 1):
            if self.cancel_event.is_set():
                op.status = "cancelled"
                op.error = "Cancelled by user"
                results.append(op)
                continue
            try:
                self._one(op)
                op.status = "success"
                op.error = ""
            except Exception as exc:
                op.status = "failed"
                op.error = str(exc)
            results.append(op)
            if progress:
                progress(op, index, total)
        return results

    def _one(self, op: Operation) -> None:
        if op.kind == "mkdir":
            Path(op.path).mkdir(parents=False, exist_ok=False)
        elif op.kind == "rename":
            Path(op.old).rename(op.new)
        elif op.kind == "move":
            Path(op.old).rename(op.new or op.path)
        elif op.kind == "delete":
            p = Path(op.path)
            if p.is_dir():
                shutil.rmtree(p)
            else:
                p.unlink()
        elif op.kind == "download":
            self._download(op.payload)
        else:
            raise ValueError(f"Unknown operation: {op.kind}")

    @staticmethod
    def _download(payload: dict) -> None:
        # Source-specific extraction is isolated here so the application UI/state does not depend on it.
        import imageio_ffmpeg
        import yt_dlp
        target = resolve(payload["target"])
        if not target.exists():
            target.mkdir(parents=True, exist_ok=True)
        title = safe_name(payload["title"])
        out = str(target / f"{title}.%(ext)s")
        ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
        common = {"outtmpl": out, "ffmpeg_location": ffmpeg, "noplaylist": True, "quiet": True, "no_warnings": True}
        if payload["format"] == "MP3":
            common.update({
                "format": "bestaudio/best",
                "postprocessors": [{"key": "FFmpegExtractAudio", "preferredcodec": "mp3", "preferredquality": "192"}],
            })
        else:
            common.update({"format": "bestvideo+bestaudio/best", "merge_output_format": "mp4"})
        with yt_dlp.YoutubeDL(common) as ydl:
            ydl.download([payload["url"]])
