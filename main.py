from __future__ import annotations

import json
import os
import urllib.request
from pathlib import Path

from PySide6.QtCore import QObject, QRunnable, QThreadPool, Qt, Signal, Slot
from PySide6.QtGui import QFont, QPixmap
from PySide6.QtWidgets import (
    QApplication, QComboBox, QDialog, QFileDialog, QFrame, QGridLayout,
    QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem, QMainWindow,
    QMessageBox, QProgressBar, QPushButton, QScrollArea, QStackedWidget,
    QVBoxLayout, QWidget, QInputDialog
)

from tunevault_engine import ApplyEngine, LibraryModel, MediaPayload, Operation, resolve, safe_name

APP_NAME = "TuneVault"
APP_DIR = Path(os.getenv("APPDATA", str(Path.home()))) / APP_NAME
CONFIG_FILE = APP_DIR / "config.json"
APP_DIR.mkdir(parents=True, exist_ok=True)
AUDIO_EXT = {".mp3", ".wav", ".flac", ".m4a", ".aac", ".ogg", ".opus"}
VIDEO_EXT = {".mp4", ".mkv", ".webm", ".mov", ".avi", ".m4v"}


def load_config():
    try:
        if CONFIG_FILE.exists():
            value = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
            return value if isinstance(value, dict) else {"roots": []}
    except Exception:
        pass
    return {"roots": []}


def save_config(value):
    tmp = CONFIG_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(CONFIG_FILE)


def icon_for(path: Path):
    if path.is_dir(): return "▣"
    if path.suffix.lower() in AUDIO_EXT: return "♪"
    if path.suffix.lower() in VIDEO_EXT: return "▶"
    return "•"


def pretty_duration(seconds):
    try: n = int(seconds or 0)
    except Exception: n = 0
    if n <= 0: return ""
    m, s = divmod(n, 60); h, m = divmod(m, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


class Signals(QObject):
    result = Signal(object); error = Signal(str); done = Signal()


class Worker(QRunnable):
    def __init__(self, func, *args):
        super().__init__(); self.func = func; self.args = args; self.signals = Signals()
    @Slot()
    def run(self):
        try: self.signals.result.emit(self.func(*self.args))
        except Exception as exc: self.signals.error.emit(str(exc))
        finally: self.signals.done.emit()


class MediaDialog(QDialog):
    def __init__(self, target: Path, parent=None):
        super().__init__(parent); self.target = resolve(target); self.info = {}; self.entries = []
        self.setWindowTitle("הוספת מדיה"); self.resize(820, 620); self.build()
    def build(self):
        box = QVBoxLayout(self)
        title = QLabel("הוספת מדיה"); title.setObjectName("DialogTitle"); box.addWidget(title)
        sub = QLabel("הדבק קישור למדיה או לחיפוש. ההורדה עצמה תתחיל רק לאחר שמירת השינויים."); sub.setObjectName("Muted"); sub.setWordWrap(True); box.addWidget(sub)
        row = QHBoxLayout(); self.url = QLineEdit(); self.url.setPlaceholderText("YouTube / URL / חיפוש…"); self.detect_btn = QPushButton("זיהוי"); self.detect_btn.setObjectName("Primary"); self.detect_btn.clicked.connect(self.detect); row.addWidget(self.url, 1); row.addWidget(self.detect_btn); box.addLayout(row)
        preview = QFrame(); preview.setObjectName("Glass"); pv = QHBoxLayout(preview); self.thumb = QLabel("PREVIEW"); self.thumb.setObjectName("Thumbnail"); self.thumb.setAlignment(Qt.AlignCenter); self.thumb.setFixedSize(220, 124); pv.addWidget(self.thumb); meta = QVBoxLayout(); self.media_title = QLabel("עדיין לא זוהתה מדיה"); self.media_title.setObjectName("HeroSmall"); self.media_title.setWordWrap(True); self.media_meta = QLabel(""); self.media_meta.setObjectName("Muted"); meta.addWidget(self.media_title); meta.addWidget(self.media_meta); meta.addStretch(); pv.addLayout(meta, 1); box.addWidget(preview)
        form = QFrame(); form.setObjectName("Glass"); fl = QVBoxLayout(form); lab = QLabel("שם הקובץ"); lab.setObjectName("Eyebrow"); fl.addWidget(lab); self.name = QLineEdit(); self.name.setEnabled(False); fl.addWidget(self.name); r = QHBoxLayout(); r.addWidget(QLabel("פורמט")); self.format = QComboBox(); self.format.addItems(["MP3", "MP4"]); self.format.setEnabled(False); r.addWidget(self.format, 1); fl.addLayout(r); box.addWidget(form)
        self.hint = QLabel(""); self.hint.setObjectName("Muted"); self.hint.setWordWrap(True); box.addWidget(self.hint)
        actions = QHBoxLayout(); actions.addStretch(); cancel = QPushButton("ביטול"); cancel.clicked.connect(self.reject); self.ok = QPushButton("הוסף לתור"); self.ok.setObjectName("Primary"); self.ok.setEnabled(False); self.ok.clicked.connect(self.accept); actions.addWidget(cancel); actions.addWidget(self.ok); box.addLayout(actions)
    @staticmethod
    def extract(query):
        import yt_dlp
        q = query.strip(); opts = {"quiet": True, "no_warnings": True, "skip_download": True, "noplaylist": False}
        if not (q.startswith("http://") or q.startswith("https://")): q = "ytsearch5:" + q; opts["noplaylist"] = True
        with yt_dlp.YoutubeDL(opts) as ydl: return ydl.extract_info(q, download=False), q
    def detect(self):
        query = self.url.text().strip()
        if not query: return
        self.detect_btn.setEnabled(False); self.detect_btn.setText("מזהה…")
        w = Worker(self.extract, query); w.signals.result.connect(self.detected); w.signals.error.connect(lambda e: QMessageBox.warning(self, "הזיהוי נכשל", e)); w.signals.done.connect(lambda: (self.detect_btn.setEnabled(True), self.detect_btn.setText("זיהוי"))); QThreadPool.globalInstance().start(w)
    def detected(self, result):
        info, original = result; self.info = info or {}
        if original.startswith("ytsearch") and self.info.get("entries"):
            self.info = next((x for x in self.info["entries"] if x), self.info)
        self.entries = [x for x in (self.info.get("entries") or []) if x]
        title = self.info.get("title") or "ללא שם"; creator = self.info.get("uploader") or self.info.get("channel") or ""; duration = pretty_duration(self.info.get("duration")); self.media_title.setText(title); self.media_meta.setText(" · ".join(x for x in (creator, duration) if x) or "מדיה מוכנה"); self.name.setText(title); self.name.setEnabled(True); self.format.setEnabled(True); self.ok.setEnabled(True); self.hint.setText("הפעולה תופיע מיד בתור, אבל הקובץ לא ייכתב לדיסק עד שתלחץ על ‘שמירת שינויים’.")
        thumb = self.info.get("thumbnail")
        if thumb:
            w = Worker(self._thumb_data, thumb); w.signals.result.connect(self._show_thumb); QThreadPool.globalInstance().start(w)
    @staticmethod
    def _thumb_data(url):
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=10) as response: return response.read()
    def _show_thumb(self, data):
        pix = QPixmap()
        if pix.loadFromData(data): self.thumb.setText(""); self.thumb.setPixmap(pix.scaled(self.thumb.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation))
    def payloads(self):
        if self.entries: return [MediaPayload(url=e.get("webpage_url") or e.get("original_url") or e.get("url", ""), title=e.get("title") or "ללא שם", target=str(self.target), format="MP3") for e in self.entries]
        return [MediaPayload(url=self.url.text().strip(), title=self.name.text().strip() or "ללא שם", target=str(self.target), format=self.format.currentText())]


class OperationCard(QFrame):
    def __init__(self, op):
        super().__init__(); self.setObjectName("QueueCard"); l = QVBoxLayout(self); l.setContentsMargins(18,14,18,14); row = QHBoxLayout(); self.dot = QLabel("●"); self.text = QLabel(op.label); self.text.setObjectName("CardTitle"); self.text.setWordWrap(True); self.percent = QLabel("0%"); self.percent.setObjectName("Muted"); row.addWidget(self.dot); row.addWidget(self.text,1); row.addWidget(self.percent); l.addLayout(row); self.bar = QProgressBar(); l.addWidget(self.bar); self.update_op(op)
    def update_op(self, op):
        self.bar.setValue(op.progress); state = {"pending": ("ממתין","●"), "running": ("מבצע","◉"), "success": ("הושלם","✓"), "failed": ("נכשל","!"), "cancelled": ("בוטל","×")}.get(op.status,(op.status,"●")); self.dot.setText(state[1]); self.text.setText(op.label if not op.error else f"{op.label}  ·  {op.error}"); self.percent.setText(state[0] if op.status == "success" else f"{op.progress}%"); self.bar.setVisible(op.status in {"pending","running","failed"})


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__(); self.config=load_config(); self.model=LibraryModel(self.config.get("roots",[])); self.pool=QThreadPool.globalInstance(); self.engine=None; self.rows={}; self.current_folder=None; self.saving=False; self.setWindowTitle("TuneVault"); self.resize(1460,920); self.setMinimumSize(1180,760); self.build(); self.refresh(); self.navigate(0)
    def build(self):
        root=QWidget(); self.setCentralWidget(root); main=QHBoxLayout(root); main.setContentsMargins(0,0,0,0); main.setSpacing(0)
        side=QFrame(); side.setObjectName("Sidebar"); side.setFixedWidth(250); sl=QVBoxLayout(side); sl.setContentsMargins(22,28,22,24); logo=QLabel("TuneVault"); logo.setObjectName("Brand"); sl.addWidget(logo); tag=QLabel("Your media.\nYour structure.\nYour computer."); tag.setObjectName("Muted"); sl.addWidget(tag); sl.addSpacing(28); self.nav=[]
        for label,idx in [("⌂   סקירה",0),("▣   הספרייה",1),("↘   פעולות",2),("⌕   חיפוש",3)]: b=QPushButton(label); b.setObjectName("Nav"); b.clicked.connect(lambda _,i=idx:self.navigate(i)); self.nav.append(b); sl.addWidget(b)
        sl.addStretch(); self.save_button=QPushButton("שמירת שינויים"); self.save_button.setObjectName("SaveButton"); self.save_button.clicked.connect(self.save_changes); sl.addWidget(self.save_button); self.status=QLabel("הכול מסונכרן"); self.status.setObjectName("Status"); sl.addWidget(self.status); main.addWidget(side)
        self.pages=QStackedWidget(); self.pages.addWidget(self.dashboard_page()); self.pages.addWidget(self.library_page()); self.pages.addWidget(self.queue_page()); self.pages.addWidget(self.search_page()); main.addWidget(self.pages,1)
    def navigate(self,idx): self.pages.setCurrentIndex(idx); [self._set_active(i,b,idx) for i,b in enumerate(self.nav)]; self.refresh()
    @staticmethod
    def _set_active(i,b,idx): b.setProperty("active",i==idx); b.style().unpolish(b); b.style().polish(b)
    def page_shell(self,title,subtitle):
        p=QWidget(); l=QVBoxLayout(p); l.setContentsMargins(36,30,36,30); v=QVBoxLayout(); a=QLabel(title); a.setObjectName("PageTitle"); b=QLabel(subtitle); b.setObjectName("Muted"); b.setWordWrap(True); v.addWidget(a); v.addWidget(b); l.addLayout(v); return p,l
    def dashboard_page(self):
        p,l=self.page_shell("סקירה","כל מה שצריך כדי לנהל את המדיה שלך מהמקום היחיד שמרכז הכול."); hero=QFrame(); hero.setObjectName("Hero"); h=QHBoxLayout(hero); copy=QVBoxLayout(); t=QLabel("המדיה שלך, בלי הבלגן"); t.setObjectName("HeroTitle"); copy.addWidget(t); s=QLabel("בנה שינויים, בדוק אותם, ורק כשאתה מוכן — שמור אותם למחשב."); s.setObjectName("HeroText"); s.setWordWrap(True); copy.addWidget(s); ar=QHBoxLayout(); a=QPushButton("＋ הוסף מדיה"); a.setObjectName("Primary"); a.clicked.connect(self.quick_add); f=QPushButton("פתח ספרייה"); f.clicked.connect(lambda:self.navigate(1)); ar.addWidget(a); ar.addWidget(f); ar.addStretch(); copy.addLayout(ar); h.addLayout(copy,2); stats=QVBoxLayout(); self.d_pending=QLabel("0\nפעולות ממתינות"); self.d_items=QLabel("0\nפריטים"); self.d_roots=QLabel("0\nמיקומים"); [x.setObjectName("Stat") or stats.addWidget(x) for x in (self.d_pending,self.d_items,self.d_roots)]; h.addLayout(stats,1); l.addWidget(hero); sec=QLabel("הפעולות המרכזיות"); sec.setObjectName("SectionTitle"); l.addWidget(sec); grid=QGridLayout(); cards=[("הוספת מדיה","קישור או חיפוש → בחירת פורמט → שמירה",self.quick_add),("ניהול ספרייה","תיקיות, שמות, העברה ומחיקה בלי לגעת מיד בדיסק",lambda:self.navigate(1)),("תור הפעולות","ראה בדיוק מה ממתין ומה כבר בוצע",lambda:self.navigate(2)),("חיפוש","מצא שירים וקבצים בכל המיקומים המורשים",lambda:self.navigate(3))]
        for i,(a,b,fn) in enumerate(cards): f=QFrame(); f.setObjectName("ActionCard"); q=QVBoxLayout(f); x=QLabel(a); x.setObjectName("CardTitle"); y=QLabel(b); y.setObjectName("Muted"); y.setWordWrap(True); go=QPushButton("פתח →"); go.clicked.connect(fn); q.addWidget(x); q.addWidget(y); q.addStretch(); q.addWidget(go); grid.addWidget(f,i//2,i%2)
        l.addLayout(grid); l.addStretch(); return p
    def library_page(self):
        p,l=self.page_shell("הספרייה","העולם הווירטואלי שלך. שום שינוי לא נכתב לדיסק עד לשמירת שינויים."); top=QHBoxLayout(); self.search_box=QLineEdit(); self.search_box.setPlaceholderText("חיפוש בתיקייה הנוכחית…"); self.search_box.textChanged.connect(self.render_folder); top.addWidget(self.search_box,1); add=QPushButton("＋ הוסף מיקום"); add.setObjectName("Primary"); add.clicked.connect(self.add_root); top.addWidget(add); l.addLayout(top); split=QSplitter(Qt.Horizontal)
        left=QFrame(); left.setObjectName("Glass"); ll=QVBoxLayout(left); e=QLabel("מיקומים מורשים"); e.setObjectName("Eyebrow"); ll.addWidget(e); self.roots=QListWidget(); self.roots.currentItemChanged.connect(self.root_changed); ll.addWidget(self.roots,1); rm=QPushButton("הסר מיקום"); rm.clicked.connect(self.remove_root); ll.addWidget(rm); split.addWidget(left)
        right=QFrame(); right.setObjectName("Glass"); rl=QVBoxLayout(right); bar=QHBoxLayout(); self.folder_title=QLabel("בחר מיקום"); self.folder_title.setObjectName("SectionTitle"); bar.addWidget(self.folder_title,1); up=QPushButton("↑"); up.clicked.connect(self.go_up); bar.addWidget(up); rf=QPushButton("↻"); rf.clicked.connect(self.render_folder); bar.addWidget(rf); rl.addLayout(bar); self.items=QListWidget(); self.items.itemDoubleClicked.connect(self.open_item); rl.addWidget(self.items,1); actions=QHBoxLayout(); [self._action_button(actions,label,fn) for label,fn in [("＋ תיקייה",self.stage_folder),("＋ מדיה",self.stage_media),("שנה שם",self.rename_item),("העבר",self.move_item),("מחק",self.delete_item)]]; actions.addStretch(); rl.addLayout(actions); split.addWidget(right); split.setSizes([300,900]); l.addWidget(split,1); return p
    @staticmethod
    def _action_button(layout,label,fn): b=QPushButton(label); b.clicked.connect(fn); layout.addWidget(b)
    def queue_page(self):
        p,l=self.page_shell("פעולות","כל הורדה וכל שינוי מופיעים כאן. אתה שומר כשאתה מחליט."); h=QHBoxLayout(); self.queue_count=QLabel("0 פעולות"); self.queue_count.setObjectName("Eyebrow"); h.addWidget(self.queue_count); h.addStretch(); u=QPushButton("↶ בטל אחרונה"); u.clicked.connect(self.undo); h.addWidget(u); s=QPushButton("שמירת שינויים"); s.setObjectName("Primary"); s.clicked.connect(self.save_changes); h.addWidget(s); l.addLayout(h); self.queue_list=QVBoxLayout(); container=QWidget(); container.setLayout(self.queue_list); scroll=QScrollArea(); scroll.setWidgetResizable(True); scroll.setWidget(container); l.addWidget(scroll,1); return p
    def search_page(self):
        p,l=self.page_shell("חיפוש","חיפוש מקומי בכל המיקומים שאישרת ל-TuneVault."); row=QHBoxLayout(); self.global_search=QLineEdit(); self.global_search.setPlaceholderText("למשל: album, artist, song…"); self.global_search.returnPressed.connect(self.run_search); row.addWidget(self.global_search,1); go=QPushButton("חיפוש"); go.setObjectName("Primary"); go.clicked.connect(self.run_search); row.addWidget(go); l.addLayout(row); self.search_results=QListWidget(); l.addWidget(self.search_results,1); return p
    def refresh(self):
        if hasattr(self,"roots"): self.refresh_roots()
        if hasattr(self,"queue_list"): self.refresh_queue()
        if hasattr(self,"d_pending"):
            self.d_pending.setText(f"{len(self.model.pending)}\nפעולות ממתינות"); self.d_roots.setText(f"{sum(Path(r).exists() for r in self.model.roots)}\nמיקומים")
            total=0
            for root in self.model.roots:
                try: total += sum(1 for _ in Path(root).rglob("*"))
                except Exception: pass
            self.d_items.setText(f"{total}\nפריטים")
        self.save_button.setText(f"שמירת שינויים  ·  {len(self.model.pending)}" if self.model.pending else "שמירת שינויים"); self.save_button.setEnabled(bool(self.model.pending) and not self.saving); self.status.setText("יש שינויים ממתינים" if self.model.pending else "הכול מסונכרן")
        if self.current_folder and hasattr(self,"items"): self.render_folder()
    def refresh_roots(self):
        self.roots.blockSignals(True); self.roots.clear()
        for root in self.model.roots:
            item=QListWidgetItem(f"▣  {Path(root).name or root}"); item.setData(Qt.UserRole,root); item.setToolTip(root); self.roots.addItem(item)
        self.roots.blockSignals(False)
        if self.roots.count() and self.current_folder is None: self.roots.setCurrentRow(0)
    def root_changed(self,item,_old=None):
        if item: self.current_folder=resolve(item.data(Qt.UserRole)); self.render_folder()
    def render_folder(self):
        if not self.current_folder: return
        self.folder_title.setText(self.current_folder.name or str(self.current_folder)); query=self.search_box.text().strip().casefold(); self.items.clear()
        for path,origin in self.model.virtual_entries(self.current_folder):
            if query and query not in path.name.casefold(): continue
            item=QListWidgetItem(f"{icon_for(path)}   {path.name}" + ("   · ממתין" if origin=="pending" else "")); item.setData(Qt.UserRole,str(path)); item.setToolTip(str(path)); self.items.addItem(item)
    def open_item(self,item):
        path=Path(item.data(Qt.UserRole))
        if path.is_dir(): self.current_folder=resolve(path); self.render_folder()
    def go_up(self):
        if self.current_folder and self.model.allowed(self.current_folder.parent) and self.current_folder.parent != self.current_folder: self.current_folder=self.current_folder.parent; self.render_folder()
    def add_root(self):
        path=QFileDialog.getExistingDirectory(self,"בחר מיקום לספרייה")
        if not path:return
        path=str(resolve(path))
        if path in self.model.roots:return
        self.model.roots.append(path); self.config["roots"]=self.model.roots; save_config(self.config); self.current_folder=resolve(path); self.refresh()
    def remove_root(self):
        item=self.roots.currentItem()
        if not item:return
        path=item.data(Qt.UserRole)
        if QMessageBox.question(self,"הסרת מיקום",f"להסיר את הגישה ל־{Path(path).name}?")==QMessageBox.Yes:
            self.model.roots=[x for x in self.model.roots if x!=path]; self.config["roots"]=self.model.roots; save_config(self.config); self.current_folder=None; self.refresh()
    def stage_folder(self):
        if not self.current_folder:return
        name,ok=QInputDialog.getText(self,"תיקייה חדשה","שם התיקייה:")
        if not ok or not name.strip():return
        path=self.current_folder/safe_name(name.strip())
        try:self.model.stage(Operation(kind="mkdir",label=f"יצירת תיקייה · {path.name}",path=str(path)))
        except Exception as e:QMessageBox.warning(self,"לא ניתן",str(e));return
        self.refresh()
    def quick_add(self):
        self.navigate(1)
        if not self.current_folder and self.model.roots:self.current_folder=resolve(self.model.roots[0])
        self.stage_media()
    def stage_media(self):
        if not self.current_folder: QMessageBox.information(self,"בחר מיקום","בחר מיקום מורשה לפני הוספת מדיה."); return
        d=MediaDialog(self.current_folder,self)
        if d.exec()!=QDialog.Accepted:return
        try:
            for p in d.payloads(): self.model.stage(Operation(kind="download",label=f"הורדת {p.title}.{p.normalized_format().lower()}",path=p.target,payload=p))
            self.refresh(); self.navigate(2)
        except Exception as e:QMessageBox.warning(self,"לא ניתן להוסיף",str(e))
    def selected_path(self):
        item=self.items.currentItem(); return Path(item.data(Qt.UserRole)) if item else None
    def rename_item(self):
        path=self.selected_path()
        if not path:return
        name,ok=QInputDialog.getText(self,"שינוי שם","שם חדש:",text=path.stem)
        if not ok or not name.strip():return
        new=path.parent/safe_name(name.strip())
        if path.suffix and not new.suffix:new=new.with_suffix(path.suffix)
        try:self.model.stage(Operation(kind="rename",label=f"שינוי שם · {path.name} → {new.name}",old=str(path),new=str(new)))
        except Exception as e:QMessageBox.warning(self,"לא ניתן",str(e));return
        self.refresh()
    def move_item(self):
        path=self.selected_path()
        if not path:return
        dest=QFileDialog.getExistingDirectory(self,"בחר תיקיית יעד",str(self.current_folder))
        if not dest:return
        try:self.model.stage(Operation(kind="move",label=f"העברה · {path.name}",old=str(path),new=str(Path(dest)/path.name)))
        except Exception as e:QMessageBox.warning(self,"לא ניתן",str(e));return
        self.refresh()
    def delete_item(self):
        path=self.selected_path()
        if not path:return
        if QMessageBox.question(self,"מחיקה",f"לסמן למחיקה את {path.name}?")!=QMessageBox.Yes:return
        try:self.model.stage(Operation(kind="delete",label=f"מחיקה · {path.name}",path=str(path)))
        except Exception as e:QMessageBox.warning(self,"לא ניתן",str(e));return
        self.refresh()
    def refresh_queue(self):
        while self.queue_list.count():
            item=self.queue_list.takeAt(0); w=item.widget()
            if w:w.deleteLater()
        self.queue_count.setText(f"{len(self.model.pending)} פעולות"); self.rows={}
        if not self.model.pending:
            empty=QLabel("אין פעולות ממתינות. הספרייה נקייה."); empty.setObjectName("Empty"); self.queue_list.addWidget(empty)
        for op in self.model.pending:
            card=OperationCard(op); self.rows[op.id]=card; self.queue_list.addWidget(card)
        self.queue_list.addStretch()
    def undo(self):
        if self.model.undo_last():self.refresh()
    def save_changes(self):
        if self.saving or not self.model.pending:return
        self.saving=True; self.refresh(); self.engine=ApplyEngine(self.model)
        w=Worker(self.engine.apply); w.signals.result.connect(self.apply_finished); w.signals.error.connect(self.apply_error); self.pool.start(w)
    def _op_progress(self,op):
        card=self.rows.get(op.id)
        if card:card.update_op(op)
    def apply_finished(self,results):
        with self.model._lock:
            done={op.id for op in results if op.status=="success"}; self.model.pending=[op for op in self.model.pending if op.id not in done]; self.model.history.extend(results)
        self.saving=False; self.refresh(); self.navigate(2); failed=[x for x in results if x.status=="failed"]
        if failed:QMessageBox.warning(self,"שמירת השינויים הסתיימה",f"{len(failed)} פעולות נכשלו. פתח את ‘פעולות’ לפרטים.")
        else:QMessageBox.information(self,"הכול נשמר","כל השינויים שבחרת נשמרו במחשב.")
    def apply_error(self,error):self.saving=False;self.refresh();QMessageBox.critical(self,"שגיאה",error)
    def run_search(self):
        q=self.global_search.text().strip().casefold(); self.search_results.clear()
        if not q:return
        for root in self.model.roots:
            try:
                for path in Path(root).rglob("*"):
                    if q in path.name.casefold(): self.search_results.addItem(f"{icon_for(path)}   {path.name}  ·  {path.parent}")
                    if self.search_results.count()>=500:break
            except Exception:pass


STYLE=r"""
* { font-family: 'Segoe UI'; font-size: 14px; }
QMainWindow,QWidget { background:#0b0f16; color:#eef2f7; }
#Sidebar { background:#090d13; border-right:1px solid #202733; }
#Brand { font-size:26px; font-weight:800; color:#fff; }
#Muted { color:#7f8a9a; }
#Nav { background:transparent; border:0; border-radius:12px; color:#8792a3; text-align:left; padding:14px 16px; margin:3px 0; }
#Nav:hover { background:#141a24; color:#fff; }
#Nav[active="true"] { background:#1c2330; color:#fff; font-weight:700; }
#Status { background:#111722; border:1px solid #222c3b; border-radius:12px; padding:10px; color:#9ba7b8; }
#SaveButton,#Primary { background:#f3f5f8; color:#0b0f16; border:0; border-radius:12px; padding:12px 18px; font-weight:700; }
#SaveButton:hover,#Primary:hover { background:#fff; }
#SaveButton:disabled { background:#202733; color:#657083; }
#PageTitle { font-size:31px; font-weight:800; }
#SectionTitle { font-size:19px; font-weight:700; }
#Eyebrow { color:#7d8796; font-size:12px; font-weight:700; letter-spacing:1px; }
#Hero { background:qlineargradient(x1:0,y1:0,x2:1,y2:1,stop:0 #171e2b,stop:1 #10151f); border:1px solid #273141; border-radius:22px; padding:28px; }
#HeroTitle { font-size:36px; font-weight:800; }
#HeroText { font-size:16px; color:#a6b0bf; }
#HeroSmall { font-size:18px; font-weight:700; }
#Stat { background:#0d131c; border:1px solid #252f3d; border-radius:14px; padding:14px; color:#cbd3df; font-weight:700; }
#ActionCard,#Glass,#QueueCard { background:#0e141d; border:1px solid #222c39; border-radius:18px; }
#ActionCard { padding:18px; }
#CardTitle { font-size:16px; font-weight:700; }
#DialogTitle { font-size:28px; font-weight:800; }
#Thumbnail { background:#090d13; border:1px solid #232d3c; border-radius:12px; color:#586477; }
QLineEdit,QComboBox { background:#0d131c; color:#fff; border:1px solid #273243; border-radius:11px; padding:11px 13px; }
QLineEdit:focus,QComboBox:focus { border:1px solid #617087; }
QPushButton { background:#141b25; color:#d9e0e9; border:1px solid #263140; border-radius:11px; padding:10px 14px; }
QPushButton:hover { background:#1b2430; }
QListWidget { background:#0b1119; border:0; border-radius:14px; padding:7px; }
QListWidget::item { padding:12px; border-radius:9px; color:#d5dce6; }
QListWidget::item:selected { background:#1b2431; color:#fff; }
QProgressBar { border:0; background:#1a2230; height:7px; border-radius:4px; }
QProgressBar::chunk { background:#e8ecf2; border-radius:4px; }
QScrollArea { border:0; }
#Empty { color:#677385; font-size:17px; padding:60px; }
QSplitter::handle { background:#202a38; }
"""


def main():
    app=QApplication([]); app.setStyle("Fusion"); app.setLayoutDirection(Qt.RightToLeft); app.setStyleSheet(STYLE); app.setFont(QFont("Segoe UI",10)); w=MainWindow(); w.show(); app.exec()

if __name__=="__main__": main()
