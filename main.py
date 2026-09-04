import json
import os
import shutil
import sys
import uuid
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QObject, QRunnable, QThreadPool, Qt, QTimer, Signal, Slot
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QApplication, QComboBox, QDialog, QFileDialog, QFrame, QHBoxLayout, QLabel,
    QLineEdit, QListWidget, QListWidgetItem, QMainWindow, QMessageBox, QPushButton,
    QSplitter, QStackedWidget, QVBoxLayout, QWidget, QInputDialog
)

APP_NAME = "TuneVault"
APP_DIR = Path(os.getenv("APPDATA", Path.home())) / APP_NAME
APP_DIR.mkdir(parents=True, exist_ok=True)
CONFIG = APP_DIR / "config.json"

MEDIA_EXTS = {
    ".mp3", ".wav", ".flac", ".m4a", ".aac", ".ogg", ".opus",
    ".mp4", ".mkv", ".webm", ".mov", ".avi", ".m4v",
}


def load_config():
    if CONFIG.exists():
        try:
            data = json.loads(CONFIG.read_text(encoding="utf-8"))
            if isinstance(data, dict) and isinstance(data.get("roots"), list):
                return data
        except Exception:
            pass
    return {"roots": []}


def save_config(data):
    CONFIG.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def resolved(path):
    try:
        return Path(path).resolve()
    except OSError:
        return Path(path).absolute()


def is_under(child: Path, parent: Path) -> bool:
    try:
        child.resolve().relative_to(parent.resolve())
        return True
    except ValueError:
        return False


def safe_name(value: str) -> str:
    invalid = '<>:/\\|?*"'
    result = "".join("-" if c in invalid or ord(c) < 32 else c for c in value).strip().rstrip(".")
    return result or "Untitled"


def format_seconds(seconds):
    if not seconds:
        return ""
    seconds = int(seconds)
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def icon_for(path: Path) -> str:
    if path.is_dir():
        return "📁"
    if path.suffix.lower() in {".mp3", ".wav", ".flac", ".m4a", ".aac", ".ogg", ".opus"}:
        return "🎵"
    if path.suffix.lower() in {".mp4", ".mkv", ".webm", ".mov", ".avi", ".m4v"}:
        return "🎬"
    return "📄"


class WorkerSignals(QObject):
    result = Signal(object)
    error = Signal(str)
    done = Signal()


class Worker(QRunnable):
    def __init__(self, fn, *args, **kwargs):
        super().__init__()
        self.fn = fn
        self.args = args
        self.kwargs = kwargs
        self.signals = WorkerSignals()

    @Slot()
    def run(self):
        try:
            self.signals.result.emit(self.fn(*self.args, **self.kwargs))
        except Exception as exc:
            self.signals.error.emit(str(exc))
        finally:
            self.signals.done.emit()


class MediaDialog(QDialog):
    def __init__(self, target: Path, parent=None):
        super().__init__(parent)
        self.target = target
        self.info = None
        self.entries = []
        self.setWindowTitle("Add Media")
        self.setMinimumWidth(660)
        self.setModal(True)

        layout = QVBoxLayout(self)
        title = QLabel("Add media")
        title.setObjectName("DialogTitle")
        layout.addWidget(title)
        layout.addWidget(QLabel("Paste a media URL or playlist URL. Detection runs locally."))

        self.url = QLineEdit()
        self.url.setPlaceholderText("https://…")
        layout.addWidget(self.url)

        row = QHBoxLayout()
        self.detect = QPushButton("Detect")
        row.addWidget(self.detect)
        self.target_label = QLabel(f"Target: {target}")
        self.target_label.setObjectName("Subtle")
        row.addWidget(self.target_label, 1)
        layout.addLayout(row)

        preview = QFrame()
        pv = QHBoxLayout(preview)
        self.thumb = QLabel()
        self.thumb.setFixedSize(150, 84)
        self.thumb.setObjectName("Thumb")
        pv.addWidget(self.thumb)
        meta_box = QVBoxLayout()
        self.preview_title = QLabel("No media detected")
        self.preview_title.setObjectName("MediaPreviewTitle")
        self.preview_meta = QLabel("")
        self.preview_type = QLabel("")
        self.preview_meta.setObjectName("Subtle")
        self.preview_type.setObjectName("Subtle")
        meta_box.addWidget(self.preview_title)
        meta_box.addWidget(self.preview_meta)
        meta_box.addWidget(self.preview_type)
        meta_box.addStretch()
        pv.addLayout(meta_box, 1)
        layout.addWidget(preview)

        self.mode = QComboBox()
        self.mode.addItems(["Single media", "Playlist / batch"])
        self.mode.setEnabled(False)
        layout.addWidget(self.mode)

        self.name = QLineEdit()
        self.name.setPlaceholderText("File name")
        self.name.setEnabled(False)
        layout.addWidget(self.name)

        fmt_row = QHBoxLayout()
        fmt_row.addWidget(QLabel("Format"))
        self.format = QComboBox()
        self.format.addItems(["MP3", "MP4"])
        self.format.setEnabled(False)
        fmt_row.addWidget(self.format)
        layout.addLayout(fmt_row)

        self.batch = QListWidget()
        self.batch.setVisible(False)
        layout.addWidget(self.batch)

        controls = QHBoxLayout()
        cancel = QPushButton("Cancel")
        self.add = QPushButton("Add to library")
        self.add.setObjectName("Primary")
        self.add.setEnabled(False)
        controls.addStretch()
        controls.addWidget(cancel)
        controls.addWidget(self.add)
        layout.addLayout(controls)

        self.detect.clicked.connect(self.detect_media)
        self.mode.currentIndexChanged.connect(self._toggle_batch)
        self.add.clicked.connect(self.accept)
        cancel.clicked.connect(self.reject)

    def detect_media(self):
        url = self.url.text().strip()
        if not url:
            QMessageBox.warning(self, "Missing URL", "Paste a media URL first.")
            return
        self.detect.setEnabled(False)
        self.detect.setText("Detecting…")
        worker = Worker(self._extract, url)
        worker.signals.result.connect(self._detected)
        worker.signals.error.connect(lambda e: QMessageBox.warning(self, "Could not detect", e))
        worker.signals.done.connect(lambda: (self.detect.setEnabled(True), self.detect.setText("Detect")))
        QThreadPool.globalInstance().start(worker)

    def _extract(self, url):
        import yt_dlp
        opts = {"quiet": True, "no_warnings": True, "skip_download": True, "noplaylist": False}
        with yt_dlp.YoutubeDL(opts) as ydl:
            return ydl.extract_info(url, download=False)

    def _detected(self, info):
        self.info = info or {}
        raw_entries = self.info.get("entries") or []
        self.entries = [e for e in raw_entries if e]
        title = self.info.get("title") or "Untitled media"
        duration = format_seconds(self.info.get("duration"))
        uploader = self.info.get("uploader") or self.info.get("channel") or "Unknown creator"
        self.preview_title.setText(title)
        self.preview_meta.setText(" • ".join(x for x in [uploader, duration] if x))
        is_playlist = len(self.entries) > 1
        self.preview_type.setText(f"Playlist detected • {len(self.entries)} items" if is_playlist else "Single media")
        self.mode.setEnabled(is_playlist)
        self.mode.setCurrentIndex(1 if is_playlist else 0)
        if is_playlist:
            self.batch.clear()
            for entry in self.entries:
                row = QListWidgetItem(f"•  {entry.get('title') or 'Untitled'}")
                self.batch.addItem(row)
        self.name.setText(title)
        self.name.setEnabled(True)
        self.format.setEnabled(True)
        self.add.setEnabled(True)

        thumb_url = self.info.get("thumbnail")
        if thumb_url:
            worker = Worker(self._download_thumb, thumb_url)
            worker.signals.result.connect(self._show_thumb)
            QThreadPool.globalInstance().start(worker)

    def _download_thumb(self, url):
        import urllib.request
        with urllib.request.urlopen(url, timeout=10) as response:
            return response.read()

    def _show_thumb(self, data):
        pix = QPixmap()
        if pix.loadFromData(data):
            self.thumb.setPixmap(pix.scaled(self.thumb.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation))

    def _toggle_batch(self, index):
        batch_mode = index == 1 and bool(self.entries)
        self.batch.setVisible(batch_mode)
        self.name.setVisible(not batch_mode)
        self.format.setVisible(not batch_mode)

    def payloads(self):
        if self.mode.currentIndex() == 1 and self.entries:
            result = []
            for entry in self.entries:
                url = entry.get("webpage_url") or entry.get("original_url")
                if not url and entry.get("id") and entry.get("extractor_key"):
                    url = entry.get("url")
                if not url:
                    continue
                result.append({
                    "url": url,
                    "title": entry.get("title") or "Untitled media",
                    "format": "MP3",
                    "target": str(self.target),
                    "source": "playlist",
                })
            return result
        return [{
            "url": self.url.text().strip(),
            "title": self.name.text().strip() or (self.info or {}).get("title", "media"),
            "format": self.format.currentText(),
            "target": str(self.target),
            "source": "single",
        }]


class PlaylistFormatDialog(QDialog):
    def __init__(self, payloads, parent=None):
        super().__init__(parent)
        self.payloads = payloads
        self.setWindowTitle("Playlist options")
        self.setMinimumWidth(760)
        l = QVBoxLayout(self)
        t = QLabel("Playlist ready")
        t.setObjectName("DialogTitle")
        l.addWidget(t)
        l.addWidget(QLabel("Choose MP3 or MP4 per item, or apply one format to all."))
        quick = QHBoxLayout()
        for text, fmt in [("All MP3", "MP3"), ("All MP4", "MP4")]:
            b = QPushButton(text)
            b.clicked.connect(lambda _, f=fmt: self.set_all(f))
            quick.addWidget(b)
        quick.addStretch()
        l.addLayout(quick)

        self.rows = QListWidget()
        for payload in self.payloads:
            row = QListWidgetItem()
            widget = QWidget()
            r = QHBoxLayout(widget)
            r.setContentsMargins(8, 4, 8, 4)
            label = QLabel(payload["title"])
            label.setMinimumWidth(450)
            combo = QComboBox()
            combo.addItems(["MP3", "MP4"])
            combo.currentTextChanged.connect(lambda value, p=payload: p.__setitem__("format", value))
            r.addWidget(label, 1)
            r.addWidget(combo)
            row.setData(Qt.UserRole, payload)
            self.rows.addItem(row)
            self.rows.setItemWidget(row, widget)
        l.addWidget(self.rows, 1)

        actions = QHBoxLayout()
        actions.addStretch()
        cancel = QPushButton("Cancel")
        ok = QPushButton("Add playlist")
        ok.setObjectName("Primary")
        cancel.clicked.connect(self.reject)
        ok.clicked.connect(self.accept)
        actions.addWidget(cancel)
        actions.addWidget(ok)
        l.addLayout(actions)

    def set_all(self, fmt):
        for i, payload in enumerate(self.payloads):
            payload["format"] = fmt
            row = self.rows.item(i)
            widget = self.rows.itemWidget(row)
            combo = widget.findChild(QComboBox)
            if combo:
                combo.setCurrentText(fmt)


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.config = load_config()
        self.pending = []
        self.history = []
        self.current_path = None
        self.pool = QThreadPool.globalInstance()
        self.setWindowTitle(APP_NAME)
        self.resize(1350, 820)
        self.setMinimumSize(1100, 700)
        self._build()
        self.refresh_roots()
        self._refresh_queue()

        self.refresh_timer = QTimer(self)
        self.refresh_timer.timeout.connect(self._light_refresh)
        self.refresh_timer.start(6000)

    def _build(self):
        central = QWidget()
        self.setCentralWidget(central)
        root = QHBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        nav = QFrame()
        nav.setObjectName("Nav")
        nav.setFixedWidth(240)
        nl = QVBoxLayout(nav)
        nl.setContentsMargins(20, 28, 20, 20)
        brand = QLabel(APP_NAME)
        brand.setObjectName("Brand")
        nl.addWidget(brand)
        sub = QLabel("Your media. Your folders. Your computer.")
        sub.setObjectName("Subtle")
        sub.setWordWrap(True)
        nl.addWidget(sub)
        nl.addSpacing(32)
        for text, index in [("⌂  Library", 0), ("↓  Downloads", 1), ("⌕  Search", 2)]:
            b = QPushButton(text)
            b.setObjectName("NavButton")
            b.clicked.connect(lambda _, i=index: self.pages.setCurrentIndex(i))
            nl.addWidget(b)
        nl.addStretch()
        self.status = QLabel("All changes saved")
        self.status.setObjectName("Status")
        self.status.setWordWrap(True)
        nl.addWidget(self.status)
        root.addWidget(nav)

        self.pages = QStackedWidget()
        root.addWidget(self.pages, 1)
        self.pages.addWidget(self._library_page())
        self.pages.addWidget(self._download_page())
        self.pages.addWidget(self._search_page())

    def _library_page(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(34, 28, 34, 26)
        head = QHBoxLayout()
        titlebox = QVBoxLayout()
        title = QLabel("Your Library")
        title.setObjectName("PageTitle")
        subtitle = QLabel("Stage everything here. Real Windows folders change only after Save Changes.")
        subtitle.setObjectName("Subtle")
        titlebox.addWidget(title)
        titlebox.addWidget(subtitle)
        head.addLayout(titlebox)
        head.addStretch()
        self.search_box = QLineEdit()
        self.search_box.setPlaceholderText("Filter this folder…")
        self.search_box.textChanged.connect(self.filter_current_folder)
        head.addWidget(self.search_box)
        add = QPushButton("＋ Add folder")
        add.setObjectName("Primary")
        add.clicked.connect(self.add_root)
        head.addWidget(add)
        layout.addLayout(head)
        self.breadcrumb = QLabel("Root folders")
        self.breadcrumb.setObjectName("SectionTitle")
        layout.addWidget(self.breadcrumb)

        splitter = QSplitter(Qt.Horizontal)
        layout.addWidget(splitter, 1)

        left = QWidget()
        lv = QVBoxLayout(left)
        lv.setContentsMargins(0, 0, 0, 0)
        lv.addWidget(QLabel("Accessible libraries"))
        self.folder_list = QListWidget()
        self.folder_list.setObjectName("FolderList")
        lv.addWidget(self.folder_list, 1)
        remove_root = QPushButton("Remove library")
        remove_root.clicked.connect(self.remove_root)
        lv.addWidget(remove_root)
        splitter.addWidget(left)

        right = QWidget()
        rv = QVBoxLayout(right)
        rv.setContentsMargins(18, 0, 0, 0)
        top = QHBoxLayout()
        self.current_label = QLabel("Select a folder")
        self.current_label.setObjectName("SectionTitle")
        top.addWidget(self.current_label, 1)
        for text, fn in [("↑ Up", self.go_up), ("↻ Refresh", self.open_current_folder)]:
            b = QPushButton(text)
            b.clicked.connect(fn)
            top.addWidget(b)
        rv.addLayout(top)
        self.items = QListWidget()
        self.items.setObjectName("Items")
        self.items.itemDoubleClicked.connect(self.enter_item)
        rv.addWidget(self.items, 1)

        action_row = QHBoxLayout()
        for text, fn in [
            ("＋ Folder", self.add_subfolder), ("＋ Media", self.add_media),
            ("Rename", self.rename_item), ("Move", self.move_item), ("Delete", self.delete_item),
        ]:
            b = QPushButton(text)
            b.clicked.connect(fn)
            action_row.addWidget(b)
        rv.addLayout(action_row)
        splitter.addWidget(right)
        splitter.setSizes([320, 900])
        self.folder_list.currentItemChanged.connect(self.open_root_item)
        return page

    def _download_page(self):
        page = QWidget()
        l = QVBoxLayout(page)
        l.setContentsMargins(34, 28, 34, 26)
        title = QLabel("Downloads")
        title.setObjectName("PageTitle")
        l.addWidget(title)
        l.addWidget(QLabel("Downloads run silently on this PC. They are staged first and applied on Save Changes."))
        stats = QHBoxLayout()
        self.pending_card = QLabel("🟡 0 staged")
        self.success_card = QLabel("🟢 0 succeeded")
        self.failed_card = QLabel("🔴 0 failed")
        for w in (self.pending_card, self.success_card, self.failed_card):
            w.setObjectName("StatCard")
            stats.addWidget(w)
        l.addLayout(stats)
        self.queue = QListWidget()
        l.addWidget(self.queue, 1)
        buttons = QHBoxLayout()
        undo = QPushButton("Undo last staged action")
        undo.clicked.connect(self.undo_last)
        retry = QPushButton("Retry failed")
        retry.clicked.connect(self.retry_failed)
        save = QPushButton("Save Changes")
        save.setObjectName("Primary")
        save.clicked.connect(self.save_changes)
        self.save_btn = save
        buttons.addWidget(undo)
        buttons.addWidget(retry)
        buttons.addStretch()
        buttons.addWidget(save)
        l.addLayout(buttons)
        return page

    def _search_page(self):
        page = QWidget()
        l = QVBoxLayout(page)
        l.setContentsMargins(34, 28, 34, 26)
        title = QLabel("Search")
        title.setObjectName("PageTitle")
        l.addWidget(title)
        self.global_search = QLineEdit()
        self.global_search.setPlaceholderText("Search across every accessible library…")
        l.addWidget(self.global_search)
        go = QPushButton("Search library")
        go.setObjectName("Primary")
        go.clicked.connect(self.run_global_search)
        l.addWidget(go, 0, Qt.AlignLeft)
        self.search_results = QListWidget()
        self.search_results.itemDoubleClicked.connect(self.search_result_open)
        l.addWidget(self.search_results, 1)
        return page

    # ---------- library ----------
    def refresh_roots(self):
        self.folder_list.clear()
        valid = []
        for r in self.config.get("roots", []):
            p = resolved(r)
            if p.exists() and p.is_dir():
                valid.append(str(p))
                item = QListWidgetItem(f"📁  {p.name or p}")
                item.setData(Qt.UserRole, str(p))
                self.folder_list.addItem(item)
        self.config["roots"] = valid
        save_config(self.config)
        if self.folder_list.count() and self.folder_list.currentRow() < 0:
            self.folder_list.setCurrentRow(0)
        elif not self.folder_list.count():
            self.current_path = None
            self.current_label.setText("Add a library folder to begin")
            self.items.clear()

    def open_root_item(self, current, _previous=None):
        if not current:
            return
        self.current_path = resolved(current.data(Qt.UserRole))
        self.open_current_folder()

    def open_current_folder(self):
        if not self.current_path:
            return
        self.current_label.setText(str(self.current_path))
        self.breadcrumb.setText(f"Library  /  {self.current_path.name}")
        self.filter_current_folder(self.search_box.text())

    def filter_current_folder(self, text=""):
        if not self.current_path or not self.current_path.exists():
            return
        needle = (text or "").strip().lower()
        self.items.clear()
        try:
            children = sorted(self.current_path.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower()))
            for p in children:
                if needle and needle not in p.name.lower():
                    continue
                marker = self.pending_marker_for(p)
                it = QListWidgetItem(f"{marker}{icon_for(p)}  {p.name}")
                it.setData(Qt.UserRole, str(p))
                it.setToolTip(str(p))
                self.items.addItem(it)
        except OSError as exc:
            QMessageBox.warning(self, "Cannot read folder", str(exc))

    def enter_item(self, item, _column=0):
        p = resolved(item.data(Qt.UserRole))
        if p.exists() and p.is_dir():
            self.current_path = p
            self.open_current_folder()

    def go_up(self):
        if not self.current_path:
            return
        roots = [resolved(r) for r in self.config.get("roots", [])]
        if any(self.current_path == r for r in roots):
            return
        self.current_path = self.current_path.parent
        self.open_current_folder()

    def remove_root(self):
        item = self.folder_list.currentItem()
        if not item:
            return
        p = resolved(item.data(Qt.UserRole))
        answer = QMessageBox.question(self, "Remove library", f"Remove this folder from TuneVault?\n\n{p}\n\nFiles will NOT be deleted.")
        if answer != QMessageBox.Yes:
            return
        self.config["roots"] = [r for r in self.config["roots"] if resolved(r) != p]
        save_config(self.config)
        self.refresh_roots()
        self.status.setText("Library access removed")

    # ---------- staging ----------
    def path_allowed(self, path: Path) -> bool:
        p = resolved(path)
        return any(p == resolved(r) or is_under(p, resolved(r)) for r in self.config.get("roots", []))

    def _stage(self, change):
        change["id"] = change.get("id") or uuid.uuid4().hex
        change["status"] = "pending"
        self.pending.append(change)
        self.status.setText(f"🟡 {len(self.pending)} unsaved change(s)")
        self._refresh_queue()
        self.filter_current_folder(getattr(self, "search_box", QLineEdit()).text())

    def pending_marker_for(self, path):
        p = resolved(path)
        for change in reversed(self.pending):
            if change.get("op") == "rename" and resolved(change.get("new")) == p:
                return "🟡 "
            if change.get("op") in {"mkdir", "delete", "move"} and resolved(change.get("path") or change.get("old")) == p:
                return "🟡 "
        return ""

    def add_root(self):
        path = QFileDialog.getExistingDirectory(self, "Choose a library folder")
        if not path:
            return
        p = resolved(path)
        roots = [resolved(x) for x in self.config.get("roots", [])]
        if any(p == r or is_under(p, r) or is_under(r, p) for r in roots):
            QMessageBox.information(self, "Already accessible", "This folder already exists or overlaps an accessible library folder.")
            return
        self.config["roots"].append(str(p))
        save_config(self.config)
        self.refresh_roots()
        self.status.setText("🟢 Library access added")

    def add_subfolder(self):
        if not self.current_path:
            return
        name, ok = self._input("New folder", "Folder name")
        if not ok or not name.strip():
            return
        target = resolved(self.current_path / safe_name(name))
        if target.exists() or not self.path_allowed(target):
            QMessageBox.warning(self, "Cannot create", "That folder name already exists or is outside the allowed library.")
            return
        self._stage({"op": "mkdir", "path": str(target), "label": f"Create folder · {target.name}"})
        self.open_current_folder()

    def add_media(self):
        if not self.current_path:
            return
        dialog = MediaDialog(self.current_path, self)
        if dialog.exec() != QDialog.Accepted:
            return
        payloads = dialog.payloads()
        if len(payloads) > 1:
            batch = PlaylistFormatDialog(payloads, self)
            if batch.exec() != QDialog.Accepted:
                return
        for payload in payloads:
            self._stage({
                "op": "download", "item": payload,
                "label": f"Download · {payload['title']} [{payload['format']}]",
            })
        self.pages.setCurrentIndex(1)

    def rename_item(self):
        it = self.items.currentItem()
        if not it:
            return
        old = resolved(it.data(Qt.UserRole))
        name, ok = self._input("Rename", "New name", old.name)
        if not ok or not name.strip():
            return
        new = resolved(old.parent / safe_name(name))
        if new == old:
            return
        if new.exists() or not self.path_allowed(new):
            QMessageBox.warning(self, "Cannot rename", "The name already exists or is outside an accessible library.")
            return
        self._stage({"op": "rename", "old": str(old), "new": str(new), "label": f"Rename · {old.name} → {new.name}"})
        self.open_current_folder()

    def move_item(self):
        it = self.items.currentItem()
        if not it:
            return
        old = resolved(it.data(Qt.UserRole))
        destination = QFileDialog.getExistingDirectory(self, "Choose destination folder", str(old.parent))
        if not destination:
            return
        destination = resolved(destination)
        if not self.path_allowed(destination):
            QMessageBox.warning(self, "Blocked", "The destination must be inside an accessible library.")
            return
        new = destination / old.name
        if new == old or new.exists():
            QMessageBox.warning(self, "Cannot move", "The destination is invalid or already contains that item.")
            return
        self._stage({"op": "move", "old": str(old), "path": str(new), "label": f"Move · {old.name} → {destination}"})
        self.open_current_folder()

    def delete_item(self):
        it = self.items.currentItem()
        if not it:
            return
        p = resolved(it.data(Qt.UserRole))
        answer = QMessageBox.question(self, "Delete", f"Stage deletion of:\n\n{p.name}\n\nNothing is deleted until Save Changes.")
        if answer != QMessageBox.Yes:
            return
        self._stage({"op": "delete", "path": str(p), "label": f"Delete · {p.name}"})
        self.open_current_folder()

    # ---------- apply ----------
    def save_changes(self):
        if not self.pending:
            self.status.setText("No staged changes")
            return
        if not self.save_btn.isEnabled():
            return
        snapshot = [dict(x) for x in self.pending]
        self.save_btn.setEnabled(False)
        self.save_btn.setText("Saving…")
        self.status.setText(f"Applying {len(snapshot)} staged change(s)…")
        worker = Worker(self._apply_all, snapshot)
        worker.signals.result.connect(self._save_result)
        worker.signals.error.connect(self._save_error)
        self.pool.start(worker)

    def _apply_all(self, changes):
        results = []
        for change in changes:
            try:
                self._apply_one(change)
                results.append({"id": change["id"], "status": "success", "error": ""})
            except Exception as exc:
                results.append({"id": change["id"], "status": "failed", "error": str(exc)})
        return results

    def _apply_one(self, change):
        op = change["op"]
        if op == "mkdir":
            Path(change["path"]).mkdir(parents=False, exist_ok=False)
        elif op == "rename":
            Path(change["old"]).rename(change["new"])
        elif op == "move":
            Path(change["old"]).rename(change["path"])
        elif op == "delete":
            p = Path(change["path"])
            if p.is_dir():
                shutil.rmtree(p)
            else:
                p.unlink()
        elif op == "download":
            self._download(change["item"])
        else:
            raise ValueError(f"Unknown operation: {op}")

    def _download(self, item):
        import imageio_ffmpeg
        import yt_dlp
        target = resolved(item["target"])
        if not self.path_allowed(target):
            raise ValueError("Download target is outside an accessible library")
        target.mkdir(parents=True, exist_ok=True)
        title = safe_name(item["title"])
        output = str(target / f"{title}.%(ext)s")
        ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
        if item["format"] == "MP3":
            opts = {
                "format": "bestaudio/best",
                "outtmpl": output,
                "ffmpeg_location": ffmpeg,
                "postprocessors": [{"key": "FFmpegExtractAudio", "preferredcodec": "mp3", "preferredquality": "192"}],
                "noplaylist": True, "quiet": True, "no_warnings": True,
            }
        else:
            opts = {
                "format": "bestvideo+bestaudio/best",
                "outtmpl": output,
                "ffmpeg_location": ffmpeg,
                "merge_output_format": "mp4",
                "noplaylist": True, "quiet": True, "no_warnings": True,
            }
        with yt_dlp.YoutubeDL(opts) as ydl:
            ydl.download([item["url"]])

    def _save_result(self, results):
        lookup = {r["id"]: r for r in results}
        completed = 0
        failed = 0
        for change in self.pending[:]:
            result = lookup.get(change["id"])
            if not result:
                continue
            if result["status"] == "success":
                change["status"] = "success"
                change["timestamp"] = datetime.now().isoformat(timespec="seconds")
                self.history.append(dict(change))
                self.pending.remove(change)
                completed += 1
            else:
                change["status"] = "failed"
                change["error"] = result["error"]
                failed += 1
        self.save_btn.setEnabled(True)
        self.save_btn.setText("Save Changes")
        self.status.setText(f"🔴 {failed} failed · 🟢 {completed} applied" if failed else f"🟢 Saved {completed} change(s)")
        self._refresh_queue()
        self.open_current_folder()

    def _save_error(self, error):
        self.save_btn.setEnabled(True)
        self.save_btn.setText("Save Changes")
        self.status.setText("🔴 Save engine failed")
        QMessageBox.critical(self, "Save failed", error)

    # ---------- queue ----------
    def _refresh_queue(self):
        if not hasattr(self, "queue"):
            return
        self.queue.clear()
        for change in self.pending:
            marker = "🟡" if change.get("status") == "pending" else "🔴"
            label = change.get("label") or change.get("op", "Change")
            error = f" — {change['error']}" if change.get("error") else ""
            it = QListWidgetItem(f"{marker}  {label}{error}")
            it.setData(Qt.UserRole, change["id"])
            self.queue.addItem(it)
        for change in reversed(self.history[-50:]):
            self.queue.addItem(QListWidgetItem(f"🟢  {change.get('label') or change.get('op', 'Change')}"))
        self.pending_card.setText(f"🟡 {len([x for x in self.pending if x.get('status') == 'pending'])} staged")
        self.success_card.setText(f"🟢 {sum(1 for x in self.history if x.get('status') == 'success')} succeeded")
        self.failed_card.setText(f"🔴 {sum(1 for x in self.pending if x.get('status') == 'failed')} failed")

    def undo_last(self):
        if not self.pending:
            QMessageBox.information(self, "Nothing to undo", "There are no staged actions.")
            return
        change = self.pending.pop()
        self.status.setText(f"Undid: {change.get('label', change.get('op'))}")
        self._refresh_queue()
        self.open_current_folder()

    def retry_failed(self):
        failed = [x for x in self.pending if x.get("status") == "failed"]
        if not failed:
            QMessageBox.information(self, "Nothing to retry", "There are no failed operations.")
            return
        for change in failed:
            change["status"] = "pending"
            change.pop("error", None)
        self._refresh_queue()
        self.save_changes()

    # ---------- search ----------
    def run_global_search(self):
        text = self.global_search.text().strip().lower()
        if not text:
            return
        self.search_results.clear()
        roots = [resolved(r) for r in self.config.get("roots", [])]
        worker = Worker(self._search_files, roots, text)
        worker.signals.result.connect(self._show_search_results)
        worker.signals.error.connect(lambda e: QMessageBox.warning(self, "Search failed", e))
        QThreadPool.globalInstance().start(worker)

    def _search_files(self, roots, text):
        results = []
        for root in roots:
            if not root.exists():
                continue
            try:
                for p in root.rglob("*"):
                    try:
                        if text in p.name.lower():
                            results.append(str(p))
                            if len(results) >= 1000:
                                return results
                    except OSError:
                        continue
            except OSError:
                continue
        return results

    def _show_search_results(self, results):
        self.search_results.clear()
        for path in results:
            p = Path(path)
            it = QListWidgetItem(f"{icon_for(p)}  {p.name}    ·    {p.parent}")
            it.setData(Qt.UserRole, str(p))
            self.search_results.addItem(it)
        self.status.setText(f"Found {len(results)} result(s)")

    def search_result_open(self, item):
        p = resolved(item.data(Qt.UserRole))
        self.current_path = p if p.is_dir() else p.parent
        self.pages.setCurrentIndex(0)
        self.open_current_folder()

    def _light_refresh(self):
        if self.pages.currentIndex() == 0 and self.current_path and self.current_path.exists():
            self.filter_current_folder(self.search_box.text())

    def _input(self, title, label, value=""):
        return QInputDialog.getText(self, title, label, text=value)


STYLE = r'''
QWidget { background:#0b0d10; color:#e9edf2; font-family:"Segoe UI"; font-size:14px; }
#Nav { background:#101318; border-right:1px solid #252a31; }
#Brand { font-size:27px; font-weight:700; letter-spacing:-0.5px; }
#PageTitle { font-size:32px; font-weight:700; letter-spacing:-0.6px; }
#DialogTitle { font-size:24px; font-weight:700; }
#Subtle, #Status { color:#8d96a3; }
#SectionTitle { font-size:17px; font-weight:600; margin-top:8px; }
#StatCard { background:#11161c; border:1px solid #252c35; border-radius:12px; padding:12px 16px; min-width:150px; }
QPushButton { background:#191e25; border:1px solid #2a3038; border-radius:10px; padding:10px 15px; }
QPushButton:hover { background:#222830; }
QPushButton:pressed { background:#2b323b; }
QPushButton:disabled { color:#6c7480; }
#Primary { background:#eef1f4; color:#0b0d10; border:none; font-weight:700; }
#Primary:hover { background:#ffffff; }
#NavButton { text-align:left; background:transparent; border:none; padding:12px; color:#b8c0ca; }
#NavButton:hover { background:#1a1f26; color:#fff; }
QLineEdit, QComboBox { background:#11161c; border:1px solid #2b323b; border-radius:10px; padding:11px; }
QLineEdit:focus, QComboBox:focus { border:1px solid #68727e; }
QListWidget { background:#0f1318; border:1px solid #242a32; border-radius:12px; padding:8px; }
QListWidget::item { padding:11px; border-radius:8px; }
QListWidget::item:hover { background:#171d24; }
QListWidget::item:selected { background:#242b34; }
QFrame { border-radius:12px; }
#MediaPreviewTitle { font-size:18px; font-weight:600; }
#Thumb { background:#11161c; border:1px solid #2a3038; border-radius:10px; }
QSplitter::handle { background:#1d232b; }
QMessageBox { background:#11161c; }
QScrollBar:vertical { background:#0b0d10; width:10px; }
QScrollBar::handle:vertical { background:#303741; border-radius:5px; min-height:30px; }
'''


def main():
    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setStyleSheet(STYLE)
    win = MainWindow()
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
