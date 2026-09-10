from __future__ import annotations

import json
import os
import shutil
import sys
import threading
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

from PySide6.QtCore import QObject, QRunnable, QThreadPool, Qt, Signal, Slot
from PySide6.QtWidgets import (
    QApplication, QComboBox, QDialog, QFileDialog, QFrame, QGridLayout,
    QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem, QMainWindow,
    QMessageBox, QProgressBar, QPushButton, QScrollArea, QSplitter,
    QStackedWidget, QVBoxLayout, QWidget
)

APP_NAME = "TuneVault"
APP_DIR = Path(os.getenv("APPDATA", str(Path.home()))) / APP_NAME
APP_DIR.mkdir(parents=True, exist_ok=True)
CONFIG_FILE = APP_DIR / "config.json"

AUDIO_EXTENSIONS = {".mp3", ".wav", ".flac", ".m4a", ".aac", ".ogg", ".opus"}
VIDEO_EXTENSIONS = {".mp4", ".mkv", ".webm", ".mov", ".avi", ".m4v"}


def safe_name(value: str) -> str:
    invalid = '<>:"/\\|?*'
    text = "".join("-" if ord(c) < 32 or c in invalid else c for c in str(value))
    text = text.strip().rstrip(".")
    reserved = {"CON", "PRN", "AUX", "NUL"} | {f"COM{i}" for i in range(1, 10)} | {f"LPT{i}" for i in range(1, 10)}
    if text.upper() in reserved:
        text = "_" + text
    return text or "ללא שם"


def resolve(path: str | Path) -> Path:
    p = Path(path)
    try:
        return p.resolve()
    except OSError:
        return p.absolute()


def inside(path: str | Path, root: str | Path) -> bool:
    try:
        resolve(path).relative_to(resolve(root))
        return True
    except ValueError:
        return False


def icon_for(path: Path) -> str:
    if path.is_dir():
        return "📁"
    if path.suffix.lower() in AUDIO_EXTENSIONS:
        return "🎵"
    if path.suffix.lower() in VIDEO_EXTENSIONS:
        return "🎬"
    return "📄"


def load_config() -> dict:
    try:
        if CONFIG_FILE.exists():
            data = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
            if isinstance(data, dict) and isinstance(data.get("roots"), list):
                return data
    except (OSError, json.JSONDecodeError):
        pass
    return {"roots": []}


def save_config(data: dict) -> None:
    tmp = CONFIG_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(CONFIG_FILE)


@dataclass
class MediaPayload:
    url: str
    title: str
    target: str
    format: str = "MP3"

    def final_path(self) -> Path:
        ext = ".mp4" if self.format.upper() == "MP4" else ".mp3"
        return resolve(self.target) / f"{safe_name(self.title)}{ext}"


@dataclass
class Operation:
    kind: str
    label: str
    old: str = ""
    new: str = ""
    path: str = ""
    payload: Optional[MediaPayload] = None
    id: str = field(default_factory=lambda: uuid.uuid4().hex)
    status: str = "pending"
    progress: int = 0
    error: str = ""


class LibraryModel:
    def __init__(self, roots: list[str]):
        self.roots = [str(resolve(r)) for r in roots]
        self.pending: list[Operation] = []
        self.history: list[Operation] = []
        self._lock = threading.RLock()

    def allowed(self, path: str | Path) -> bool:
        p = resolve(path)
        return any(p == resolve(r) or inside(p, r) for r in self.roots)

    def stage(self, op: Operation) -> None:
        paths = [op.path, op.old, op.new]
        if op.payload:
            paths.append(op.payload.target)
        for path in paths:
            if path and not self.allowed(path):
                raise PermissionError("הפעולה חורגת מהספריות המורשות.")
        with self._lock:
            self.pending.append(op)

    def undo_last(self) -> Optional[Operation]:
        with self._lock:
            return self.pending.pop() if self.pending else None

    def virtual_entries(self, folder: str | Path) -> list[tuple[Path, str]]:
        folder = resolve(folder)
        visible: dict[str, tuple[Path, str]] = {}
        if folder.exists() and folder.is_dir():
            try:
                for p in folder.iterdir():
                    visible[str(resolve(p))] = (p, "real")
            except OSError:
                pass
        with self._lock:
            ops = list(self.pending)
        for op in ops:
            if op.kind == "mkdir":
                p = resolve(op.path)
                if p.parent == folder:
                    visible[str(p)] = (p, "pending")
            elif op.kind == "delete":
                visible.pop(str(resolve(op.path)), None)
            elif op.kind in {"rename", "move"}:
                old, new = resolve(op.old), resolve(op.new)
                visible.pop(str(old), None)
                if new.parent == folder:
                    visible[str(new)] = (new, "pending")
            elif op.kind == "download" and op.payload:
                p = op.payload.final_path()
                if p.parent == folder:
                    visible[str(p)] = (p, "pending")
        return sorted(visible.values(), key=lambda x: (not x[0].is_dir(), x[0].name.casefold()))


class ApplyEngine:
    def __init__(self, model: LibraryModel):
        self.model = model
        self.cancel_event = threading.Event()

    def cancel(self) -> None:
        self.cancel_event.set()

    def apply(self, callback: Optional[Callable[[Operation], None]] = None) -> list[Operation]:
        with self.model._lock:
            batch = list(self.model.pending)
        results = []
        for op in batch:
            if self.cancel_event.is_set():
                op.status = "cancelled"
                op.error = "הפעולה בוטלה."
                results.append(op)
                continue
            try:
                op.status = "running"
                op.progress = 1
                if callback: callback(op)
                if op.kind == "mkdir":
                    target = resolve(op.path)
                    if target.exists():
                        raise FileExistsError(f"הפריט כבר קיים: {target.name}")
                    target.mkdir(parents=False)
                elif op.kind in {"rename", "move"}:
                    self._move(op.old, op.new)
                elif op.kind == "delete":
                    target = resolve(op.path)
                    if not target.exists():
                        raise FileNotFoundError(f"הפריט לא נמצא: {target.name}")
                    if target.is_dir():
                        shutil.rmtree(target)
                    else:
                        target.unlink()
                elif op.kind == "download":
                    self._download(op.payload, op)
                else:
                    raise ValueError(f"פעולה לא מוכרת: {op.kind}")
                op.progress = 100
                op.status = "success"
            except Exception as exc:
                op.status = "failed"
                op.error = str(exc)
            results.append(op)
            if callback: callback(op)
        return results

    @staticmethod
    def _move(old: str, new: str) -> None:
        source, destination = resolve(old), resolve(new)
        if not source.exists():
            raise FileNotFoundError(f"הפריט לא נמצא: {source.name}")
        if destination.exists():
            raise FileExistsError(f"היעד כבר קיים: {destination.name}")
        if not destination.parent.exists():
            raise FileNotFoundError(f"תיקיית היעד לא קיימת: {destination.parent}")
        source.rename(destination)

    @staticmethod
    def _download(payload: Optional[MediaPayload], op: Operation) -> None:
        if payload is None or not payload.url:
            raise ValueError("לא הוגדר קישור להורדה.")
        import imageio_ffmpeg
        import yt_dlp

        target = resolve(payload.target)
        target.mkdir(parents=True, exist_ok=True)
        final_path = payload.final_path()
        if final_path.exists():
            raise FileExistsError(f"הקובץ כבר קיים: {final_path.name}")

        temp_dir = target / ".tunevault_tmp"
        temp_dir.mkdir(parents=True, exist_ok=True)

        def hook(data):
            if data.get("status") == "downloading":
                total = data.get("total_bytes") or data.get("total_bytes_estimate")
                downloaded = data.get("downloaded_bytes", 0)
                if total:
                    op.progress = min(90, max(0, int(downloaded * 90 / total)))
            elif data.get("status") == "finished":
                op.progress = 93

        options = {
            "outtmpl": str(temp_dir / "%(id)s.%(ext)s"),
            "ffmpeg_location": imageio_ffmpeg.get_ffmpeg_exe(),
            "noplaylist": True,
            "quiet": True,
            "no_warnings": True,
            "retries": 5,
            "fragment_retries": 5,
            "continuedl": True,
            "progress_hooks": [hook],
        }
        if payload.format.upper() == "MP4":
            options.update({"format": "bestvideo+bestaudio/best", "merge_output_format": "mp4"})
        else:
            options.update({
                "format": "bestaudio/best",
                "postprocessors": [{"key": "FFmpegExtractAudio", "preferredcodec": "mp3", "preferredquality": "192"}],
            })
        try:
            with yt_dlp.YoutubeDL(options) as ydl:
                info = ydl.extract_info(payload.url, download=True)
                requested = Path(ydl.prepare_filename(info))
            candidates = ([requested] if requested.exists() else []) + list(temp_dir.glob(f"{requested.stem}.*")) + list(temp_dir.glob("*"))
            source = next((p for p in candidates if p.is_file() and p.suffix.lower() in AUDIO_EXTENSIONS | VIDEO_EXTENSIONS), None)
            if source is None:
                raise FileNotFoundError("לא נמצא קובץ מדיה לאחר ההורדה.")
            op.progress = 97
            source.replace(final_path)
            if not final_path.exists() or final_path.stat().st_size <= 0:
                raise IOError("הקובץ הסופי ריק או לא נוצר.")
        finally:
            try:
                if temp_dir.exists():
                    for p in temp_dir.iterdir():
                        if p.is_file(): p.unlink(missing_ok=True)
                    temp_dir.rmdir()
            except OSError:
                pass


class WorkerSignals(QObject):
    result = Signal(object)
    error = Signal(str)
    finished = Signal()


class Worker(QRunnable):
    def __init__(self, fn, *args):
        super().__init__()
        self.fn, self.args = fn, args
        self.signals = WorkerSignals()

    @Slot()
    def run(self):
        try:
            self.signals.result.emit(self.fn(*self.args))
        except Exception as exc:
            self.signals.error.emit(str(exc))
        finally:
            self.signals.finished.emit()


class MediaDialog(QDialog):
    def __init__(self, target: Path, parent=None):
        super().__init__(parent)
        self.target = resolve(target)
        self.info = {}
        self.setWindowTitle("הוספת מדיה")
        self.resize(760, 520)
        layout = QVBoxLayout(self)
        title = QLabel("הוספת מדיה")
        title.setObjectName("DialogTitle")
        layout.addWidget(title)
        layout.addWidget(QLabel("הדבק קישור או כתוב חיפוש. ההוספה לתור אינה משנה את הדיסק עד לשמירת שינויים."))
        row = QHBoxLayout()
        self.query = QLineEdit()
        self.query.setPlaceholderText("קישור או חיפוש…")
        self.detect_btn = QPushButton("זיהוי")
        self.detect_btn.setObjectName("Primary")
        self.detect_btn.clicked.connect(self.detect)
        row.addWidget(self.query, 1)
        row.addWidget(self.detect_btn)
        layout.addLayout(row)
        self.preview = QLabel("עדיין לא זוהתה מדיה")
        self.preview.setObjectName("Preview")
        self.preview.setWordWrap(True)
        layout.addWidget(self.preview)
        self.name = QLineEdit()
        self.name.setPlaceholderText("שם הקובץ הסופי")
        self.name.setEnabled(False)
        layout.addWidget(self.name)
        self.format = QComboBox()
        self.format.addItems(["MP3", "MP4"])
        self.format.setEnabled(False)
        layout.addWidget(self.format)
        actions = QHBoxLayout(); actions.addStretch()
        cancel = QPushButton("ביטול"); cancel.clicked.connect(self.reject)
        ok = QPushButton("הוסף לתור"); ok.setObjectName("Primary"); ok.setEnabled(False); ok.clicked.connect(self.accept)
        self.ok = ok
        actions.addWidget(cancel); actions.addWidget(ok)
        layout.addLayout(actions)

    @staticmethod
    def extract(query):
        import yt_dlp
        value = query.strip()
        opts = {"quiet": True, "no_warnings": True, "skip_download": True, "noplaylist": False}
        if not value.startswith(("http://", "https://")):
            value = "ytsearch1:" + value
            opts["noplaylist"] = True
        with yt_dlp.YoutubeDL(opts) as ydl:
            return ydl.extract_info(value, download=False), value

    def detect(self):
        if not self.query.text().strip(): return
        self.detect_btn.setEnabled(False)
        worker = Worker(self.extract, self.query.text())
        worker.signals.result.connect(self.detected)
        worker.signals.error.connect(lambda e: QMessageBox.warning(self, "הזיהוי נכשל", e))
        worker.signals.finished.connect(lambda: self.detect_btn.setEnabled(True))
        QThreadPool.globalInstance().start(worker)

    def detected(self, result):
        info, original = result
        if original.startswith("ytsearch") and info.get("entries"):
            info = next((e for e in info["entries"] if e), info)
        self.info = info or {}
        title = self.info.get("title") or "ללא שם"
        creator = self.info.get("uploader") or self.info.get("channel") or ""
        duration = self.info.get("duration")
        self.preview.setText(f"{title}\n{creator}\n{duration or ''}")
        self.name.setText(title)
        self.name.setEnabled(True)
        self.format.setEnabled(True)
        self.ok.setEnabled(True)

    def payload(self) -> MediaPayload:
        return MediaPayload(self.query.text().strip(), self.name.text().strip() or "ללא שם", str(self.target), self.format.currentText())


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        config = load_config()
        self.model = LibraryModel(config.get("roots", []))
        self.config = config
        self.current_folder: Optional[Path] = None
        self.pool = QThreadPool.globalInstance()
        self.engine: Optional[ApplyEngine] = None
        self.saving = False
        self.setWindowTitle("TuneVault")
        self.resize(1420, 880)
        self.setMinimumSize(1100, 700)
        self.build()
        self.refresh_roots()
        self.refresh_all()

    def build(self):
        root = QWidget(); outer = QHBoxLayout(root); outer.setContentsMargins(0, 0, 0, 0); outer.setSpacing(0); self.setCentralWidget(root)
        side = QFrame(); side.setObjectName("Sidebar"); side.setFixedWidth(250); sl = QVBoxLayout(side); sl.setContentsMargins(22, 28, 22, 24)
        logo = QLabel("TuneVault"); logo.setObjectName("Brand"); sl.addWidget(logo)
        sl.addWidget(QLabel("הספרייה שלך.\nמסודרת בדרך שלך.", objectName="Muted")); sl.addSpacing(25)
        self.nav_buttons = []
        for text, idx in [("סקירה", 0), ("ספרייה", 1), ("מרכז הפעולות", 2), ("חיפוש", 3)]:
            b = QPushButton(text); b.setObjectName("NavButton"); b.clicked.connect(lambda _, i=idx: self.go(i)); sl.addWidget(b); self.nav_buttons.append(b)
        sl.addStretch()
        self.save_button = QPushButton("שמירת שינויים"); self.save_button.setObjectName("Primary"); self.save_button.clicked.connect(self.save_changes); sl.addWidget(self.save_button)
        self.status = QLabel("הכול מסונכרן"); self.status.setObjectName("Status"); sl.addWidget(self.status)
        outer.addWidget(side)
        self.pages = QStackedWidget(); self.pages.addWidget(self.dashboard()); self.pages.addWidget(self.library()); self.pages.addWidget(self.actions_page()); self.pages.addWidget(self.search_page()); outer.addWidget(self.pages, 1)

    def shell(self, title: str, subtitle: str):
        page = QWidget(); lay = QVBoxLayout(page); lay.setContentsMargins(34, 28, 34, 28)
        t = QLabel(title); t.setObjectName("PageTitle"); lay.addWidget(t)
        s = QLabel(subtitle); s.setObjectName("Muted"); s.setWordWrap(True); lay.addWidget(s)
        return page, lay

    def dashboard(self):
        page, lay = self.shell("סקירה", "כל מה שקורה בספרייה שלך — במקום אחד.")
        hero = QFrame(); hero.setObjectName("Hero"); h = QHBoxLayout(hero)
        copy = QVBoxLayout(); a = QLabel("המדיה שלך, מסודרת."); a.setObjectName("HeroTitle"); copy.addWidget(a)
        copy.addWidget(QLabel("מוסיפים, מסדרים ובודקים שינויים — ורק כשמוכנים שומרים אותם למחשב.", objectName="Muted"))
        row = QHBoxLayout(); add = QPushButton("＋ הוסף מדיה"); add.setObjectName("Primary"); add.clicked.connect(self.quick_add); browse = QPushButton("פתח ספרייה"); browse.clicked.connect(lambda: self.go(1)); row.addWidget(add); row.addWidget(browse); row.addStretch(); copy.addLayout(row); h.addLayout(copy, 2)
        stats = QVBoxLayout(); self.d_pending = QLabel("0\nממתינים"); self.d_roots = QLabel("0\nמיקומים"); self.d_history = QLabel("0\nהושלמו");
        for x in [self.d_pending, self.d_roots, self.d_history]: x.setObjectName("Stat"); stats.addWidget(x)
        h.addLayout(stats, 1); lay.addWidget(hero)
        grid = QGridLayout()
        cards = [("הוספת מדיה", "חיפוש או קישור, עם שם ופורמט משלך.", self.quick_add), ("ניהול הספרייה", "תיקיות, שינוי שמות, העברה ומחיקה עם תצוגה מקדימה.", lambda: self.go(1)), ("מרכז הפעולות", "הכול ברור: ממתין, מתבצע, הצליח או נכשל.", lambda: self.go(2)), ("חיפוש", "מצא במהירות שירים, סרטונים ותיקיות.", lambda: self.go(3))]
        for i, (title, text, fn) in enumerate(cards):
            f = QFrame(); f.setObjectName("Card"); q = QVBoxLayout(f); x = QLabel(title); x.setObjectName("CardTitle"); q.addWidget(x); q.addWidget(QLabel(text, objectName="Muted")); q.addStretch(); b = QPushButton("פתח →"); b.clicked.connect(fn); q.addWidget(b); grid.addWidget(f, i // 2, i % 2)
        lay.addLayout(grid); lay.addStretch(); return page

    def library(self):
        page, lay = self.shell("הספרייה", "זו תצוגה וירטואלית. שינוי אמיתי במחשב קורה רק לאחר שמירת שינויים.")
        top = QHBoxLayout(); self.local_filter = QLineEdit(); self.local_filter.setPlaceholderText("חיפוש בתיקייה…"); self.local_filter.textChanged.connect(self.render_folder); top.addWidget(self.local_filter)
        add_root = QPushButton("＋ מיקום"); add_root.clicked.connect(self.add_root); top.addWidget(add_root); lay.addLayout(top)
        split = QSplitter(Qt.Horizontal); lay.addWidget(split, 1)
        left = QWidget(); ll = QVBoxLayout(left); ll.addWidget(QLabel("מיקומים מורשים", objectName="Eyebrow")); self.roots = QListWidget(); self.roots.currentItemChanged.connect(self.select_root); ll.addWidget(self.roots, 1); remove = QPushButton("הסר גישה"); remove.clicked.connect(self.remove_root); ll.addWidget(remove); split.addWidget(left)
        right = QWidget(); rl = QVBoxLayout(right); self.path_label = QLabel("בחר מיקום", objectName="SectionTitle"); rl.addWidget(self.path_label); self.items = QListWidget(); self.items.itemDoubleClicked.connect(self.open_item); rl.addWidget(self.items, 1)
        actions = QHBoxLayout();
        for text, fn in [("＋ תיקייה", self.stage_folder), ("＋ מדיה", self.quick_add), ("שנה שם", self.rename), ("העבר", self.move), ("מחק", self.delete)]: b = QPushButton(text); b.clicked.connect(fn); actions.addWidget(b)
        actions.addStretch(); rl.addLayout(actions); split.addWidget(right); split.setSizes([320, 900]); return page

    def actions_page(self):
        page, lay = self.shell("מרכז הפעולות", "כל פעולה מקבלת סטטוס משלה. אין יותר ניחושים.")
        self.action_scroll = QScrollArea(); self.action_scroll.setWidgetResizable(True); self.action_container = QWidget(); self.action_layout = QVBoxLayout(self.action_container); self.action_layout.setAlignment(Qt.AlignTop); self.action_scroll.setWidget(self.action_container); lay.addWidget(self.action_scroll, 1)
        controls = QHBoxLayout(); undo = QPushButton("בטל פעולה אחרונה"); undo.clicked.connect(self.undo); controls.addWidget(undo); controls.addStretch(); cancel = QPushButton("עצור שמירה"); cancel.clicked.connect(self.cancel_save); controls.addWidget(cancel); lay.addLayout(controls); return page

    def search_page(self):
        page, lay = self.shell("חיפוש", "חיפוש מהיר בכל המיקומים שהרשית ל־TuneVault.")
        row = QHBoxLayout(); self.search_input = QLineEdit(); self.search_input.setPlaceholderText("מה לחפש?"); button = QPushButton("חיפוש"); button.setObjectName("Primary"); button.clicked.connect(self.run_search); row.addWidget(self.search_input, 1); row.addWidget(button); lay.addLayout(row); self.results = QListWidget(); self.results.itemDoubleClicked.connect(self.open_result); lay.addWidget(self.results, 1); return page

    def go(self, index: int): self.pages.setCurrentIndex(index); self.refresh_all()

    def refresh_roots(self):
        self.roots.clear(); valid=[]
        for raw in self.config.get("roots", []):
            p = resolve(raw)
            if p.exists() and p.is_dir():
                valid.append(str(p)); item=QListWidgetItem(f"📁 {p.name or p}"); item.setData(Qt.UserRole, str(p)); self.roots.addItem(item)
        self.config["roots"] = valid; self.model.roots = valid; save_config(self.config)
        if self.roots.count() and self.roots.currentRow() < 0: self.roots.setCurrentRow(0)

    def add_root(self):
        raw = QFileDialog.getExistingDirectory(self, "בחר תיקייה")
        if not raw: return
        candidate = resolve(raw)
        roots = [resolve(r) for r in self.model.roots]
        if any(candidate == r or inside(candidate, r) or inside(r, candidate) for r in roots):
            QMessageBox.information(self, "כבר קיים", "התיקייה כבר מורשית או משתרשת מספרייה מורשית."); return
        self.config.setdefault("roots", []).append(str(candidate)); save_config(self.config); self.model.roots = self.config["roots"]; self.refresh_roots()

    def remove_root(self):
        item = self.roots.currentItem()
        if not item: return
        p = resolve(item.data(Qt.UserRole)); ans = QMessageBox.question(self, "הסר גישה", f"להסיר גישה ל־{p}?\nשום דבר לא יימחק.")
        if ans != QMessageBox.Yes: return
        self.config["roots"] = [r for r in self.config.get("roots", []) if resolve(r) != p]; save_config(self.config); self.model.roots = self.config["roots"]; self.refresh_roots()

    def select_root(self, item, _prev=None):
        if item: self.current_folder = resolve(item.data(Qt.UserRole)); self.render_folder()

    def render_folder(self, *_):
        if not self.current_folder: return
        self.path_label.setText(str(self.current_folder)); needle=(self.local_filter.text() if hasattr(self, 'local_filter') else '').casefold(); self.items.clear()
        for p, state in self.model.virtual_entries(self.current_folder):
            if needle and needle not in p.name.casefold(): continue
            marker = "🟡 " if state == "pending" else ""
            item = QListWidgetItem(f"{marker}{icon_for(p)}  {p.name}"); item.setData(Qt.UserRole, str(p)); item.setToolTip(str(p)); self.items.addItem(item)

    def open_item(self, item):
        p=resolve(item.data(Qt.UserRole));
        if p.is_dir() or any(str(x[0]) == str(p) for x in self.model.virtual_entries(p)):
            self.current_folder=p; self.render_folder()

    def selected(self):
        item=self.items.currentItem(); return resolve(item.data(Qt.UserRole)) if item else None

    def conflict(self, path: Path) -> bool:
        target=resolve(path); return any(resolve(p) == target for p,_ in self.model.virtual_entries(target.parent))

    def stage_folder(self):
        if not self.current_folder: return
        name, ok = QInputDialog.getText(self, "תיקייה חדשה", "שם התיקייה:")
        if not ok: return
        p=resolve(self.current_folder / safe_name(name))
        if self.conflict(p): QMessageBox.warning(self, "שם בשימוש", "כבר קיים פריט בשם הזה."); return
        self.model.stage(Operation("mkdir", f"יצירת תיקייה · {p.name}", path=str(p))); self.refresh_all()

    def rename(self):
        old=self.selected();
        if not old: return
        current=old.stem if old.is_file() else old.name; name,ok=QInputDialog.getText(self,"שינוי שם","השם החדש:",text=current)
        if not ok: return
        name=safe_name(name)+(old.suffix if old.is_file() else ''); new=resolve(old.parent/name)
        if self.conflict(new): QMessageBox.warning(self,"שם בשימוש","כבר קיים פריט בשם הזה."); return
        self.model.stage(Operation("rename", f"שינוי שם · {old.name} → {new.name}", old=str(old), new=str(new))); self.refresh_all()

    def move(self):
        old=self.selected();
        if not old: return
        dest=QFileDialog.getExistingDirectory(self,"בחר יעד",str(old.parent));
        if not dest: return
        dest=resolve(dest); new=dest/old.name
        if not self.model.allowed(dest): QMessageBox.warning(self,"לא מורשה","היעד אינו בתוך ספרייה מורשית."); return
        if self.conflict(new): QMessageBox.warning(self,"כבר קיים","פריט באותו שם כבר קיים."); return
        self.model.stage(Operation("move",f"העברה · {old.name} → {dest.name}",old=str(old),new=str(new))); self.refresh_all()

    def delete(self):
        p=self.selected();
        if not p: return
        if QMessageBox.question(self,"מחיקה",f"לסמן את «{p.name}» למחיקה?") != QMessageBox.Yes: return
        self.model.stage(Operation("delete",f"מחיקה · {p.name}",path=str(p))); self.refresh_all()

    def quick_add(self):
        if not self.current_folder:
            if self.model.roots: self.current_folder=resolve(self.model.roots[0])
            else: self.add_root(); return
        dialog=MediaDialog(self.current_folder,self)
        if dialog.exec()!=QDialog.Accepted: return
        payload=dialog.payload(); self.model.stage(Operation("download",f"הורדה · {payload.title} [{payload.format}]",payload=payload)); self.refresh_all(); self.go(2)

    def save_changes(self):
        if self.saving or not self.model.pending: return
        self.saving=True; self.engine=ApplyEngine(self.model); self.save_button.setEnabled(False); self.status.setText("מבצע שינויים…")
        worker=Worker(self.engine.apply,self.on_operation)
        worker.signals.result.connect(self.finish_save); worker.signals.error.connect(self.save_error); self.pool.start(worker)

    def on_operation(self, op: Operation):
        self.refresh_actions()

    def finish_save(self, results):
        success=[o for o in results if o.status=="success"]; failed=[o for o in results if o.status in {"failed","cancelled"}]
        with self.model._lock:
            self.model.history.extend(success[-200:]); self.model.pending=failed
        self.saving=False; self.engine=None; self.save_button.setEnabled(bool(self.model.pending)); self.status.setText("הושלם" if not failed else f"{len(failed)} פעולות דורשות טיפול"); self.refresh_all()

    def save_error(self, err):
        self.saving=False; self.engine=None; self.save_button.setEnabled(True); self.status.setText("שגיאה"); QMessageBox.critical(self,"שגיאה",err)

    def cancel_save(self):
        if self.engine: self.engine.cancel(); self.status.setText("עוצר…")

    def undo(self):
        if self.saving: return
        op=self.model.undo_last();
        if op: self.refresh_all()

    def refresh_actions(self):
        if not hasattr(self, 'action_layout'): return
        while self.action_layout.count():
            item=self.action_layout.takeAt(0); widget=item.widget();
            if widget: widget.deleteLater()
        ops=list(self.model.pending)+list(reversed(self.model.history[-25:]))
        for op in ops:
            card=QFrame(); card.setObjectName("QueueCard"); q=QVBoxLayout(card); row=QHBoxLayout(); state={"pending":"ממתין","running":"מבצע","success":"הושלם","failed":"נכשל","cancelled":"בוטל"}.get(op.status,op.status); row.addWidget(QLabel(state)); row.addWidget(QLabel(op.label),1); row.addWidget(QLabel(f"{op.progress}%")); q.addLayout(row); bar=QProgressBar(); bar.setValue(op.progress); bar.setVisible(op.status in {"pending","running","failed"}); q.addWidget(bar); self.action_layout.addWidget(card)

    def refresh_all(self):
        if hasattr(self,'d_pending'): self.d_pending.setText(f"{len(self.model.pending)}\nממתינים"); self.d_roots.setText(f"{len(self.model.roots)}\nמיקומים"); self.d_history.setText(f"{len(self.model.history)}\nהושלמו")
        if hasattr(self,'save_button'): self.save_button.setText("שמירת שינויים" + (f" ({len(self.model.pending)})" if self.model.pending else "")); self.save_button.setEnabled(bool(self.model.pending) and not self.saving)
        if hasattr(self,'current_folder') and self.current_folder: self.render_folder()
        if hasattr(self,'action_layout'): self.refresh_actions()

    def run_search(self):
        needle=self.search_input.text().strip().casefold(); self.results.clear();
        if not needle: return
        hits=[]
        for raw in self.model.roots:
            root=resolve(raw)
            if not root.exists(): continue
            try:
                for p in root.rglob('*'):
                    try:
                        if needle in p.name.casefold(): hits.append(p)
                        if len(hits)>=1000: raise StopIteration
                    except OSError: pass
            except StopIteration: pass
        for p in hits:
            item=QListWidgetItem(f"{icon_for(p)}  {p.name}  ·  {p.parent}"); item.setData(Qt.UserRole,str(p)); self.results.addItem(item)

    def open_result(self,item):
        p=resolve(item.data(Qt.UserRole)); self.current_folder=p if p.is_dir() else p.parent; self.go(1)


def stylesheet():
    return """
    QWidget { background:#090b0f; color:#f2f4f7; font-family:'Segoe UI'; font-size:14px; }
    #Sidebar { background:#0d1015; border-right:1px solid #20252d; }
    #Brand { font-size:30px; font-weight:800; }
    #PageTitle { font-size:34px; font-weight:800; }
    #DialogTitle { font-size:25px; font-weight:800; }
    #Hero { background:#121820; border:1px solid #252d36; border-radius:18px; padding:20px; }
    #HeroTitle { font-size:27px; font-weight:800; }
    #Muted { color:#8d96a3; }
    QLabel#Muted { color:#8d96a3; }
    #Status { color:#aeb6c1; background:#141a20; border:1px solid #28303a; border-radius:10px; padding:9px; }
    #NavButton { background:transparent; border:0; border-radius:10px; text-align:right; padding:13px; color:#aeb7c3; }
    #NavButton:hover, #NavButton[active='true'] { background:#181e25; color:white; }
    QPushButton { background:#151a21; border:1px solid #2a323c; border-radius:10px; padding:10px 15px; font-weight:650; }
    QPushButton:hover { background:#202731; }
    #Primary { background:#f3f5f7; color:#090b0f; border:0; font-weight:800; }
    QLineEdit, QComboBox { background:#10151b; border:1px solid #2a323c; border-radius:10px; padding:11px; }
    QListWidget { background:#0f1318; border:1px solid #242b34; border-radius:14px; padding:7px; }
    QListWidget::item { padding:12px; border-radius:9px; }
    QListWidget::item:hover { background:#171d24; }
    QListWidget::item:selected { background:#242c36; }
    #Card, #QueueCard, #Stat { background:#11171e; border:1px solid #262e37; border-radius:15px; }
    #CardTitle { font-size:18px; font-weight:750; }
    #Stat { padding:14px; font-size:15px; font-weight:750; }
    #Preview { background:#10151b; border:1px solid #2a323c; border-radius:12px; padding:18px; min-height:90px; }
    QProgressBar { background:#11161c; border:1px solid #252d36; border-radius:8px; height:15px; text-align:center; }
    QProgressBar::chunk { border-radius:7px; }
    QScrollBar:vertical { background:#0b0e12; width:10px; }
    QScrollBar::handle:vertical { background:#303843; border-radius:5px; min-height:30px; }
    """


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setLayoutDirection(Qt.RightToLeft)
    app.setStyleSheet(stylesheet())
    window = MainWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
