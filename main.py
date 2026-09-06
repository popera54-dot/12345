from __future__ import annotations

import json
import os
import sys
import urllib.request
from pathlib import Path

from PySide6.QtCore import QObject, QRunnable, QThreadPool, Qt, QTimer, Signal, Slot
from PySide6.QtGui import QPixmap, QFont
from PySide6.QtWidgets import (
    QApplication, QComboBox, QDialog, QFileDialog, QFrame, QHBoxLayout, QLabel,
    QLineEdit, QListWidget, QListWidgetItem, QMainWindow, QMessageBox,
    QPushButton, QProgressBar, QScrollArea, QSplitter, QStackedWidget,
    QVBoxLayout, QWidget, QInputDialog
)

from tunevault_engine import ApplyEngine, LibraryModel, Operation, resolve, safe_name

APP = "TuneVault"
APP_DIR = Path(os.getenv("APPDATA", str(Path.home()))) / APP
APP_DIR.mkdir(parents=True, exist_ok=True)
CONFIG = APP_DIR / "config.json"
AUDIO = {".mp3", ".wav", ".flac", ".m4a", ".aac", ".ogg", ".opus"}
VIDEO = {".mp4", ".mkv", ".webm", ".mov", ".avi", ".m4v"}


def load_cfg():
    try:
        data = json.loads(CONFIG.read_text(encoding="utf-8")) if CONFIG.exists() else {}
        return data if isinstance(data, dict) else {"roots": []}
    except Exception:
        return {"roots": []}


def save_cfg(data):
    tmp = CONFIG.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(CONFIG)


def icon_for(path: Path):
    if path.is_dir(): return "◼"
    if path.suffix.lower() in AUDIO: return "♫"
    if path.suffix.lower() in VIDEO: return "▶"
    return "•"


def display_name(path: Path):
    return path.name or str(path)


class Signals(QObject):
    result = Signal(object)
    error = Signal(str)
    done = Signal()


class Worker(QRunnable):
    def __init__(self, fn, *args):
        super().__init__(); self.fn = fn; self.args = args; self.signals = Signals()

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
        self.target = target; self.info = {}; self.entries = []
        self.setLayoutDirection(Qt.RightToLeft); self.setWindowTitle("הוספת מדיה"); self.resize(840, 690)
        root = QVBoxLayout(self); root.setSpacing(16); root.setContentsMargins(28, 26, 28, 24)
        title = QLabel("הוספת מדיה"); title.setObjectName("DialogTitle"); root.addWidget(title)
        sub = QLabel("הדבק קישור, בדוק את המדיה, שנה את השם ואת הפורמט — ורק אחר כך הוסף לתור."); sub.setObjectName("Muted"); root.addWidget(sub)
        line = QHBoxLayout(); self.url = QLineEdit(); self.url.setPlaceholderText("הדבק כאן קישור למדיה או לפלייליסט…")
        self.detect_btn = QPushButton("בדוק קישור"); self.detect_btn.setObjectName("Primary"); self.detect_btn.clicked.connect(self.detect)
        line.addWidget(self.url, 1); line.addWidget(self.detect_btn); root.addLayout(line)
        card = QFrame(); card.setObjectName("Card"); cv = QHBoxLayout(card); cv.setContentsMargins(14, 14, 14, 14)
        self.thumb = QLabel("תצוגה\nמקדימה"); self.thumb.setAlignment(Qt.AlignCenter); self.thumb.setFixedSize(230, 130); self.thumb.setObjectName("Thumb"); cv.addWidget(self.thumb)
        meta = QVBoxLayout(); self.media_title = QLabel("עדיין לא זוהתה מדיה"); self.media_title.setObjectName("MediaTitle"); self.media_meta = QLabel("הדבק קישור ולחץ על «בדוק קישור»"); self.media_meta.setObjectName("Muted"); self.media_type = QLabel(""); self.media_type.setObjectName("Tiny"); meta.addWidget(self.media_title); meta.addWidget(self.media_meta); meta.addWidget(self.media_type); meta.addStretch(); cv.addLayout(meta, 1); root.addWidget(card)
        self.single_box = QFrame(); sb = QVBoxLayout(self.single_box); sb.setContentsMargins(0,0,0,0)
        sb.addWidget(QLabel("שם השיר / הקובץ")); self.name = QLineEdit(); self.name.setPlaceholderText("השם שיופיע בפועל בתיקייה"); self.name.setEnabled(False); sb.addWidget(self.name)
        row = QHBoxLayout(); row.addWidget(QLabel("פורמט")); self.format = QComboBox(); self.format.addItems(["MP3", "MP4"]); self.format.setEnabled(False); row.addWidget(self.format); sb.addLayout(row); root.addWidget(self.single_box)
        self.playlist = QListWidget(); self.playlist.hide(); root.addWidget(self.playlist, 1)
        actions = QHBoxLayout(); actions.addStretch(); cancel = QPushButton("ביטול"); ok = QPushButton("הוסף לתור"); ok.setObjectName("Primary"); ok.setEnabled(False); cancel.clicked.connect(self.reject); ok.clicked.connect(self.accept); actions.addWidget(cancel); actions.addWidget(ok); root.addLayout(actions); self.ok = ok

    def detect(self):
        url = self.url.text().strip()
        if not url: return
        self.detect_btn.setEnabled(False); self.detect_btn.setText("בודק…")
        w = Worker(self.extract, url); w.signals.result.connect(self.detected); w.signals.error.connect(lambda e: QMessageBox.warning(self, "לא הצלחנו לזהות", "לא ניתן לקרוא את הקישור.\n\n" + e)); w.signals.done.connect(lambda: (self.detect_btn.setEnabled(True), self.detect_btn.setText("בדוק קישור"))); QThreadPool.globalInstance().start(w)

    @staticmethod
    def extract(url):
        import yt_dlp
        with yt_dlp.YoutubeDL({"quiet": True, "no_warnings": True, "skip_download": True, "noplaylist": False}) as ydl:
            return ydl.extract_info(url, download=False)

    def detected(self, info):
        self.info = info or {}; self.entries = [e for e in (self.info.get("entries") or []) if e]
        title = self.info.get("title") or "ללא שם"; creator = self.info.get("uploader") or self.info.get("channel") or ""
        self.media_title.setText(title); self.media_meta.setText(" · ".join(v for v in (creator, self.info.get("duration_string"), self.info.get("webpage_url") and "מדיה" or "") if v)); self.name.setText(title); self.name.setEnabled(True); self.format.setEnabled(True); self.ok.setEnabled(True)
        if len(self.entries) > 1:
            self.playlist.show(); self.single_box.hide(); self.media_type.setText(f"פלייליסט · {len(self.entries)} פריטים")
            self.playlist.clear()
            for e in self.entries: self.playlist.addItem(e.get("title") or "ללא שם")
        else:
            self.playlist.hide(); self.single_box.show(); self.media_type.setText("פריט בודד")
        thumb = self.info.get("thumbnail")
        if thumb:
            w = Worker(self.thumb_bytes, thumb); w.signals.result.connect(self.show_thumb); QThreadPool.globalInstance().start(w)

    @staticmethod
    def thumb_bytes(url):
        with urllib.request.urlopen(url, timeout=10) as r: return r.read()

    def show_thumb(self, data):
        pix = QPixmap();
        if pix.loadFromData(data): self.thumb.setText(""); self.thumb.setPixmap(pix.scaled(self.thumb.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation))

    def payloads(self):
        if len(self.entries) > 1:
            return [{"url": e.get("webpage_url") or e.get("original_url"), "title": e.get("title") or "ללא שם", "format": "MP3", "target": str(self.target)} for e in self.entries if e.get("webpage_url") or e.get("original_url")]
        return [{"url": self.url.text().strip(), "title": safe_name(self.name.text().strip() or "ללא שם"), "format": self.format.currentText(), "target": str(self.target)}]


class PlaylistDialog(QDialog):
    def __init__(self, payloads, parent=None):
        super().__init__(parent); self.payloads = payloads; self.setLayoutDirection(Qt.RightToLeft); self.setWindowTitle("הגדרת פלייליסט"); self.resize(820, 640)
        l = QVBoxLayout(self); l.setContentsMargins(26, 24, 26, 24); t = QLabel(f"פלייליסט · {len(payloads)} שירים"); t.setObjectName("DialogTitle"); l.addWidget(t); l.addWidget(QLabel("הגדר לכל שיר פורמט ושם. הכל יתווסף לתור ורק שמירת השינויים תתחיל את ההורדה."))
        q = QHBoxLayout(); all3 = QPushButton("הכול MP3"); all4 = QPushButton("הכול MP4"); all3.clicked.connect(lambda: self.set_all("MP3")); all4.clicked.connect(lambda: self.set_all("MP4")); q.addWidget(all3); q.addWidget(all4); q.addStretch(); l.addLayout(q)
        self.rows = QListWidget(); l.addWidget(self.rows, 1)
        for p in payloads:
            item = QListWidgetItem(); w = QWidget(); row = QHBoxLayout(w); row.setContentsMargins(10, 6, 10, 6)
            name = QLineEdit(p["title"]); name.textChanged.connect(lambda v, p=p: p.__setitem__("title", safe_name(v))); combo = QComboBox(); combo.addItems(["MP3", "MP4"]); combo.currentTextChanged.connect(lambda v, p=p: p.__setitem__("format", v)); row.addWidget(name, 1); row.addWidget(combo); self.rows.addItem(item); self.rows.setItemWidget(item, w)
        a = QHBoxLayout(); a.addStretch(); c = QPushButton("ביטול"); ok = QPushButton("אשר והוסף לתור"); ok.setObjectName("Primary"); c.clicked.connect(self.reject); ok.clicked.connect(self.accept); a.addWidget(c); a.addWidget(ok); l.addLayout(a)

    def set_all(self, fmt):
        for i, p in enumerate(self.payloads):
            p["format"] = fmt; self.rows.itemWidget(self.rows.item(i)).findChild(QComboBox).setCurrentText(fmt)


class Main(QMainWindow):
    def __init__(self):
        super().__init__(); self.setLayoutDirection(Qt.RightToLeft); self.config = load_cfg(); self.model = LibraryModel(self.config.get("roots", [])); self.current = None; self.pool = QThreadPool.globalInstance(); self.engine = None; self.setWindowTitle("TuneVault"); self.resize(1500, 930); self.setMinimumSize(1180, 760); self.build(); self.refresh(); self.timer = QTimer(self); self.timer.timeout.connect(self.tick); self.timer.start(350)

    def build(self):
        central = QWidget(); outer = QHBoxLayout(central); outer.setContentsMargins(0,0,0,0); outer.setSpacing(0); self.setCentralWidget(central)
        side = QFrame(); side.setObjectName("Sidebar"); side.setFixedWidth(255); sl = QVBoxLayout(side); sl.setContentsMargins(22, 24, 22, 22)
        brand = QLabel("TuneVault"); brand.setObjectName("Brand"); sl.addWidget(brand); tag = QLabel("המדיה שלך. הסדר שלך. בלי ענן."); tag.setObjectName("Muted"); tag.setWordWrap(True); sl.addWidget(tag); sl.addSpacing(26)
        self.nav_buttons = []
        for text, idx, glyph in [("ספרייה",0,"⌂"),("הורדות",1,"↓"),("חיפוש",2,"⌕")]:
            b = QPushButton(f"{glyph}   {text}"); b.setObjectName("NavButton"); b.clicked.connect(lambda _,i=idx: self.pages.setCurrentIndex(i)); sl.addWidget(b); self.nav_buttons.append(b)
        sl.addStretch(); self.status = QLabel("מוכן"); self.status.setObjectName("StatusPill"); sl.addWidget(self.status); outer.addWidget(side)
        self.pages = QStackedWidget(); self.pages.addWidget(self.library_page()); self.pages.addWidget(self.download_page()); self.pages.addWidget(self.search_page()); outer.addWidget(self.pages, 1)

    def page_title(self, title, subtitle):
        box = QVBoxLayout(); box.setSpacing(3); t = QLabel(title); t.setObjectName("PageTitle"); s = QLabel(subtitle); s.setObjectName("Muted"); box.addWidget(t); box.addWidget(s); return box

    def library_page(self):
        p = QWidget(); l = QVBoxLayout(p); l.setContentsMargins(34, 28, 34, 26); head = QHBoxLayout(); head.addLayout(self.page_title("ספרייה", "מרחב העבודה שלך למדיה ולתיקיות מקומיות."), 1); self.filter = QLineEdit(); self.filter.setPlaceholderText("חיפוש בתוך התיקייה הנוכחית…"); self.filter.textChanged.connect(self.render); head.addWidget(self.filter); add = QPushButton("+ הוסף תיקייה"); add.setObjectName("Primary"); add.clicked.connect(self.add_root); head.addWidget(add); l.addLayout(head)
        strip = QFrame(); strip.setObjectName("InfoStrip"); iv = QHBoxLayout(strip); self.breadcrumb = QLabel("בחר תיקייה"); self.breadcrumb.setObjectName("SectionTitle"); iv.addWidget(self.breadcrumb,1); self.selection_info = QLabel(""); self.selection_info.setObjectName("Muted"); iv.addWidget(self.selection_info); l.addWidget(strip)
        split = QSplitter(Qt.Horizontal); left = QWidget(); ll = QVBoxLayout(left); ll.setContentsMargins(0,0,0,0); lab = QLabel("תיקיות מורשות"); lab.setObjectName("Tiny"); ll.addWidget(lab); self.roots = QListWidget(); ll.addWidget(self.roots,1); rr = QPushButton("הסר גישה"); rr.clicked.connect(self.remove_root); ll.addWidget(rr); split.addWidget(left)
        right = QWidget(); rl = QVBoxLayout(right); rl.setContentsMargins(16,0,0,0); bar = QHBoxLayout(); up = QPushButton("↑"); up.setFixedWidth(42); up.clicked.connect(self.go_up); refresh = QPushButton("↻"); refresh.setFixedWidth(42); refresh.clicked.connect(self.render); bar.addWidget(self.path_label if hasattr(self,'path_label') else QLabel("")); bar.addStretch(); bar.addWidget(up); bar.addWidget(refresh); rl.addLayout(bar); self.items = QListWidget(); self.items.itemDoubleClicked.connect(self.open_item); self.items.itemSelectionChanged.connect(self.selected_changed); rl.addWidget(self.items,1)
        act = QHBoxLayout();
        for text, fn in [("+ תיקייה",self.mkdir),("+ שיר",self.add_media),("שנה שם",self.rename),("העבר",self.move_item),("מחק",self.delete_item)]: b=QPushButton(text); b.clicked.connect(fn); act.addWidget(b)
        act.addStretch(); self.saveb = QPushButton("שמירת שינויים"); self.saveb.setObjectName("Primary"); self.saveb.clicked.connect(self.save_changes); act.addWidget(self.saveb); rl.addLayout(act); split.addWidget(right); split.setSizes([320,1040]); l.addWidget(split,1); self.roots.currentItemChanged.connect(self.root_selected); return p

    def download_page(self):
        p=QWidget(); l=QVBoxLayout(p); l.setContentsMargins(34,28,34,26); l.addLayout(self.page_title("הורדות", "כל פעולה שקורית במחשב עוברת כאן — עם מצב ברור ותוצאה ברורה.")); self.totalbar=QProgressBar(); self.totalbar.setValue(0); l.addWidget(self.totalbar); cards=QHBoxLayout(); self.card_pending=QLabel("0\nממתינים"); self.card_ok=QLabel("0\nהצליחו"); self.card_fail=QLabel("0\nנכשלו");
        for c in (self.card_pending,self.card_ok,self.card_fail): c.setObjectName('Metric'); cards.addWidget(c)
        l.addLayout(cards); self.queue=QListWidget(); l.addWidget(self.queue,1); r=QHBoxLayout(); undo=QPushButton("בטל פעולה אחרונה"); undo.clicked.connect(self.undo); r.addWidget(undo); retry=QPushButton("נסה שוב את הכשלים"); retry.clicked.connect(self.retry_failed); r.addWidget(retry); r.addStretch(); l.addLayout(r); return p

    def search_page(self):
        p=QWidget(); l=QVBoxLayout(p); l.setContentsMargins(34,28,34,26); l.addLayout(self.page_title("חיפוש", "מצא שירים, תיקיות וקבצים מכל הספריות שהרשית.")); row=QHBoxLayout(); self.search_edit=QLineEdit(); self.search_edit.setPlaceholderText("חפש לפי שם…"); go=QPushButton("חפש"); go.setObjectName('Primary'); go.clicked.connect(self.run_search); row.addWidget(self.search_edit,1); row.addWidget(go); l.addLayout(row); self.results=QListWidget(); self.results.itemDoubleClicked.connect(self.open_search); l.addWidget(self.results,1); return p

    def refresh(self):
        self.roots.clear(); valid=[]
        for x in self.config.get('roots',[]):
            p=resolve(x)
            if p.exists() and p.is_dir(): valid.append(str(p)); it=QListWidgetItem('◼  '+display_name(p)); it.setData(Qt.UserRole,str(p)); self.roots.addItem(it)
        self.config['roots']=valid; save_cfg(self.config); self.model.roots=valid
        if self.roots.count(): self.roots.setCurrentRow(0)
        else: self.current=None; self.breadcrumb.setText('הוסף תיקייה כדי להתחיל'); self.items.clear()

    def add_root(self):
        path=QFileDialog.getExistingDirectory(self,'בחר תיקייה');
        if not path:return
        p=resolve(path); rs=[resolve(x) for x in self.config.get('roots',[])]
        if any(p==r or p.is_relative_to(r) or r.is_relative_to(p) for r in rs): QMessageBox.information(self,'כבר קיימת','התיקייה כבר כלולה באזור הרשאה קיים.'); return
        self.config['roots'].append(str(p)); save_cfg(self.config); self.model.roots=self.config['roots']; self.refresh()

    def remove_root(self):
        it=self.roots.currentItem();
        if not it:return
        p=resolve(it.data(Qt.UserRole))
        if QMessageBox.question(self,'הסר גישה',f'להסיר את «{display_name(p)}»?\n\nשום קובץ לא יימחק מהמחשב.')==QMessageBox.Yes:
            self.config['roots']=[x for x in self.config['roots'] if resolve(x)!=p]; save_cfg(self.config); self.refresh()

    def root_selected(self,it,_prev=None):
        if it:self.current=resolve(it.data(Qt.UserRole)); self.render()

    def selected_changed(self):
        it=self.items.currentItem(); self.selection_info.setText(display_name(resolve(it.data(Qt.UserRole))) if it else '')

    def render(self,*_):
        if not self.current:return
        self.breadcrumb.setText(str(self.current)); self.items.clear(); q=self.filter.text().strip().casefold()
        for p,state in self.model.virtual_entries(self.current):
            if q and q not in p.name.casefold():continue
            text=f"{icon_for(p)}   {p.name}"
            if state=='pending': text+='   ·  ממתין'
            it=QListWidgetItem(text); it.setData(Qt.UserRole,str(p)); self.items.addItem(it)
        self.update_activity()

    def open_item(self,it):
        p=resolve(it.data(Qt.UserRole))
        if p.is_dir():self.current=p;self.render()

    def go_up(self):
        if not self.current:return
        roots=[resolve(x) for x in self.config.get('roots',[])]
        if self.current not in roots:self.current=self.current.parent;self.render()

    def selected_path(self):
        it=self.items.currentItem();return resolve(it.data(Qt.UserRole)) if it else None

    def conflict(self,new):
        return any(p.name.casefold()==new.name.casefold() for p,_ in self.model.virtual_entries(new.parent) if resolve(p)!=resolve(new))

    def mkdir(self):
        if not self.current:return
        name,ok=QInputDialog.getText(self,'תיקייה חדשה','שם התיקייה');
        if ok and name.strip():
            p=resolve(self.current/safe_name(name));
            if self.conflict(p):QMessageBox.warning(self,'השם כבר קיים','בחר שם אחר.');return
            self.model.stage(Operation('mkdir',f'יצירת תיקייה · {p.name}',path=str(p)));self.mark_dirty()

    def add_media(self):
        if not self.current:return
        d=MediaDialog(self.current,self)
        if d.exec()!=QDialog.Accepted:return
        payloads=d.payloads()
        if len(payloads)>1:
            p=PlaylistDialog(payloads,self)
            if p.exec()!=QDialog.Accepted:return
        for payload in payloads:self.model.stage(Operation('download',f"הורדה · {payload['title']}",payload=payload))
        self.mark_dirty();self.pages.setCurrentIndex(1)

    def rename(self):
        old=self.selected_path();
        if not old:return
        name,ok=QInputDialog.getText(self,'שינוי שם','השם החדש:',text=old.stem if old.is_file() else old.name)
        if not ok:return
        new_name=safe_name(name)
        if old.is_file():new_name += old.suffix
        new=resolve(old.parent/new_name)
        if new==old:return
        if self.conflict(new):QMessageBox.warning(self,'השם כבר קיים','כבר קיים פריט בשם הזה.');return
        self.model.stage(Operation('rename',f'שינוי שם · {old.name} → {new.name}',old=str(old),new=str(new)));self.mark_dirty()

    def move_item(self):
        old=self.selected_path();
        if not old:return
        dest=QFileDialog.getExistingDirectory(self,'בחר תיקיית יעד',str(old.parent));
        if not dest:return
        dest=resolve(dest);new=dest/old.name
        if not self.model.allowed(dest) or self.conflict(new):QMessageBox.warning(self,'אי אפשר להעביר','יעד לא מורשה או שיש בו כבר פריט עם אותו שם.');return
        self.model.stage(Operation('move',f'העברה · {old.name}',old=str(old),new=str(new)));self.mark_dirty()

    def delete_item(self):
        p=self.selected_path();
        if not p:return
        if QMessageBox.question(self,'מחיקה בהמתנה',f'לסמן את «{p.name}» למחיקה?\n\nהקובץ לא יימחק עד שמירת השינויים.')==QMessageBox.Yes:self.model.stage(Operation('delete',f'מחיקה · {p.name}',path=str(p)));self.mark_dirty()

    def mark_dirty(self):
        self.status.setText(f'יש {len(self.model.pending)} שינויים שלא נשמרו');self.saveb.setText(f'שמירת שינויים · {len(self.model.pending)}');self.saveb.setEnabled(True);self.render()

    def save_changes(self):
        if not self.model.pending or self.engine:return
        self.engine=ApplyEngine(self.model);self.saveb.setEnabled(False);self.saveb.setText('שומר…');self.status.setText('מבצע שינויים במחשב…');w=Worker(lambda:self.engine.apply());w.signals.result.connect(self.save_done);w.signals.error.connect(self.save_error);self.pool.start(w)

    def save_done(self,results):
        for op in results:
            if op.status=='success':self.model.history.append(op)
        self.model.pending=[op for op in self.model.pending if op.status=='failed']
        self.engine=None;self.saveb.setEnabled(bool(self.model.pending));self.saveb.setText('שמירת שינויים' + (f' · {len(self.model.pending)}' if self.model.pending else ''));self.status.setText('נשמר בהצלחה' if not self.model.pending else 'יש פעולות שנכשלו');self.render();self.update_activity()

    def save_error(self,e):
        self.engine=None;self.saveb.setEnabled(True);self.status.setText('שגיאה בביצוע');QMessageBox.critical(self,'שגיאה',e)

    def undo(self):
        if self.model.undo_last():self.mark_dirty()

    def retry_failed(self):
        changed=False
        for op in self.model.pending:
            if op.status=='failed':op.status='pending';op.error='';changed=True
        if changed:self.save_changes()

    def update_activity(self):
        if not hasattr(self,'queue'):return
        self.queue.clear();
        for op in self.model.pending:
            state='נכשל' if op.status=='failed' else 'ממתין'
            extra=f' · {op.error}' if op.error else ''
            self.queue.addItem(QListWidgetItem(f'•  {op.label}  ·  {state}{extra}'))
        for op in reversed(self.model.history[-100:]):self.queue.addItem(QListWidgetItem(f'✓  {op.label}  ·  הושלם'))
        pending=sum(1 for x in self.model.pending if x.status=='pending');failed=sum(1 for x in self.model.pending if x.status=='failed');done=sum(1 for x in self.model.history if x.status=='success')
        self.card_pending.setText(f'{pending}\nממתינים');self.card_fail.setText(f'{failed}\nנכשלו');self.card_ok.setText(f'{done}\nהצליחו')

    def tick(self):
        if self.engine:
            ops=[x for x in self.model.pending if x.status=='pending']; vals=[x.progress for x in ops if getattr(x,'progress',0)>0]
            self.totalbar.setValue(int(sum(vals)/len(vals)) if vals else 0);self.update_activity()
        else:self.totalbar.setValue(0)

    def run_search(self):
        q=self.search_edit.text().strip().casefold();
        if not q:return
        roots=[resolve(x) for x in self.config.get('roots',[])];self.results.clear();w=Worker(self.search_worker,roots,q);w.signals.result.connect(self.show_results);self.pool.start(w);self.status.setText('מחפש…')

    @staticmethod
    def search_worker(roots,q):
        out=[]
        for root in roots:
            if not root.exists():continue
            try:
                for p in root.rglob('*'):
                    if q in p.name.casefold():out.append(str(p))
                    if len(out)>=2000:return out
            except OSError:pass
        return out

    def show_results(self,arr):
        self.results.clear()
        for raw in arr:
            p=resolve(raw);it=QListWidgetItem(f'{icon_for(p)}   {p.name}   ·   {p.parent}');it.setData(Qt.UserRole,raw);self.results.addItem(it)
        self.status.setText(f'נמצאו {len(arr)} תוצאות')

    def open_search(self,it):
        p=resolve(it.data(Qt.UserRole));self.current=p if p.is_dir() else p.parent;self.pages.setCurrentIndex(0);self.render()


STYLE=r'''
*{font-family:"Segoe UI";font-size:14px;}
QWidget{background:#0a0c0f;color:#eef2f6;}
#Sidebar{background:#0e1116;border-left:1px solid #20252d;}
#Brand{font-size:31px;font-weight:800;letter-spacing:-1.5px;}
#PageTitle{font-size:35px;font-weight:800;letter-spacing:-1.2px;}
#DialogTitle{font-size:27px;font-weight:800;}
#MediaTitle{font-size:20px;font-weight:700;}
#SectionTitle{font-size:17px;font-weight:700;}
#Muted,#Tiny{color:#858e9b;}
#Tiny{font-size:11px;font-weight:800;}
#StatusPill{background:#151a21;border:1px solid #2b333e;border-radius:11px;padding:10px 12px;color:#bdc5cf;font-weight:700;}
#NavButton{background:transparent;border:0;text-align:right;border-radius:11px;padding:13px 14px;color:#aeb7c2;font-size:15px;font-weight:650;}
#NavButton:hover{background:#181d24;color:#fff;}
QLineEdit,QComboBox{background:#10151b;border:1px solid #2a313b;border-radius:11px;padding:11px 12px;color:#f3f5f7;}
QLineEdit:focus,QComboBox:focus{border:1px solid #6f7a88;}
QPushButton{background:#151a21;border:1px solid #29313a;border-radius:11px;padding:10px 15px;color:#eef2f6;font-weight:650;}
QPushButton:hover{background:#20262e;}
#Primary{background:#f4f6f8;color:#090b0e;border:0;font-weight:800;}
#Primary:hover{background:#fff;}
QListWidget{background:#0f1318;border:1px solid #242b34;border-radius:15px;padding:8px;}
QListWidget::item{padding:14px;border-radius:10px;margin:2px 0;}
QListWidget::item:hover{background:#171d25;}
QListWidget::item:selected{background:#252c35;}
#Card,#InfoStrip{background:#11161c;border:1px solid #252d36;border-radius:15px;}
#InfoStrip{padding:2px;}
#Thumb{background:#0a0d11;border:1px solid #28303a;border-radius:12px;color:#65707c;font-weight:800;}
#Metric{background:#11161c;border:1px solid #262e38;border-radius:14px;padding:15px;font-size:16px;font-weight:800;}
QProgressBar{background:#151a21;border:0;border-radius:7px;height:10px;text-align:center;color:transparent;}
QProgressBar::chunk{background:#e8ecf0;border-radius:7px;}
QSplitter::handle{background:#1c222a;}
QScrollBar:vertical{background:#0a0c0f;width:10px;}QScrollBar::handle:vertical{background:#2e3640;border-radius:5px;min-height:30px;}
'''


def main():
    app=QApplication(sys.argv);app.setApplicationName(APP);app.setStyleSheet(STYLE);win=Main();win.show();sys.exit(app.exec())

if __name__=='__main__':main()
