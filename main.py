from __future__ import annotations

import json
import os
import sys
import urllib.request
from pathlib import Path

from PySide6.QtCore import QObject, QRunnable, QThreadPool, Qt, QTimer, Signal, Slot
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QApplication, QComboBox, QDialog, QFileDialog, QFrame, QHBoxLayout,
    QLabel, QLineEdit, QListWidget, QListWidgetItem, QMainWindow, QMessageBox,
    QPushButton, QSplitter, QStackedWidget, QVBoxLayout, QWidget, QInputDialog,
)

from tunevault_engine import ApplyEngine, LibraryModel, Operation, resolve, safe_name

APP_NAME = "TuneVault"
APP_DIR = Path(os.getenv("APPDATA", Path.home())) / APP_NAME
APP_DIR.mkdir(parents=True, exist_ok=True)
CONFIG = APP_DIR / "config.json"


def load_config() -> dict:
    try:
        data = json.loads(CONFIG.read_text(encoding="utf-8")) if CONFIG.exists() else {}
        return data if isinstance(data, dict) else {"roots": []}
    except (OSError, json.JSONDecodeError):
        return {"roots": []}


def save_config(data: dict) -> None:
    tmp = CONFIG.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(CONFIG)


def fmt_time(seconds) -> str:
    if not seconds:
        return ""
    total = max(0, int(seconds))
    h, rem = divmod(total, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def icon_for(path: Path) -> str:
    if path.is_dir():
        return "FOLDER"
    if path.suffix.lower() in {".mp3", ".wav", ".flac", ".m4a", ".aac", ".ogg", ".opus"}:
        return "AUDIO"
    if path.suffix.lower() in {".mp4", ".mkv", ".webm", ".mov", ".avi", ".m4v"}:
        return "VIDEO"
    return "FILE"


class Signals(QObject):
    result = Signal(object)
    error = Signal(str)
    done = Signal()


class Worker(QRunnable):
    def __init__(self, fn, *args):
        super().__init__()
        self.fn, self.args = fn, args
        self.signals = Signals()

    @Slot()
    def run(self):
        try:
            self.signals.result.emit(self.fn(*self.args))
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
        self.setWindowTitle("TuneVault · Add media")
        self.resize(760, 620)
        box = QVBoxLayout(self)

        head = QFrame(); head.setObjectName("Panel")
        hv = QVBoxLayout(head)
        title = QLabel("Add media"); title.setObjectName("DialogTitle")
        hv.addWidget(title)
        hv.addWidget(QLabel("Detect a media URL, configure it, then stage it. Nothing is downloaded until Save Changes."))
        box.addWidget(head)

        urlrow = QHBoxLayout()
        self.url = QLineEdit(); self.url.setPlaceholderText("Paste a media or playlist URL…")
        self.detect = QPushButton("Detect")
        self.detect.setObjectName("Primary")
        urlrow.addWidget(self.url, 1); urlrow.addWidget(self.detect)
        box.addLayout(urlrow)

        preview = QFrame(); preview.setObjectName("Panel")
        pv = QHBoxLayout(preview)
        self.thumb = QLabel("NO\nPREVIEW"); self.thumb.setAlignment(Qt.AlignCenter); self.thumb.setObjectName("Thumb")
        self.thumb.setFixedSize(200, 112)
        pv.addWidget(self.thumb)
        meta = QVBoxLayout()
        self.preview_title = QLabel("No media detected"); self.preview_title.setObjectName("MediaTitle")
        self.preview_meta = QLabel("Paste a URL and press Detect"); self.preview_meta.setObjectName("Muted")
        self.preview_kind = QLabel(""); self.preview_kind.setObjectName("Muted")
        meta.addWidget(self.preview_title); meta.addWidget(self.preview_meta); meta.addWidget(self.preview_kind); meta.addStretch()
        pv.addLayout(meta, 1)
        box.addWidget(preview)

        self.mode = QComboBox(); self.mode.addItems(["Single", "Playlist"]); self.mode.setEnabled(False)
        self.name = QLineEdit(); self.name.setPlaceholderText("File name"); self.name.setEnabled(False)
        self.format = QComboBox(); self.format.addItems(["MP3", "MP4"]); self.format.setEnabled(False)
        box.addWidget(QLabel("Mode")); box.addWidget(self.mode)
        box.addWidget(QLabel("Name")); box.addWidget(self.name)
        fr = QHBoxLayout(); fr.addWidget(QLabel("Format")); fr.addWidget(self.format, 1); box.addLayout(fr)

        self.batch = QListWidget(); self.batch.setVisible(False); box.addWidget(self.batch, 1)
        buttons = QHBoxLayout(); buttons.addStretch()
        cancel = QPushButton("Cancel"); add = QPushButton("Stage media"); add.setObjectName("Primary")
        add.setEnabled(False); buttons.addWidget(cancel); buttons.addWidget(add); box.addLayout(buttons)

        self.detect.clicked.connect(self.detect_media)
        self.mode.currentIndexChanged.connect(self._mode_changed)
        cancel.clicked.connect(self.reject); add.clicked.connect(self.accept)
        self.add_btn = add

    def detect_media(self):
        url = self.url.text().strip()
        if not url: return
        self.detect.setEnabled(False); self.detect.setText("Detecting…")
        w = Worker(self._extract, url)
        w.signals.result.connect(self._detected)
        w.signals.error.connect(lambda e: QMessageBox.warning(self, "Detection failed", e))
        w.signals.done.connect(lambda: (self.detect.setEnabled(True), self.detect.setText("Detect")))
        QThreadPool.globalInstance().start(w)

    @staticmethod
    def _extract(url):
        import yt_dlp
        opts = {"quiet": True, "no_warnings": True, "skip_download": True, "noplaylist": False}
        with yt_dlp.YoutubeDL(opts) as ydl:
            return ydl.extract_info(url, download=False)

    def _detected(self, info):
        self.info = info or {}
        self.entries = [e for e in (self.info.get("entries") or []) if e]
        title = self.info.get("title") or "Untitled media"
        creator = self.info.get("uploader") or self.info.get("channel") or ""
        dur = fmt_time(self.info.get("duration"))
        self.preview_title.setText(title)
        self.preview_meta.setText(" · ".join(x for x in (creator, dur) if x))
        playlist = len(self.entries) > 1
        self.preview_kind.setText(f"Playlist · {len(self.entries)} items" if playlist else "Single media")
        self.mode.setEnabled(playlist); self.mode.setCurrentIndex(1 if playlist else 0)
        self.name.setText(title); self.name.setEnabled(True); self.format.setEnabled(True); self.add_btn.setEnabled(True)
        self.batch.clear()
        for entry in self.entries:
            self.batch.addItem(QListWidgetItem(entry.get("title") or "Untitled"))
        self._mode_changed(self.mode.currentIndex())
        thumb_url = self.info.get("thumbnail")
        if thumb_url:
            w = Worker(self._thumb, thumb_url); w.signals.result.connect(self._show_thumb)
            QThreadPool.globalInstance().start(w)

    @staticmethod
    def _thumb(url):
        with urllib.request.urlopen(url, timeout=12) as r:
            return r.read()

    def _show_thumb(self, data):
        pix = QPixmap();
        if pix.loadFromData(data):
            self.thumb.setText(""); self.thumb.setPixmap(pix.scaled(self.thumb.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation))

    def _mode_changed(self, index):
        playlist = index == 1 and bool(self.entries)
        self.batch.setVisible(playlist)
        self.name.setVisible(not playlist)
        self.format.setVisible(not playlist)

    def payloads(self):
        if self.mode.currentIndex() == 1 and self.entries:
            out = []
            for entry in self.entries:
                url = entry.get("webpage_url") or entry.get("original_url")
                if not url: continue
                out.append({"url": url, "title": entry.get("title") or "Untitled", "format": "MP3", "target": str(self.target)})
            return out
        return [{"url": self.url.text().strip(), "title": self.name.text().strip() or "Untitled", "format": self.format.currentText(), "target": str(self.target)}]


class PlaylistDialog(QDialog):
    def __init__(self, payloads, parent=None):
        super().__init__(parent); self.payloads = payloads
        self.setWindowTitle("TuneVault · Playlist"); self.resize(820, 650)
        l = QVBoxLayout(self)
        t = QLabel("Playlist options"); t.setObjectName("DialogTitle"); l.addWidget(t)
        l.addWidget(QLabel(f"{len(payloads)} items are ready. Choose a format for each or apply one format to all."))
        quick = QHBoxLayout()
        for label, fmt in (("All MP3", "MP3"), ("All MP4", "MP4")):
            b = QPushButton(label); b.clicked.connect(lambda _, f=fmt: self.set_all(f)); quick.addWidget(b)
        quick.addStretch(); l.addLayout(quick)
        self.rows = QListWidget(); l.addWidget(self.rows, 1)
        for p in payloads:
            row = QListWidgetItem(); w = QWidget(); r = QHBoxLayout(w); r.setContentsMargins(10, 6, 10, 6)
            text = QLabel(p["title"]); combo = QComboBox(); combo.addItems(["MP3", "MP4"])
            combo.currentTextChanged.connect(lambda v, item=p: item.__setitem__("format", v))
            r.addWidget(text, 1); r.addWidget(combo); self.rows.addItem(row); self.rows.setItemWidget(row, w)
        actions = QHBoxLayout(); actions.addStretch(); cancel = QPushButton("Cancel"); ok = QPushButton("Stage playlist"); ok.setObjectName("Primary")
        cancel.clicked.connect(self.reject); ok.clicked.connect(self.accept); actions.addWidget(cancel); actions.addWidget(ok); l.addLayout(actions)

    def set_all(self, fmt):
        for i, p in enumerate(self.payloads):
            p["format"] = fmt
            combo = self.rows.itemWidget(self.rows.item(i)).findChild(QComboBox)
            if combo: combo.setCurrentText(fmt)


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.config = load_config(); self.model = LibraryModel(self.config.get("roots", [])); self.current_path = None; self.pool = QThreadPool.globalInstance(); self.applying = False
        self.setWindowTitle(APP_NAME); self.resize(1440, 900); self.setMinimumSize(1180, 760)
        self._build(); self.refresh_roots()
        self.timer = QTimer(self); self.timer.timeout.connect(self.external_refresh); self.timer.start(5000)

    def _build(self):
        central = QWidget(); self.setCentralWidget(central); root = QHBoxLayout(central); root.setContentsMargins(0,0,0,0); root.setSpacing(0)
        nav = QFrame(); nav.setObjectName("Sidebar"); nav.setFixedWidth(250); nv = QVBoxLayout(nav); nv.setContentsMargins(22,28,22,22)
        brand = QLabel("TuneVault"); brand.setObjectName("Brand"); nv.addWidget(brand); sub = QLabel("Your media. Your folders. Your computer."); sub.setObjectName("Muted"); sub.setWordWrap(True); nv.addWidget(sub); nv.addSpacing(26)
        self.nav_buttons = []
        for label, idx in (("Library",0),("Downloads",1),("Search",2)):
            b = QPushButton(label); b.setObjectName("NavButton"); b.clicked.connect(lambda _, i=idx: self.pages.setCurrentIndex(i)); nv.addWidget(b); self.nav_buttons.append(b)
        nv.addStretch(); self.sidebar_status = QLabel("READY"); self.sidebar_status.setObjectName("StatusPill"); nv.addWidget(self.sidebar_status); root.addWidget(nav)
        self.pages = QStackedWidget(); root.addWidget(self.pages, 1); self.pages.addWidget(self.library_page()); self.pages.addWidget(self.downloads_page()); self.pages.addWidget(self.search_page())

    def header(self, title, subtitle):
        box = QVBoxLayout(); t = QLabel(title); t.setObjectName("PageTitle"); s = QLabel(subtitle); s.setObjectName("Muted"); box.addWidget(t); box.addWidget(s); return box

    def library_page(self):
        page=QWidget(); l=QVBoxLayout(page); l.setContentsMargins(34,28,34,28)
        top=self.header("Library", "A focused media workspace. Edits are staged and committed only when you save.")
        head=QHBoxLayout(); head.addLayout(top,1); self.folder_search=QLineEdit(); self.folder_search.setPlaceholderText("Filter current folder…"); self.folder_search.textChanged.connect(self.render_items); head.addWidget(self.folder_search)
        add=QPushButton("＋ Add library"); add.setObjectName("Primary"); add.clicked.connect(self.add_root); head.addWidget(add); l.addLayout(head)
        split=QSplitter(Qt.Horizontal); l.addWidget(split,1)
        left=QWidget(); lv=QVBoxLayout(left); lv.setContentsMargins(0,0,0,0); ll=QLabel("LIBRARIES"); ll.setObjectName("Eyebrow"); lv.addWidget(ll)
        self.roots_list=QListWidget(); lv.addWidget(self.roots_list,1); remove=QPushButton("Remove access"); remove.clicked.connect(self.remove_root); lv.addWidget(remove); split.addWidget(left)
        right=QWidget(); rv=QVBoxLayout(right); rv.setContentsMargins(18,0,0,0)
        bar=QHBoxLayout(); self.path_label=QLabel("Select a library"); self.path_label.setObjectName("SectionTitle"); bar.addWidget(self.path_label,1)
        for label, fn in (("↑ Up",self.go_up),("↻ Refresh",self.render_items)):
            b=QPushButton(label); b.clicked.connect(fn); bar.addWidget(b)
        rv.addLayout(bar)
        self.items=QListWidget(); self.items.itemDoubleClicked.connect(self.open_item); rv.addWidget(self.items,1)
        acts=QHBoxLayout();
        for label, fn in (("＋ Folder",self.add_folder),("＋ Media",self.add_media),("Rename",self.rename_item),("Move",self.move_item),("Delete",self.delete_item)):
            b=QPushButton(label); b.clicked.connect(fn); acts.addWidget(b)
        acts.addStretch(); self.save_button=QPushButton("Save Changes"); self.save_button.setObjectName("Primary"); self.save_button.clicked.connect(self.save_changes); acts.addWidget(self.save_button); rv.addLayout(acts)
        split.addWidget(right); split.setSizes([320, 980]); self.roots_list.currentItemChanged.connect(self.root_selected); return page

    def downloads_page(self):
        page=QWidget(); l=QVBoxLayout(page); l.setContentsMargins(34,28,34,28); l.addLayout(self.header("Activity", "Every staged and completed action is visible here."))
        cards=QHBoxLayout(); self.staged=QLabel("0 STAGED"); self.done=QLabel("0 SUCCEEDED"); self.failed=QLabel("0 FAILED")
        for c in (self.staged,self.done,self.failed): c.setObjectName("StatCard"); cards.addWidget(c)
        l.addLayout(cards); self.queue=QListWidget(); l.addWidget(self.queue,1); row=QHBoxLayout(); undo=QPushButton("Undo last"); undo.clicked.connect(self.undo_last); retry=QPushButton("Retry failed"); retry.clicked.connect(self.retry_failed); row.addWidget(undo); row.addWidget(retry); row.addStretch(); l.addLayout(row); return page

    def search_page(self):
        page=QWidget(); l=QVBoxLayout(page); l.setContentsMargins(34,28,34,28); l.addLayout(self.header("Search", "Search across every library you have explicitly granted."))
        row=QHBoxLayout(); self.global_search=QLineEdit(); self.global_search.setPlaceholderText("Search all libraries…"); go=QPushButton("Search"); go.setObjectName("Primary"); go.clicked.connect(self.run_search); row.addWidget(self.global_search,1); row.addWidget(go); l.addLayout(row); self.results=QListWidget(); self.results.itemDoubleClicked.connect(self.open_search); l.addWidget(self.results,1); return page

    def refresh_roots(self):
        self.roots_list.clear(); valid=[]
        for root in self.config.get("roots",[]):
            p=resolve(root)
            if p.exists() and p.is_dir(): valid.append(str(p)); it=QListWidgetItem(p.name or str(p)); it.setData(Qt.UserRole,str(p)); self.roots_list.addItem(it)
        self.config["roots"]=valid; save_config(self.config); self.model.roots=valid
        if self.roots_list.count() and self.roots_list.currentRow()<0: self.roots_list.setCurrentRow(0)
        if not self.roots_list.count(): self.current_path=None; self.path_label.setText("Add a library to begin"); self.items.clear()

    def add_root(self):
        path=QFileDialog.getExistingDirectory(self,"Choose library folder")
        if not path:return
        p=resolve(path); roots=[resolve(x) for x in self.config.get("roots",[])]
        try:
            overlap=any(p==r or p.is_relative_to(r) or r.is_relative_to(p) for r in roots)
        except AttributeError:
            overlap=any(p==r for r in roots)
        if overlap: QMessageBox.information(self,"Already accessible","This folder overlaps an existing library."); return
        self.config["roots"].append(str(p)); self.config["roots"] = [str(resolve(x)) for x in self.config["roots"]]; save_config(self.config); self.model.roots=self.config["roots"]; self.refresh_roots()

    def remove_root(self):
        it=self.roots_list.currentItem()
        if not it:return
        p=resolve(it.data(Qt.UserRole))
        if QMessageBox.question(self,"Remove access",f"Remove {p.name} from TuneVault?\n\nNo files will be deleted.")==QMessageBox.Yes:
            self.config["roots"]=[r for r in self.config.get("roots",[]) if resolve(r)!=p]; save_config(self.config); self.model.roots=self.config["roots"]; self.refresh_roots()

    def root_selected(self,current,_prev=None):
        if current:self.current_path=resolve(current.data(Qt.UserRole)); self.render_items()

    def path_allowed(self,p): return self.model.allowed(p)

    def virtual_name_conflict(self,path):
        folder=resolve(Path(path).parent); name=resolve(path).name.lower()
        return any(p.name.lower()==name for p,_ in self.model.virtual_entries(folder))

    def render_items(self,*_):
        if not self.current_path:return
        self.path_label.setText(str(self.current_path)); needle=self.folder_search.text().strip().lower(); self.items.clear()
        for p, state in self.model.virtual_entries(self.current_path):
            if needle and needle not in p.name.lower():continue
            marker={"pending":"• ","delete":"✕ "}.get(state,""); it=QListWidgetItem(f"{marker}{p.name}"); it.setData(Qt.UserRole,str(p)); it.setToolTip(f"{icon_for(p)} · {p}"); self.items.addItem(it)
        self.update_activity()

    def open_item(self,item):
        p=resolve(item.data(Qt.UserRole))
        if p.is_dir(): self.current_path=p; self.render_items()

    def go_up(self):
        if not self.current_path:return
        roots=[resolve(r) for r in self.config.get("roots",[])]
        if self.current_path not in roots:self.current_path=self.current_path.parent; self.render_items()

    def add_folder(self):
        if not self.current_path:return
        name,ok=QInputDialog.getText(self,"New folder","Folder name")
        if not ok:return
        target=resolve(self.current_path/safe_name(name))
        if self.virtual_name_conflict(target): QMessageBox.warning(self,"Folder exists","Choose a different name."); return
        self.model.stage(Operation("mkdir",f"Create folder · {target.name}",path=str(target))); self.render_items(); self.set_dirty()

    def add_media(self):
        if not self.current_path:return
        d=MediaDialog(self.current_path,self)
        if d.exec()!=QDialog.Accepted:return
        payloads=d.payloads()
        if len(payloads)>1:
            p=PlaylistDialog(payloads,self)
            if p.exec()!=QDialog.Accepted:return
        for payload in payloads:self.model.stage(Operation("download",f"Download · {payload['title']} [{payload['format']}]",payload=payload))
        self.set_dirty(); self.pages.setCurrentIndex(1)

    def selected_path(self):
        it=self.items.currentItem(); return resolve(it.data(Qt.UserRole)) if it else None

    def rename_item(self):
        old=self.selected_path()
        if not old:return
        name,ok=QInputDialog.getText(self,"Rename","New name",text=old.name)
        if not ok:return
        new=resolve(old.parent/safe_name(name))
        if new==old:return
        if self.virtual_name_conflict(new): QMessageBox.warning(self,"Name in use","That name already exists in this folder."); return
        self.model.stage(Operation("rename",f"Rename · {old.name} → {new.name}",old=str(old),new=str(new))); self.render_items(); self.set_dirty()

    def move_item(self):
        old=self.selected_path()
        if not old:return
        dest=QFileDialog.getExistingDirectory(self,"Choose destination folder",str(old.parent))
        if not dest:return
        dest=resolve(dest); new=dest/old.name
        if not self.path_allowed(dest) or self.virtual_name_conflict(new): QMessageBox.warning(self,"Move blocked","Choose an accessible destination without a conflicting name."); return
        self.model.stage(Operation("move",f"Move · {old.name} → {dest}",old=str(old),new=str(new))); self.render_items(); self.set_dirty()

    def delete_item(self):
        p=self.selected_path()
        if not p:return
        if QMessageBox.question(self,"Stage deletion",f"Stage deletion of {p.name}?\n\nNothing is removed until Save Changes.")==QMessageBox.Yes:
            self.model.stage(Operation("delete",f"Delete · {p.name}",path=str(p))); self.render_items(); self.set_dirty()

    def set_dirty(self):
        self.sidebar_status.setText(f"UNSAVED · {len(self.model.pending)}"); self.save_button.setEnabled(True); self.update_activity()

    def save_changes(self):
        if not self.model.pending or self.applying:return
        self.applying=True; self.save_button.setEnabled(False); self.save_button.setText("Saving…")
        engine=ApplyEngine(self.model); self.current_engine=engine
        w=Worker(engine.apply); w.signals.result.connect(self.save_result); w.signals.error.connect(self.save_error); self.pool.start(w)

    def save_result(self,results):
        for op in results:
            if op.status=="success":
                self.model.history.append(op); self.model.pending.remove(op)
        self.model.pending=[op for op in self.model.pending if op.status!="cancelled"]
        self.applying=False; self.save_button.setEnabled(bool(self.model.pending)); self.save_button.setText("Save Changes"); self.sidebar_status.setText("SAVED" if not self.model.pending else f"UNSAVED · {len(self.model.pending)}"); self.render_items(); self.update_activity()

    def save_error(self,error):
        self.applying=False; self.save_button.setEnabled(True); self.save_button.setText("Save Changes"); self.sidebar_status.setText("ERROR"); QMessageBox.critical(self,"Save engine error",error)

    def undo_last(self):
        op=self.model.undo_last()
        if op:self.sidebar_status.setText("UNDO"); self.render_items(); self.update_activity()

    def retry_failed(self):
        changed=False
        for op in self.model.pending:
            if op.status=="failed": op.status="pending"; op.error=""; changed=True
        if changed:self.set_dirty()
        self.save_changes()

    def update_activity(self):
        if not hasattr(self,"queue"):return
        self.queue.clear()
        for op in self.model.pending:
            marker="✕" if op.status=="failed" else "•"; err=f" — {op.error}" if op.error else ""; self.queue.addItem(QListWidgetItem(f"{marker}  {op.label}{err}"))
        for op in reversed(self.model.history[-80:]): self.queue.addItem(QListWidgetItem(f"✓  {op.label}"))
        self.staged.setText(f"{sum(1 for x in self.model.pending if x.status=='pending')} STAGED"); self.done.setText(f"{sum(1 for x in self.model.history if x.status=='success')} SUCCEEDED"); self.failed.setText(f"{sum(1 for x in self.model.pending if x.status=='failed')} FAILED")

    def external_refresh(self):
        if self.applying:return
        if self.current_path and self.current_path.exists(): self.render_items()

    def run_search(self):
        text=self.global_search.text().strip().lower()
        if not text:return
        self.results.clear(); roots=[resolve(r) for r in self.config.get("roots",[])]
        w=Worker(self.search_worker,roots,text); w.signals.result.connect(self.show_search); w.signals.error.connect(lambda e: QMessageBox.warning(self,"Search failed",e)); self.pool.start(w)

    @staticmethod
    def search_worker(roots,text):
        out=[]
        for root in roots:
            if not root.exists():continue
            try:
                for p in root.rglob("*"):
                    if text in p.name.lower():out.append(str(p))
                    if len(out)>=1500:return out
            except OSError:pass
        return out

    def show_search(self,results):
        self.results.clear()
        for raw in results:
            p=Path(raw); it=QListWidgetItem(f"{p.name}  ·  {p.parent}"); it.setData(Qt.UserRole,raw); self.results.addItem(it)
        self.sidebar_status.setText(f"FOUND · {len(results)}")

    def open_search(self,item):
        p=resolve(item.data(Qt.UserRole)); self.current_path=p if p.is_dir() else p.parent; self.pages.setCurrentIndex(0); self.render_items()

    def closeEvent(self,event):
        if self.model.pending and not self.applying:
            answer=QMessageBox.question(self,"Unsaved changes","You have unsaved changes. Close without saving?")
            if answer!=QMessageBox.Yes:event.ignore(); return
        event.accept()


STYLE = r'''
* { font-family: "Segoe UI"; }
QWidget { background:#0b0d10; color:#edf1f5; font-size:14px; }
#Sidebar { background:#0f1217; border-right:1px solid #222731; }
#Brand { font-size:30px; font-weight:800; letter-spacing:-1px; }
#PageTitle { font-size:34px; font-weight:750; letter-spacing:-1px; }
#DialogTitle { font-size:25px; font-weight:700; }
#SectionTitle { font-size:18px; font-weight:650; }
#Eyebrow { color:#737d8a; font-size:11px; font-weight:800; letter-spacing:1px; }
#Muted { color:#89929e; }
#StatusPill { background:#171c23; border:1px solid #2b323c; border-radius:9px; padding:9px 12px; color:#a9b1bc; font-weight:700; }
#NavButton { background:transparent; border:0; text-align:left; padding:12px 14px; border-radius:10px; color:#aeb6c1; }
#NavButton:hover { background:#181d24; color:#fff; }
QLineEdit,QComboBox { background:#11161c; border:1px solid #2a3038; border-radius:10px; padding:11px 12px; }
QLineEdit:focus,QComboBox:focus { border:1px solid #667180; }
QPushButton { background:#161b21; border:1px solid #2a3038; border-radius:10px; padding:10px 15px; font-weight:600; }
QPushButton:hover { background:#20262d; }
#Primary { background:#f1f4f7; color:#0a0c0f; border:0; font-weight:750; }
#Primary:hover { background:#fff; }
QListWidget { background:#0f1318; border:1px solid #242a33; border-radius:13px; padding:7px; }
QListWidget::item { padding:13px; border-radius:9px; margin:2px 0; }
QListWidget::item:hover { background:#171d24; }
QListWidget::item:selected { background:#232a33; }
#Panel { background:#11161c; border:1px solid #262d36; border-radius:14px; }
#Thumb { background:#0b0d10; border:1px solid #282f38; border-radius:10px; color:#67717d; font-size:11px; font-weight:700; }
#MediaTitle { font-size:19px; font-weight:700; }
#StatCard { background:#11161c; border:1px solid #262d36; border-radius:13px; padding:14px 16px; font-weight:800; }
QSplitter::handle { background:#1d232b; }
QScrollBar:vertical { background:#0b0d10; width:10px; }
QScrollBar::handle:vertical { background:#303741; border-radius:5px; min-height:30px; }
'''


def main():
    app=QApplication(sys.argv); app.setApplicationName(APP_NAME); app.setStyleSheet(STYLE); win=MainWindow(); win.show(); sys.exit(app.exec())

if __name__ == "__main__": main()
