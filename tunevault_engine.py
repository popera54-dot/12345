from __future__ import annotations

import shutil
import threading
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

AUDIO_EXTENSIONS = {'.mp3', '.wav', '.flac', '.m4a', '.aac', '.ogg', '.opus'}
VIDEO_EXTENSIONS = {'.mp4', '.mkv', '.webm', '.mov', '.avi', '.m4v'}
MEDIA_EXTENSIONS = AUDIO_EXTENSIONS | VIDEO_EXTENSIONS


def resolve(path: str | Path) -> Path:
    p = Path(path)
    try:
        return p.resolve()
    except OSError:
        return p.absolute()


def is_inside(path: str | Path, root: str | Path) -> bool:
    try:
        resolve(path).relative_to(resolve(root))
        return True
    except ValueError:
        return False


def safe_name(value: str) -> str:
    invalid = '<>:"/\\|?*'
    text = ''.join('-' if ord(ch) < 32 or ch in invalid else ch for ch in str(value))
    text = text.strip().rstrip('.')
    reserved = {'CON', 'PRN', 'AUX', 'NUL'} | {f'COM{i}' for i in range(1, 10)} | {f'LPT{i}' for i in range(1, 10)}
    if text.upper() in reserved:
        text = '_' + text
    return text or 'ללא שם'


@dataclass
class MediaPayload:
    url: str
    title: str
    target: str
    format: str = 'MP3'
    source_title: str = ''
    thumbnail: str = ''
    duration: int = 0

    def normalized_format(self) -> str:
        return 'MP4' if self.format.upper().strip() == 'MP4' else 'MP3'

    def final_path(self) -> Path:
        extension = '.mp4' if self.normalized_format() == 'MP4' else '.mp3'
        return resolve(self.target) / f'{safe_name(self.title)}{extension}'


@dataclass
class Operation:
    kind: str
    label: str
    old: str = ''
    new: str = ''
    path: str = ''
    payload: Optional[MediaPayload] = None
    id: str = field(default_factory=lambda: uuid.uuid4().hex)
    status: str = 'pending'
    error: str = ''
    progress: int = 0


class LibraryModel:
    def __init__(self, roots: Optional[list[str]] = None):
        self.roots = [str(resolve(root)) for root in (roots or [])]
        self.pending: list[Operation] = []
        self.history: list[Operation] = []
        self._lock = threading.RLock()

    def allowed(self, path: str | Path) -> bool:
        p = resolve(path)
        return any(p == resolve(root) or is_inside(p, root) for root in self.roots)

    def stage(self, operation: Operation) -> Operation:
        candidates = []
        if operation.path:
            candidates.append(operation.path)
        if operation.old:
            candidates.append(operation.old)
        if operation.new:
            candidates.append(operation.new)
        if operation.payload:
            candidates.append(operation.payload.target)
        for path in candidates:
            if not self.allowed(path):
                raise PermissionError('הפעולה מחוץ לתיקיות שהורשו ל-TuneVault.')
        with self._lock:
            self.pending.append(operation)
        return operation

    def undo_last(self) -> Optional[Operation]:
        with self._lock:
            return self.pending.pop() if self.pending else None

    def virtual_entries(self, folder: str | Path) -> list[tuple[Path, str]]:
        folder = resolve(folder)
        visible: dict[str, tuple[Path, str]] = {}
        if folder.exists() and folder.is_dir():
            try:
                for path in folder.iterdir():
                    visible[str(resolve(path))] = (path, 'real')
            except OSError:
                pass
        with self._lock:
            operations = list(self.pending)
        for operation in operations:
            if operation.kind == 'mkdir':
                path = resolve(operation.path)
                if path.parent == folder:
                    visible[str(path)] = (path, 'pending')
            elif operation.kind == 'delete':
                visible.pop(str(resolve(operation.path)), None)
            elif operation.kind in {'rename', 'move'}:
                old = resolve(operation.old)
                new = resolve(operation.new)
                visible.pop(str(old), None)
                if new.parent == folder:
                    visible[str(new)] = (new, 'pending')
            elif operation.kind == 'download' and operation.payload:
                final_path = operation.payload.final_path()
                if final_path.parent == folder:
                    visible[str(final_path)] = (final_path, 'pending')
        return sorted(visible.values(), key=lambda item: (not item[0].is_dir(), item[0].name.casefold()))


class ApplyEngine:
    def __init__(self, model: LibraryModel):
        self.model = model
        self.cancel_event = threading.Event()

    def cancel(self) -> None:
        self.cancel_event.set()

    def apply(self, progress_callback: Optional[Callable[[Operation, int, int], None]] = None) -> list[Operation]:
        with self.model._lock:
            batch = list(self.model.pending)
        results = []
        total = len(batch)
        for index, operation in enumerate(batch, 1):
            if self.cancel_event.is_set():
                operation.status = 'cancelled'
                operation.error = 'הפעולה בוטלה.'
                results.append(operation)
                continue
            try:
                operation.status = 'running'
                operation.progress = 0
                self._execute(operation, lambda pct: self._set_progress(operation, pct, progress_callback))
                operation.status = 'success'
                operation.progress = 100
                operation.error = ''
            except Exception as exc:
                operation.status = 'failed'
                operation.error = str(exc)
            results.append(operation)
            if progress_callback:
                progress_callback(operation, index, total)
        return results

    @staticmethod
    def _set_progress(operation: Operation, pct: int, callback) -> None:
        operation.progress = max(0, min(100, int(pct)))
        if callback:
            callback(operation, 0, 0)

    def _execute(self, operation: Operation, progress) -> None:
        if operation.kind == 'mkdir':
            target = resolve(operation.path)
            if target.exists():
                raise FileExistsError(f'הפריט כבר קיים: {target.name}')
            target.mkdir(parents=False, exist_ok=False)
        elif operation.kind in {'rename', 'move'}:
            self._rename(operation.old, operation.new)
        elif operation.kind == 'delete':
            target = resolve(operation.path)
            if not target.exists():
                raise FileNotFoundError(f'הפריט לא נמצא: {target.name}')
            if target.is_dir():
                shutil.rmtree(target)
            else:
                target.unlink()
        elif operation.kind == 'download':
            if not operation.payload:
                raise ValueError('חסר מידע להורדה.')
            self._download(operation.payload, progress)
        else:
            raise ValueError(f'סוג פעולה לא מוכר: {operation.kind}')

    @staticmethod
    def _rename(old: str, new: str) -> None:
        source = resolve(old)
        destination = resolve(new)
        if not source.exists():
            raise FileNotFoundError(f'הפריט לא נמצא: {source.name}')
        if destination.exists():
            raise FileExistsError(f'היעד כבר קיים: {destination.name}')
        if not destination.parent.exists():
            raise FileNotFoundError(f'תיקיית היעד לא קיימת: {destination.parent}')
        source.rename(destination)

    @staticmethod
    def _download(payload: MediaPayload, progress) -> None:
        import imageio_ffmpeg
        import yt_dlp

        target = resolve(payload.target)
        target.mkdir(parents=True, exist_ok=True)
        final_path = payload.final_path()
        if final_path.exists():
            raise FileExistsError(f'הקובץ כבר קיים: {final_path.name}')

        temp_dir = target / '.tunevault_tmp'
        temp_dir.mkdir(parents=True, exist_ok=True)
        ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()

        def hook(data):
            status = data.get('status')
            if status == 'downloading':
                total = data.get('total_bytes') or data.get('total_bytes_estimate')
                downloaded = data.get('downloaded_bytes', 0)
                if total:
                    progress(min(90, max(0, int(downloaded * 90 / total))))
            elif status == 'finished':
                progress(93)

        options = {
            'outtmpl': str(temp_dir / '%(id)s.%(ext)s'),
            'ffmpeg_location': ffmpeg,
            'noplaylist': True,
            'quiet': True,
            'no_warnings': True,
            'retries': 5,
            'fragment_retries': 5,
            'continuedl': True,
            'windowsfilenames': False,
            'overwrites': True,
            'progress_hooks': [hook],
        }

        if payload.normalized_format() == 'MP3':
            options.update({
                'format': 'bestaudio/best',
                'postprocessors': [{
                    'key': 'FFmpegExtractAudio',
                    'preferredcodec': 'mp3',
                    'preferredquality': '192',
                }],
            })
        else:
            options.update({
                'format': 'bestvideo+bestaudio/best',
                'merge_output_format': 'mp4',
            })

        try:
            with yt_dlp.YoutubeDL(options) as downloader:
                info = downloader.extract_info(payload.url, download=True)
                requested = Path(downloader.prepare_filename(info))
            progress(96)
            candidates = []
            if requested.exists():
                candidates.append(requested)
            candidates.extend(temp_dir.glob(f'{requested.stem}.*'))
            candidates.extend(temp_dir.glob('*'))
            source = next((p for p in candidates if p.is_file() and p.suffix.lower() in MEDIA_EXTENSIONS), None)
            if source is None:
                raise FileNotFoundError('ההורדה הסתיימה אך לא נמצא קובץ מדיה תקין.')
            source.replace(final_path)
            if not final_path.exists() or final_path.stat().st_size <= 0:
                raise IOError('הקובץ הסופי לא נוצר כראוי.')
            progress(100)
        finally:
            try:
                if temp_dir.exists():
                    for item in temp_dir.iterdir():
                        if item.is_file():
                            item.unlink(missing_ok=True)
                    temp_dir.rmdir()
            except OSError:
                pass
