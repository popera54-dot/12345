import json
import os
import shutil
import sys
from pathlib import Path
from typing import Optional

from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal, Slot, Qt
from PySide6.QtWidgets import (
    QApplication, QComboBox, QDialog, QFileDialog, QFrame, QHBoxLayout, QLabel,
    QLineEdit, QListWidget, QListWidgetItem, QMainWindow, QMessageBox, QPushButton,
    QScrollArea, QSizePolicy, QSplitter, QStackedWidget, QVBoxLayout, QWidget
)

APP_DIR = Path(os.getenv("APPDATA", Path.home())) / "TuneVault"
APP_DIR.mkdir(parents=True, exist_ok=True)
CONFIG = APP_DIR / "config.json"


def load_config():
    if CONFIG.exists():
        try:
            return json.loads(CONFIG.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {"roots": []}


def save_config(data):
    CONFIG.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def is_under(child: Path, parent: Path) -> bool:
    try:
        child.resolve().relative_to(parent.resolve())
        return True
    except ValueError:
        return False


class WorkerSignals(QObject):
    result = Signal(object)
    error = Signal(str)
    progress = Signal(str)
    done = Signal()


class Worker(QRunnable):
    def __init__(self, fn, *args, **kwargs):
        super().__init__()
        self.fn, self.args, self.kwargs = fn, args, kwargs
        self.signals = WorkerSignals()

    @Slot()
    def run(self):
        try:
            self.signals.result.emit(self.fn(*self.args, **self.kwargs))
        except Exception as e:
            self.signals.error.emit(str(e))
        finally:
            self.signals.done.emit()


class MediaDialog(QDialog):
    def __init__(self, target: Path, parent=None):
        super().__init__(parent)
        self.target = target
        self.setWindowTitle("Add Media")
        self.setMinimumWidth(560)
        self.setModal(True)
        self.info = None
        layout = QVBoxLayout(self)
        title = QLabel("Add media")
        title.setObjectName("DialogTitle")
        layout.addWidget(title)
        layout.addWidget(QLabel("Paste a link. TuneVault will detect the media automatically."))
        self.url = QLineEdit()
        self.url.setPlaceholderText("Paste a video, audio or playlist URL…")
        layout.addWidget(self.url)
        self.detect = QPushButton("Detect media")
        layout.addWidget(self.detect)
        self.preview = QFrame()
        pv = QVBoxLayout(self.preview)
        self.preview_title = QLabel("No media detected")
        self.preview_title.setObjectName("MediaPreviewTitle")
        self.preview_meta = QLabel("")
        pv.addWidget(self.preview_title)
        pv.addWidget(self.preview_meta)
        layout.addWidget(self.preview)
        self.name = QLineEdit()
        self.name.setPlaceholderText("File name")
        self.name.setEnabled(False)
        layout.addWidget(self.name)
        row = QHBoxLayout()
        row.addWidget(QLabel("Format"))
        self.format = QComboBox()
        self.format.addItems(["MP3", "MP4"])
        self.format.setEnabled(False)
        row.addWidget(self.format)
        layout.addLayout(row)
        self.add = QPushButton("Add to library")
        self.add.setEnabled(False)
        layout.addWidget(self.add)
        self.detect.clicked.connect(self.detect_media)
        self.add.clicked.connect(self.accept)

    def detect_media(self):
        url = self.url.text().strip()
        if not url:
            QMessageBox.warning(self, "Missing link", "Paste a media URL first.")
            return
        self.detect.setEnabled(False)
        self.detect.setText("Detecting…")
        worker = Worker(self._extract, url)
        worker.signals.result.connect(self._detected)
        worker.signals.error.connect(lambda e: QMessageBox.warning(self, "Could not detect", e))
        worker.signals.done.connect(lambda: (self.detect.setEnabled(True), self.detect.setText("Detect media")))
        QThreadPool.globalInstance().start(worker)

    def _extract(self, url):
        import yt_dlp
        opts = {"quiet": True, "no_warnings": True, "skip_download": True, "noplaylist": True}
        with yt_dlp.YoutubeDL(opts) as ydl:
            return ydl.extract_info(url, download=False)

    def _detected(self, info):
        self.info = info
        title = info.get("title") or "Untitled media"
        duration = info.get("duration")
        duration_text = ""
        if duration:
            m, s = divmod(int(duration), 60)
            h, m = divmod(m, 60)
            duration_text = f" • {h}:{m:02d}:{s:02d}" if h else f" • {m}:{s:02d}"
        self.preview_title.setText(title)
        self.preview_meta.setText(f"{info.get('uploader') or 'Unknown creator'}{duration_text}")
        self.name.setText(title)
        self.name.setEnabled(True)
        self.format.setEnabled(True)
        self.add.setEnabled(True)

    def payload(self):
        return {
            "url": self.url.text().strip(),
            "title": self.name.text().strip() or (self.info or {}).get("title", "media"),
            "format": self.format.currentText(),
            "target": str(self.target),
        }


class DownloadWorker(QRunnable):
    def __init__(self, item, callback):
        super().__init__()
        self.item, self.callback = item, callback

    @Slot()
    def run(self):
        try:
            import yt_dlp
            import imageio_ffmpeg
            target = Path(self.item["target"])
            target.mkdir(parents=True, exist_ok=True)
            fmt = self.item["format"]
            safe = self.item["title"].replace("/", "-").replace("\\", "-")
            if fmt == "MP3":
                opts = {
                    "format": "bestaudio/best",
                    "outtmpl": str(target / f"{safe}.%(ext)s"),
                    "ffmpeg_location": imageio_ffmpeg.get_ffmpeg_exe(),
                    "postprocessors": [{"key": "FFmpegExtractAudio", "preferredcodec": "mp3", "preferredquality": "192"}],
                    "noplaylist": True,
                }
            else:
                opts = {
                    "format": "bestvideo+bestaudio/best",
                    "outtmpl": str(target / f"{safe}.%(ext)s"),
                    "ffmpeg_location": imageio_ffmpeg.get_ffmpeg_exe(),
                    "merge_output_format": "mp4",
                    "noplaylist": True,
                }
            with yt_dlp.YoutubeDL(opts) as ydl:
                ydl.download([self.item["url"]])
            self.callback(True, "Download completed")
        except Exception as e:
            self.callback(False, str(e))


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.config = load_config()
        self.pending = []
        self.pool = QThreadPool.globalInstance()
        self.setWindowTitle("TuneVault")
        self.resize(1200, 760)
        self.setMinimumSize(980, 650)
        self._build()
        self.refresh_roots()

    def _build(self):
        central = QWidget()
        self.setCentralWidget(central)
        root = QHBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        nav = QFrame()
        nav.setObjectName("Nav")
        nav.setFixedWidth(230)
        nl = QVBoxLayout(nav)
        nl.setContentsMargins(20, 26, 20, 20)
        brand = QLabel("TuneVault")
        brand.setObjectName("Brand")
        nl.addWidget(brand)
        sub = QLabel("Your media, your way.")
        sub.setObjectName("Subtle")
        nl.addWidget(sub)
        nl.addSpacing(35)
        for text, index in [("⌂  Library", 0), ("↓  Downloads", 1)]:
            b = QPushButton(text)
            b.setObjectName("NavButton")
            b.clicked.connect(lambda _, i=index: self.pages.setCurrentIndex(i))
            nl.addWidget(b)
        nl.addStretch()
        self.status = QLabel("All changes saved")
        self.status.setObjectName("Status")
        nl.addWidget(self.status)
        root.addWidget(nav)

        self.pages = QStackedWidget()
        root.addWidget(self.pages, 1)
        self.library_page = self._library_page()
        self.download_page = self._download_page()
        self.pages.addWidget(self.library_page)
        self.pages.addWidget(self.download_page)

    def _library_page(self):
        page = QWidget(); layout = QVBoxLayout(page); layout.setContentsMargins(38, 30, 38, 30)
        head = QHBoxLayout()
        titlebox = QVBoxLayout()
        title = QLabel("Your Library"); title.setObjectName("PageTitle")
        titlebox.addWidget(title); titlebox.addWidget(QLabel("Manage your real folders. Changes are applied only when you save."))
        head.addLayout(titlebox); head.addStretch()
        add = QPushButton("＋ Add folder"); add.setObjectName("Primary")
        add.clicked.connect(self.add_root); head.addWidget(add)
        layout.addLayout(head)
        self.breadcrumb = QLabel("Root folders"); self.breadcrumb.setObjectName("SectionTitle"); layout.addWidget(self.breadcrumb)
        splitter = QSplitter(Qt.Horizontal); layout.addWidget(splitter, 1)
        self.folder_list = QListWidget(); self.folder_list.setObjectName("FolderList"); splitter.addWidget(self.folder_list)
        right = QWidget(); rv = QVBoxLayout(right); rv.setContentsMargins(18, 0, 0, 0)
        self.current_label = QLabel("Select a folder"); self.current_label.setObjectName("SectionTitle"); rv.addWidget(self.current_label)
        self.items = QListWidget(); self.items.setObjectName("Items"); rv.addWidget(self.items, 1)
        buttons = QHBoxLayout()
        for text, fn in [("＋ Add folder", self.add_subfolder), ("＋ Add media", self.add_media), ("Rename", self.rename_item), ("Delete", self.delete_item)]:
            b=QPushButton(text); b.clicked.connect(fn); buttons.addWidget(b)
        rv.addLayout(buttons); splitter.addWidget(right); splitter.setSizes([300, 700])
        self.folder_list.currentItemChanged.connect(self.open_folder)
        return page

    def _download_page(self):
        page=QWidget(); l=QVBoxLayout(page); l.setContentsMargins(38,30,38,30)
        title=QLabel("Downloads"); title.setObjectName("PageTitle"); l.addWidget(title)
        l.addWidget(QLabel("Queued media is downloaded silently in the background by the local engine."))
        self.queue=QListWidget(); l.addWidget(self.queue,1)
        return page

    def refresh_roots(self):
        self.folder_list.clear()
        for r in self.config["roots"]:
            p=Path(r)
            if p.exists():
                item=QListWidgetItem("📁  " + p.name)
                item.setData(Qt.UserRole, str(p)); self.folder_list.addItem(item)
        if self.folder_list.count(): self.folder_list.setCurrentRow(0)

    def add_root(self):
        path=QFileDialog.getExistingDirectory(self, "Choose a library folder")
        if not path: return
        p=Path(path).resolve()
        roots=[Path(x).resolve() for x in self.config["roots"]]
        if any(p == r or is_under(p,r) or is_under(r,p) for r in roots):
            QMessageBox.information(self, "Already accessible", "This folder already exists or is inside an accessible library folder.")
            return
        self.config["roots"].append(str(p)); save_config(self.config); self.refresh_roots()

    def open_folder(self, current, _previous=None):
        if not current: return
        path=Path(current.data(Qt.UserRole)); self.current_path=path
        self.current_label.setText(str(path))
        self.items.clear()
        try:
            for p in sorted(path.iterdir(), key=lambda x:(not x.is_dir(), x.name.lower())):
                icon="📁" if p.is_dir() else "🎵" if p.suffix.lower() in {".mp3", ".wav", ".flac", ".m4a"} else "🎬" if p.suffix.lower() in {".mp4", ".mkv", ".webm", ".mov"} else "📄"
                it=QListWidgetItem(f"{icon}  {p.name}"); it.setData(Qt.UserRole,str(p)); self.items.addItem(it)
        except OSError as e: QMessageBox.warning(self,"Cannot read folder",str(e))

    def add_subfolder(self):
        if not hasattr(self,"current_path"): return
        name, ok = self._input("New folder", "Folder name")
        if not ok or not name: return
        target=self.current_path / name
        if target.exists(): QMessageBox.warning(self,"Already exists","A folder with this name already exists."); return
        self._stage({"op":"mkdir","path":str(target)})
        self._optimistic_add(target)

    def rename_item(self):
        it=self.items.currentItem()
        if not it: return
        old=Path(it.data(Qt.UserRole)); name,ok=self._input("Rename", "New name", old.name)
        if not ok or not name: return
        new=old.parent/name
        self._stage({"op":"rename","old":str(old),"new":str(new)})
        it.setText(it.text().split("  ")[0]+"  "+name); it.setData(Qt.UserRole,str(new)); self._mark_pending(it)

    def delete_item(self):
        it=self.items.currentItem()
        if not it: return
        p=Path(it.data(Qt.UserRole))
        self._stage({"op":"delete","path":str(p)})
        self._mark_pending(it)

    def add_media(self):
        if not hasattr(self,"current_path"): return
        d=MediaDialog(self.current_path,self)
        if d.exec()!=QDialog.Accepted: return
        item=d.payload(); self._stage({"op":"download","item":item})
        it=QListWidgetItem("🟡  "+item["title"]+" ["+item["format"]+"]"); it.setData(Qt.UserRole,"pending"); self.items.addItem(it)

    def _stage(self, change):
        self.pending.append(change); self.status.setText(f"🟡 {len(self.pending)} unsaved change(s)")
        if not hasattr(self,"save_btn"):
            self.save_btn=QPushButton("Save changes")
            self.save_btn.setObjectName("Save")
            self.status.parentWidget().layout().addWidget(self.save_btn)
            self.save_btn.clicked.connect(self.save_changes)

    def _optimistic_add(self,p):
        it=QListWidgetItem("🟡  📁  "+p.name); it.setData(Qt.UserRole,str(p)); self.items.addItem(it)

    def _mark_pending(self,it):
        text=it.text(); it.setText("🟡  "+text.replace("🟡  ","").replace("🟢  ",""))

    def save_changes(self):
        if not self.pending: return
        changes=self.pending[:]; self.pending.clear(); self.status.setText("Applying changes…")
        self.save_btn.setEnabled(False)
        worker=Worker(self._apply_changes, changes); worker.signals.result.connect(self._save_result); worker.signals.error.connect(self._save_error)
        self.pool.start(worker)

    def _apply_changes(self, changes):
        results=[]
        for c in changes:
            op=c["op"]
            if op=="mkdir": Path(c["path"]).mkdir(parents=False,exist_ok=False)
            elif op=="rename": Path(c["old"]).rename(c["new"])
            elif op=="delete":
                p=Path(c["path"])
                if p.is_dir(): shutil.rmtree(p)
                else: p.unlink()
            elif op=="download":
                # Downloads are handled sequentially here to keep filesystem updates atomic from the UI's perspective.
                import yt_dlp, imageio_ffmpeg
                i=c["item"]; target=Path(i["target"]); target.mkdir(parents=True,exist_ok=True); safe=i["title"].replace("/","-").replace("\\","-")
                if i["format"]=="MP3":
                    opts={"format":"bestaudio/best","outtmpl":str(target/f"{safe}.%(ext)s"),"ffmpeg_location":imageio_ffmpeg.get_ffmpeg_exe(),"postprocessors":[{"key":"FFmpegExtractAudio","preferredcodec":"mp3","preferredquality":"192"}],"noplaylist":True}
                else:
                    opts={"format":"bestvideo+bestaudio/best","outtmpl":str(target/f"{safe}.%(ext)s"),"ffmpeg_location":imageio_ffmpeg.get_ffmpeg_exe(),"merge_output_format":"mp4","noplaylist":True}
                with yt_dlp.YoutubeDL(opts) as ydl: ydl.download([i["url"]])
            results.append(op)
        return results

    def _save_result(self, results):
        self.status.setText(f"🟢 Saved {len(results)} change(s)")
        self.save_btn.setEnabled(True); self.save_btn.setText("Save changes")
        self.refresh_roots()

    def _save_error(self,e):
        self.status.setText("🔴 Some changes failed")
        self.save_btn.setEnabled(True)
        QMessageBox.critical(self,"Save failed",e)
        self.refresh_roots()

    def _input(self,title,label,value=""):
        from PySide6.QtWidgets import QInputDialog
        return QInputDialog.getText(self,title,label,text=value)


def main():
    app=QApplication(sys.argv)
    app.setStyleSheet(STYLE)
    win=MainWindow(); win.show(); sys.exit(app.exec())


STYLE=r'''
QWidget { background:#0b0d10; color:#e9edf2; font-family:"Segoe UI"; font-size:14px; }
#Nav { background:#111419; border-right:1px solid #252a31; }
#Brand { font-size:26px; font-weight:700; }
#PageTitle { font-size:32px; font-weight:700; }
#DialogTitle { font-size:24px; font-weight:700; }
#Subtle, #Status { color:#8d96a3; }
#SectionTitle { font-size:17px; font-weight:600; margin-top:10px; }
QPushButton { background:#191e25; border:1px solid #2a3038; border-radius:10px; padding:10px 15px; }
QPushButton:hover { background:#20262e; }
#Primary, #Save { background:#f1f3f5; color:#0b0d10; border:none; font-weight:700; }
#NavButton { text-align:left; background:transparent; border:none; padding:12px; color:#b8c0ca; }
#NavButton:hover { background:#1a1f26; color:#fff; }
QLineEdit, QComboBox { background:#11161c; border:1px solid #2b323b; border-radius:10px; padding:11px; }
QListWidget { background:#0f1318; border:1px solid #242a32; border-radius:12px; padding:8px; }
QListWidget::item { padding:12px; border-radius:8px; }
QListWidget::item:selected { background:#242b34; }
QFrame { border-radius:12px; }
#MediaPreviewTitle { font-size:18px; font-weight:600; }
QScrollBar:vertical { background:#0b0d10; width:10px; }
QScrollBar::handle:vertical { background:#303741; border-radius:5px; }
'''

if __name__ == "__main__": main()
