from __future__ import annotations

import json
import os
import sys
import urllib.request
from pathlib import Path

from PySide6.QtCore import QObject, QRunnable, QThreadPool, Qt, Signal, Slot
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QApplication, QComboBox, QDialog, QFileDialog, QFrame, QHBoxLayout,
    QLabel, QLineEdit, QListWidget, QListWidgetItem, QMainWindow,
    QMessageBox, QPushButton, QProgressBar, QScrollArea, QSplitter,
    QStackedWidget, QVBoxLayout, QWidget, QInputDialog,
)

from tunevault_engine import ApplyEngine, LibraryModel, MediaPayload, Operation, resolve, safe_name

APP_NAME = 'TuneVault'
APP_DIR = Path(os.getenv('APPDATA', str(Path.home()))) / APP_NAME
APP_DIR.mkdir(parents=True, exist_ok=True)
CONFIG_FILE = APP_DIR / 'config.json'
AUDIO_EXT = {'.mp3','.wav','.flac','.m4a','.aac','.ogg','.opus'}
VIDEO_EXT = {'.mp4','.mkv','.webm','.mov','.avi','.m4v'}


def load_config() -> dict:
    try:
        data = json.loads(CONFIG_FILE.read_text(encoding='utf-8')) if CONFIG_FILE.exists() else {}
        return data if isinstance(data, dict) else {'roots': []}
    except (OSError, json.JSONDecodeError):
        return {'roots': []}


def save_config(data: dict) -> None:
    tmp = CONFIG_FILE.with_suffix('.tmp')
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
    tmp.replace(CONFIG_FILE)


def icon_for(path: Path) -> str:
    if path.is_dir(): return '📁'
    if path.suffix.lower() in AUDIO_EXT: return '🎵'
    if path.suffix.lower() in VIDEO_EXT: return '🎬'
    return '📄'


def duration_text(seconds) -> str:
    if not seconds: return ''
    total = max(0, int(seconds)); minutes, sec = divmod(total, 60); hours, minutes = divmod(minutes, 60)
    return f'{hours}:{minutes:02d}:{sec:02d}' if hours else f'{minutes}:{sec:02d}'


class WorkerSignals(QObject):
    result = Signal(object)
    error = Signal(str)
    done = Signal()
    progress = Signal(object)


class Worker(QRunnable):
    def __init__(self, function, *args, **kwargs):
        super().__init__(); self.function=function; self.args=args; self.kwargs=kwargs; self.signals=WorkerSignals()
    @Slot()
    def run(self):
        try: self.signals.result.emit(self.function(*self.args, **self.kwargs))
        except Exception as exc: self.signals.error.emit(str(exc))
        finally: self.signals.done.emit()


class MediaDialog(QDialog):
    def __init__(self, target: Path, parent=None):
        super().__init__(parent); self.target=resolve(target); self.info={}; self.entries=[]
        self.setWindowTitle('הוספת מדיה'); self.resize(850, 680); self.build()
    def build(self):
        l=QVBoxLayout(self); title=QLabel('הוספת מדיה'); title.setObjectName('DialogTitle'); l.addWidget(title)
        sub=QLabel('הדבק קישור למדיה או לפלייליסט. אפשר לשנות את השם לפני שהפעולה נכנסת לתור.'); sub.setObjectName('Muted'); sub.setWordWrap(True); l.addWidget(sub)
        row=QHBoxLayout(); self.url=QLineEdit(); self.url.setPlaceholderText('קישור למדיה / פלייליסט…'); self.detect_btn=QPushButton('זיהוי'); self.detect_btn.setObjectName('Primary'); self.detect_btn.clicked.connect(self.detect); row.addWidget(self.url,1); row.addWidget(self.detect_btn); l.addLayout(row)
        preview=QFrame(); preview.setObjectName('Panel'); pv=QHBoxLayout(preview); self.thumb=QLabel('תצוגה מקדימה'); self.thumb.setObjectName('Thumbnail'); self.thumb.setAlignment(Qt.AlignCenter); self.thumb.setFixedSize(240,135); pv.addWidget(self.thumb)
        md=QVBoxLayout(); self.media_title=QLabel('לא זוהתה מדיה'); self.media_title.setObjectName('MediaTitle'); self.media_meta=QLabel(''); self.media_meta.setObjectName('Muted'); self.media_meta.setWordWrap(True); md.addWidget(self.media_title); md.addWidget(self.media_meta); md.addStretch(); pv.addLayout(md,1); l.addWidget(preview)
        self.single=QFrame(); sv=QVBoxLayout(self.single); sv.addWidget(QLabel('שם הקובץ הסופי')); self.name=QLineEdit(); self.name.setPlaceholderText('שם השיר / הקובץ'); self.name.setEnabled(False); sv.addWidget(self.name); fr=QHBoxLayout(); fr.addWidget(QLabel('פורמט')); self.format=QComboBox(); self.format.addItems(['MP3','MP4']); self.format.setEnabled(False); fr.addWidget(self.format,1); sv.addLayout(fr); l.addWidget(self.single)
        self.playlist=QFrame(); pl=QVBoxLayout(self.playlist); self.pl_title=QLabel(); self.pl_title.setObjectName('SectionTitle'); pl.addWidget(self.pl_title); self.pl_list=QListWidget(); pl.addWidget(self.pl_list,1); self.playlist.hide(); l.addWidget(self.playlist,1)
        ar=QHBoxLayout(); ar.addStretch(); c=QPushButton('ביטול'); c.clicked.connect(self.reject); self.ok=QPushButton('הוספה לתור'); self.ok.setObjectName('Primary'); self.ok.setEnabled(False); self.ok.clicked.connect(self.accept); ar.addWidget(c); ar.addWidget(self.ok); l.addLayout(ar)
    @staticmethod
    def extract(url):
        import yt_dlp
        with yt_dlp.YoutubeDL({'quiet':True,'no_warnings':True,'skip_download':True,'noplaylist':False}) as ydl: return ydl.extract_info(url, download=False)
    def detect(self):
        url=self.url.text().strip()
        if not url:return
        self.detect_btn.setEnabled(False); self.detect_btn.setText('מזהה…'); w=Worker(self.extract,url); w.signals.result.connect(self.detected); w.signals.error.connect(lambda e:QMessageBox.warning(self,'הזיהוי נכשל',e)); w.signals.done.connect(lambda:(self.detect_btn.setEnabled(True),self.detect_btn.setText('זיהוי'))); QThreadPool.globalInstance().start(w)
    def detected(self,info):
        self.info=info or {}; self.entries=[e for e in (self.info.get('entries') or []) if e]; title=self.info.get('title') or 'ללא שם'; creator=self.info.get('uploader') or self.info.get('channel') or ''; dur=duration_text(self.info.get('duration')); meta=' · '.join(x for x in (creator,dur) if x); self.media_title.setText(title); self.media_meta.setText(meta or ('פלייליסט · %d פריטים'%len(self.entries) if self.entries else 'מדיה')); self.name.setText(title); self.name.setEnabled(True); self.format.setEnabled(True); self.ok.setEnabled(True)
        if self.entries:
            self.single.hide(); self.playlist.show(); self.pl_title.setText(f'פלייליסט · {len(self.entries)} פריטים'); self.pl_list.clear(); [self.pl_list.addItem(e.get('title') or 'ללא שם') for e in self.entries]
        else: self.single.show(); self.playlist.hide()
        thumb=self.info.get('thumbnail')
        if thumb:
            w=Worker(self.thumb_data,thumb); w.signals.result.connect(self.show_thumb); QThreadPool.globalInstance().start(w)
    @staticmethod
    def thumb_data(url):
        req=urllib.request.Request(url,headers={'User-Agent':'Mozilla/5.0'}); 
        with urllib.request.urlopen(req,timeout=10) as r:return r.read()
    def show_thumb(self,data):
        p=QPixmap();
        if p.loadFromData(data): self.thumb.setText(''); self.thumb.setPixmap(p.scaled(self.thumb.size(),Qt.KeepAspectRatio,Qt.SmoothTransformation))
    def payloads(self):
        if self.entries:
            return [MediaPayload(url=e.get('webpage_url') or e.get('original_url') or '', title=e.get('title') or 'ללא שם', target=str(self.target), format='MP3') for e in self.entries if e.get('webpage_url') or e.get('original_url')]
        return [MediaPayload(url=self.url.text().strip(), title=self.name.text().strip() or 'ללא שם', target=str(self.target), format=self.format.currentText())]


class PlaylistDialog(QDialog):
    def __init__(self,payloads,parent=None):
        super().__init__(parent); self.payloads_data=payloads; self.setWindowTitle('עריכת פלייליסט'); self.resize(900,650); l=QVBoxLayout(self); t=QLabel(f'עריכת פלייליסט · {len(payloads)} שירים'); t.setObjectName('DialogTitle'); l.addWidget(t); s=QLabel('אפשר לשנות שם ופורמט לכל שיר.'); s.setObjectName('Muted'); l.addWidget(s); q=QHBoxLayout()
        for text,fmt in [('הכול MP3','MP3'),('הכול MP4','MP4')]: b=QPushButton(text); b.clicked.connect(lambda _,f=fmt:self.set_all(f)); q.addWidget(b)
        q.addStretch(); l.addLayout(q); self.rows=QListWidget(); l.addWidget(self.rows,1)
        for p in payloads:
            item=QListWidgetItem(); w=QWidget(); r=QHBoxLayout(w); r.setContentsMargins(10,6,10,6); edit=QLineEdit(p.title); edit.textChanged.connect(lambda v,x=p:setattr(x,'title',v or 'ללא שם')); combo=QComboBox(); combo.addItems(['MP3','MP4']); combo.currentTextChanged.connect(lambda v,x=p:setattr(x,'format',v)); r.addWidget(edit,2); r.addWidget(combo); self.rows.addItem(item); self.rows.setItemWidget(item,w)
        a=QHBoxLayout(); a.addStretch(); c=QPushButton('ביטול'); c.clicked.connect(self.reject); ok=QPushButton('הוספה לתור'); ok.setObjectName('Primary'); ok.clicked.connect(self.accept); a.addWidget(c); a.addWidget(ok); l.addLayout(a)
    def set_all(self,fmt):
        for i,p in enumerate(self.payloads_data):
            p.format=fmt; w=self.rows.itemWidget(self.rows.item(i)); combo=w.findChild(QComboBox) if w else None
            if combo: combo.setCurrentText(fmt)


class OperationRow(QFrame):
    def __init__(self,operation):
        super().__init__(); self.operation=operation; self.setObjectName('OperationRow'); l=QVBoxLayout(self); l.setContentsMargins(16,13,16,13); top=QHBoxLayout(); self.icon=QLabel('🟡'); self.icon.setFixedWidth(26); self.label=QLabel(operation.label); self.label.setObjectName('RowTitle'); self.label.setWordWrap(True); self.pct=QLabel(f'{operation.progress}%'); self.pct.setObjectName('Muted'); top.addWidget(self.icon); top.addWidget(self.label,1); top.addWidget(self.pct); l.addLayout(top); self.bar=QProgressBar(); self.bar.setValue(operation.progress); l.addWidget(self.bar); self.update(operation)
    def update(self,operation):
        self.operation=operation; self.bar.setValue(operation.progress); self.pct.setText(f'{operation.progress}%'); text=operation.label + (f'  ·  {operation.error}' if operation.error else ''); self.label.setText(text); status=operation.status; self.icon.setText({'pending':'🟡','running':'🔵','success':'🟢','failed':'🔴','cancelled':'⚪'}.get(status,'🟡')); self.bar.setVisible(status!='success'); self.pct.setVisible(status!='success')


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__(); self.config=load_config(); self.model=LibraryModel(self.config.get('roots',[])); self.current_folder=None; self.pool=QThreadPool.globalInstance(); self.engine=None; self.saving=False; self.operation_rows={}; self.setWindowTitle(APP_NAME); self.resize(1500,940); self.setMinimumSize(1180,760); self.build(); self.refresh_roots()
    def build(self):
        central=QWidget(); main=QHBoxLayout(central); main.setContentsMargins(0,0,0,0); main.setSpacing(0); self.setCentralWidget(central)
        side=QFrame(); side.setObjectName('Sidebar'); side.setFixedWidth(270); sl=QVBoxLayout(side); sl.setContentsMargins(22,28,22,24); brand=QLabel('TuneVault'); brand.setObjectName('Brand'); sl.addWidget(brand); tag=QLabel('המדיה שלך.\nהסדר שלך.\nהמחשב שלך.'); tag.setObjectName('Muted'); sl.addWidget(tag); sl.addSpacing(30); self.nav=[]
        for text,i in [('ספרייה',0),('הורדות',1),('חיפוש',2)]: b=QPushButton(text); b.setObjectName('NavButton'); b.clicked.connect(lambda _,x=i:self.navigate(x)); sl.addWidget(b); self.nav.append(b)
        sl.addStretch(); self.status=QLabel('מוכן'); self.status.setObjectName('StatusPill'); sl.addWidget(self.status); main.addWidget(side); self.pages=QStackedWidget(); main.addWidget(self.pages,1); self.pages.addWidget(self.library_page()); self.pages.addWidget(self.downloads_page()); self.pages.addWidget(self.search_page())
    def navigate(self,i):
        self.pages.setCurrentIndex(i)
        for n,b in enumerate(self.nav): b.setProperty('active',n==i); b.style().unpolish(b); b.style().polish(b)
    def library_page(self):
        p=QWidget(); l=QVBoxLayout(p); l.setContentsMargins(34,30,34,28); h=QHBoxLayout(); v=QVBoxLayout(); t=QLabel('הספרייה שלך'); t.setObjectName('PageTitle'); s=QLabel('שינויים נבנים מראש ונכנסים למחשב רק אחרי שמירת שינויים.'); s.setObjectName('Muted'); v.addWidget(t); v.addWidget(s); h.addLayout(v,1); self.filter=QLineEdit(); self.filter.setPlaceholderText('חיפוש בתיקייה…'); self.filter.textChanged.connect(self.render); h.addWidget(self.filter); a=QPushButton('+ הוסף ספרייה'); a.setObjectName('Primary'); a.clicked.connect(self.add_library); h.addWidget(a); l.addLayout(h)
        cards=QHBoxLayout(); self.metric_locations=QLabel('0\nמיקומים'); self.metric_pending=QLabel('0\nממתינים'); self.metric_items=QLabel('0\nפריטים');
        for x in (self.metric_locations,self.metric_pending,self.metric_items): x.setObjectName('MetricCard'); cards.addWidget(x)
        l.addLayout(cards); split=QSplitter(Qt.Horizontal); left=QWidget(); ll=QVBoxLayout(left); ll.setContentsMargins(0,14,0,0); e=QLabel('מיקומים מורשים'); e.setObjectName('Eyebrow'); ll.addWidget(e); self.roots=QListWidget(); ll.addWidget(self.roots,1); rm=QPushButton('הסר גישה'); rm.clicked.connect(self.remove_library); ll.addWidget(rm); split.addWidget(left)
        right=QWidget(); rl=QVBoxLayout(right); rl.setContentsMargins(18,14,0,0); bar=QHBoxLayout(); self.path=QLabel('בחר ספרייה'); self.path.setObjectName('SectionTitle'); bar.addWidget(self.path,1); up=QPushButton('↑ אחורה'); up.clicked.connect(self.go_up); rf=QPushButton('↻ רענן'); rf.clicked.connect(self.render); bar.addWidget(up); bar.addWidget(rf); rl.addLayout(bar); self.items=QListWidget(); self.items.itemDoubleClicked.connect(self.open_item); rl.addWidget(self.items,1); acts=QHBoxLayout()
        for text,fn in [('+ תיקייה',self.stage_folder),('+ הוסף מדיה',self.stage_media),('שנה שם',self.rename_selected),('העבר',self.move_selected),('מחק',self.delete_selected)]: b=QPushButton(text); b.clicked.connect(fn); acts.addWidget(b)
        acts.addStretch(); self.save_button=QPushButton('שמירת שינויים'); self.save_button.setObjectName('Primary'); self.save_button.setEnabled(False); self.save_button.clicked.connect(self.save_changes); acts.addWidget(self.save_button); rl.addLayout(acts); split.addWidget(right); split.setSizes([320,1100]); l.addWidget(split,1); self.roots.currentItemChanged.connect(self.select_root); return p
    def downloads_page(self):
        p=QWidget(); l=QVBoxLayout(p); l.setContentsMargins(34,30,34,28); t=QLabel('הורדות'); t.setObjectName('PageTitle'); l.addWidget(t); s=QLabel('תור עבודה חי — בלי חלונות CMD ובלי להסתיר כשלונות.'); s.setObjectName('Muted'); l.addWidget(s); self.download_summary=QLabel('אין פעולות כרגע'); self.download_summary.setObjectName('SectionTitle'); l.addWidget(self.download_summary); self.total_bar=QProgressBar(); self.total_bar.setValue(0); l.addWidget(self.total_bar); scroll=QScrollArea(); scroll.setWidgetResizable(True); scroll.setFrameShape(QFrame.NoFrame); box=QWidget(); self.queue_layout=QVBoxLayout(box); self.queue_layout.setAlignment(Qt.AlignTop); scroll.setWidget(box); l.addWidget(scroll,1); r=QHBoxLayout(); undo=QPushButton('בטל פעולה אחרונה'); undo.clicked.connect(self.undo_last); r.addWidget(undo); r.addStretch(); self.stop=QPushButton('עצור שמירה'); self.stop.setEnabled(False); self.stop.clicked.connect(self.stop_saving); r.addWidget(self.stop); l.addLayout(r); return p
    def search_page(self):
        p=QWidget(); l=QVBoxLayout(p); l.setContentsMargins(34,30,34,28); t=QLabel('חיפוש'); t.setObjectName('PageTitle'); l.addWidget(t); s=QLabel('חיפוש בכל המיקומים שהרשית ל-TuneVault.'); s.setObjectName('Muted'); l.addWidget(s); r=QHBoxLayout(); self.search=QLineEdit(); self.search.setPlaceholderText('חפש שיר, תיקייה או קובץ…'); self.search.returnPressed.connect(self.run_search); go=QPushButton('חיפוש'); go.setObjectName('Primary'); go.clicked.connect(self.run_search); r.addWidget(self.search,1); r.addWidget(go); l.addLayout(r); self.results=QListWidget(); self.results.itemDoubleClicked.connect(self.open_result); l.addWidget(self.results,1); return p
    def refresh_roots(self):
        self.roots.clear(); valid=[]
        for raw in self.config.get('roots',[]):
            p=resolve(raw)
            if p.exists() and p.is_dir(): valid.append(str(p)); it=QListWidgetItem(f'📁  {p.name or p}'); it.setData(Qt.UserRole,str(p)); self.roots.addItem(it)
        self.config['roots']=valid; save_config(self.config); self.model.roots=valid; self.metric_locations.setText(f'{len(valid)}\nמיקומים')
        if self.roots.count(): self.roots.setCurrentRow(0)
        else: self.current_folder=None; self.path.setText('הוסף ספרייה כדי להתחיל'); self.items.clear()
    def add_library(self):
        value=QFileDialog.getExistingDirectory(self,'בחר תיקייה');
        if not value:return
        p=resolve(value); roots=[resolve(x) for x in self.config.get('roots',[])]
        if any(p==r or p.is_relative_to(r) or r.is_relative_to(p) for r in roots): QMessageBox.information(self,'כבר קיים','התיקייה כבר מכוסה על ידי מיקום מורשה.'); return
        self.config.setdefault('roots',[]).append(str(p)); save_config(self.config); self.model.roots=self.config['roots']; self.refresh_roots()
    def remove_library(self):
        it=self.roots.currentItem();
        if not it:return
        p=resolve(it.data(Qt.UserRole));
        if QMessageBox.question(self,'הסר גישה',f'להסיר את «{p.name}»?\n\nשום קובץ לא יימחק.')==QMessageBox.Yes: self.config['roots']=[x for x in self.config.get('roots',[]) if resolve(x)!=p]; save_config(self.config); self.model.roots=self.config['roots']; self.refresh_roots()
    def select_root(self,it,_prev=None):
        if it:self.current_folder=resolve(it.data(Qt.UserRole)); self.render()
    def render(self,*_):
        if not self.current_folder:return
        self.path.setText(str(self.current_folder)); q=self.filter.text().strip().casefold(); self.items.clear(); count=0
        for p,state in self.model.virtual_entries(self.current_folder):
            if q and q not in p.name.casefold():continue
            if p.is_file() and p.suffix.lower() in AUDIO_EXT|VIDEO_EXT: count+=1
            label=f'{icon_for(p)}  {p.name}' + ('   ·   ממתין' if state=='pending' else '')
            it=QListWidgetItem(label); it.setData(Qt.UserRole,str(p)); it.setToolTip(str(p)); self.items.addItem(it)
        self.metric_items.setText(f'{count}\nפריטי מדיה'); self.metric_pending.setText(f'{len(self.model.pending)}\nממתינים'); self.refresh_queue()
    def open_item(self,it):
        p=resolve(it.data(Qt.UserRole));
        if p.is_dir(): self.current_folder=p; self.render()
    def go_up(self):
        if not self.current_folder:return
        roots=[resolve(x) for x in self.config.get('roots',[])]; parent=self.current_folder.parent
        if self.current_folder not in roots and self.model.allowed(parent): self.current_folder=parent; self.render()
    def selected(self):
        it=self.items.currentItem(); return resolve(it.data(Qt.UserRole)) if it else None
    def conflict(self,path):
        target=resolve(path); return any(resolve(x)==target or x.name.casefold()==target.name.casefold() for x,_ in self.model.virtual_entries(target.parent))
    def stage_folder(self):
        if not self.current_folder:return
        value,ok=QInputDialog.getText(self,'תיקייה חדשה','שם התיקייה:');
        if not ok:return
        target=resolve(self.current_folder/safe_name(value))
        if self.conflict(target): QMessageBox.warning(self,'שם בשימוש','כבר קיים פריט בשם הזה.'); return
        try:self.model.stage(Operation('mkdir',f'יצירת תיקייה · {target.name}',path=str(target)))
        except Exception as e: QMessageBox.warning(self,'לא ניתן ליצור',str(e)); return
        self.mark_dirty()
    def stage_media(self):
        if not self.current_folder:return
        d=MediaDialog(self.current_folder,self)
        if d.exec()!=QDialog.Accepted:return
        payloads=d.payloads()
        if len(payloads)>1:
            p=PlaylistDialog(payloads,self)
            if p.exec()!=QDialog.Accepted:return
        for payload in payloads:
            try:self.model.stage(Operation('download',f'הורדה · {payload.title} [{payload.normalized_format()}]',payload=payload))
            except Exception as e: QMessageBox.warning(self,'לא ניתן להוסיף',str(e))
        self.mark_dirty(); self.navigate(1)
    def rename_selected(self):
        old=self.selected();
        if not old:return
        current=old.stem if old.is_file() else old.name; value,ok=QInputDialog.getText(self,'שינוי שם','השם החדש:',text=current)
        if not ok:return
        clean=safe_name(value)+(old.suffix if old.is_file() else ''); new=resolve(old.parent/clean)
        if new==old:return
        if self.conflict(new): QMessageBox.warning(self,'שם בשימוש','כבר קיים פריט בשם הזה.'); return
        try:self.model.stage(Operation('rename',f'שינוי שם · {old.name} → {new.name}',old=str(old),new=str(new)))
        except Exception as e: QMessageBox.warning(self,'לא ניתן לשנות שם',str(e)); return
        self.mark_dirty()
    def move_selected(self):
        old=self.selected();
        if not old:return
        destination=QFileDialog.getExistingDirectory(self,'בחר תיקיית יעד',str(old.parent));
        if not destination:return
        dest=resolve(destination); new=dest/old.name
        if not self.model.allowed(dest): QMessageBox.warning(self,'העברה חסומה','יעד ההעברה אינו מיקום מורשה.'); return
        if self.conflict(new): QMessageBox.warning(self,'שם בשימוש','קיים פריט באותו שם ביעד.'); return
        try:self.model.stage(Operation('move',f'העברה · {old.name} → {dest.name}',old=str(old),new=str(new)))
        except Exception as e: QMessageBox.warning(self,'לא ניתן להעביר',str(e)); return
        self.mark_dirty()
    def delete_selected(self):
        p=self.selected();
        if not p:return
        if QMessageBox.question(self,'מחיקה',f'לסמן את «{p.name}» למחיקה?\n\nהמחיקה תתבצע רק בשמירה.')!=QMessageBox.Yes:return
        try:self.model.stage(Operation('delete',f'מחיקה · {p.name}',path=str(p)))
        except Exception as e: QMessageBox.warning(self,'לא ניתן למחוק',str(e)); return
        self.mark_dirty()
    def mark_dirty(self):
        n=len(self.model.pending); self.status.setText(f'{n} פעולות ממתינות'); self.save_button.setEnabled(n>0); self.save_button.setText(f'שמירת שינויים · {n}' if n else 'הכול נשמר'); self.render(); self.refresh_queue()
    def save_changes(self):
        if self.saving or not self.model.pending:return
        self.saving=True; self.stop.setEnabled(True); self.save_button.setEnabled(False); self.save_button.setText('שומר…'); self.status.setText('מבצע שינויים…'); self.engine=ApplyEngine(self.model); w=Worker(self.run_engine); w.signals.result.connect(self.save_finished); w.signals.error.connect(self.save_error); w.signals.progress.connect(self.progress_received); self.active_worker=w; self.pool.start(w)
    def run_engine(self):
        return self.engine.apply(lambda op,i,t:self.active_worker.signals.progress.emit(op)) if self.engine else []
    def progress_received(self,op):
        row=self.operation_rows.get(op.id)
        if row: row.update(op)
        self.total_bar.setValue(op.progress); self.download_summary.setText(f'{op.label}  ·  {op.progress}%'); self.refresh_queue(update_only=True)
    def stop_saving(self):
        if self.engine:self.engine.cancel(); self.status.setText('עוצר…')
    def save_finished(self,results):
        success=[x for x in results if x.status=='success']; remain=[x for x in results if x.status in {'failed','cancelled'}]
        with self.model._lock:self.model.history.extend(success); self.model.pending=remain
        self.saving=False; self.stop.setEnabled(False); self.engine=None; self.mark_dirty()
        if remain:self.status.setText(f'{len(remain)} פעולות דורשות טיפול')
        else:self.status.setText('הכול נשמר בהצלחה'); self.save_button.setEnabled(False); self.save_button.setText('הכול נשמר'); self.total_bar.setValue(100)
        if success and remain: QMessageBox.warning(self,'שמירה חלקית',f'{len(success)} פעולות הצליחו. {len(remain)} נשארו בתור.')
        elif success: QMessageBox.information(self,'השמירה הושלמה',f'{len(success)} פעולות בוצעו בהצלחה.')
        self.refresh_queue()
    def save_error(self,error):
        self.saving=False; self.stop.setEnabled(False); self.engine=None; self.mark_dirty(); self.status.setText('שגיאה'); QMessageBox.critical(self,'שגיאת שמירה',error)
    def refresh_queue(self,update_only=False):
        if not hasattr(self,'queue_layout'):return
        pending=list(self.model.pending); history=list(self.model.history); self.download_summary.setText(f'{len(pending)} פעולות ממתינות · {len(history)} פעולות הושלמו')
        if update_only:
            for op in pending:
                row=self.operation_rows.get(op.id)
                if row:row.update(op)
            return
        while self.queue_layout.count():
            item=self.queue_layout.takeAt(0); widget=item.widget()
            if widget:widget.deleteLater()
        self.operation_rows.clear()
        for op in pending:
            row=OperationRow(op); self.operation_rows[op.id]=row; self.queue_layout.addWidget(row)
        if history:
            head=QLabel('היסטוריה אחרונה'); head.setObjectName('Eyebrow'); self.queue_layout.addWidget(head)
            for op in reversed(history[-40:]): self.queue_layout.addWidget(OperationRow(op))
        if not pending and not history:
            empty=QLabel('אין עדיין פעולות. הוסף מדיה או בצע שינוי בספרייה.'); empty.setObjectName('Muted'); self.queue_layout.addWidget(empty)
    def undo_last(self):
        if self.saving:return
        op=self.model.undo_last()
        if op:self.status.setText('הפעולה הוסרה מהתור'); self.mark_dirty()
    def run_search(self):
        query=self.search.text().strip().casefold();
        if not query:return
        roots=[resolve(x) for x in self.config.get('roots',[])]; self.results.clear(); self.status.setText('מחפש…'); w=Worker(self.search_worker,roots,query); w.signals.result.connect(self.show_results); w.signals.error.connect(lambda e:QMessageBox.warning(self,'החיפוש נכשל',e)); self.pool.start(w)
    @staticmethod
    def search_worker(roots,query):
        result=[]
        for root in roots:
            if not root.exists():continue
            try:
                for p in root.rglob('*'):
                    if query in p.name.casefold():result.append(str(p))
                    if len(result)>=2500:return result
            except OSError:continue
        return result
    def show_results(self,results):
        self.results.clear(); [self.results.addItem(QListWidgetItem(f'{icon_for(Path(x))}  {Path(x).name}\n{Path(x).parent}')) for x in results]
        for i,x in enumerate(results):self.results.item(i).setData(Qt.UserRole,x)
        self.status.setText(f'נמצאו {len(results)} תוצאות')
    def open_result(self,it):
        p=resolve(it.data(Qt.UserRole)); self.current_folder=p if p.is_dir() else p.parent; self.navigate(0); self.render()
    def closeEvent(self,event):
        if self.saving: QMessageBox.warning(self,'שמירה בתהליך','עצור את השמירה לפני סגירת TuneVault.'); event.ignore(); return
        if self.model.pending and QMessageBox.question(self,'שינויים שלא נשמרו','יש שינויים בתור. לסגור בלי לשמור?')!=QMessageBox.Yes: event.ignore(); return
        event.accept()


STYLE = r'''
* { font-family: "Segoe UI"; }
QWidget { background:#080a0e; color:#f2f5f8; font-size:14px; }
#Sidebar { background:#0c0f14; border-right:1px solid #202630; }
#Brand { font-size:32px; font-weight:800; letter-spacing:-1px; }
#PageTitle { font-size:36px; font-weight:800; letter-spacing:-1px; }
#DialogTitle { font-size:27px; font-weight:750; }
#SectionTitle { font-size:19px; font-weight:700; }
#MediaTitle { font-size:21px; font-weight:750; }
#RowTitle { font-size:14px; font-weight:650; }
#Eyebrow { color:#76808e; font-size:11px; font-weight:800; letter-spacing:1px; }
#Muted { color:#929cab; }
#StatusPill { background:#171c23; border:1px solid #2b323c; border-radius:11px; padding:11px 13px; color:#c3c9d1; font-weight:700; }
#NavButton { background:transparent; border:0; border-radius:11px; text-align:right; padding:13px 15px; color:#abb4c0; font-weight:650; }
#NavButton:hover, #NavButton[active="true"] { background:#181e26; color:#fff; }
QLineEdit,QComboBox { background:#10151b; border:1px solid #2a313a; border-radius:11px; padding:11px 13px; selection-background-color:#2c3540; }
QLineEdit:focus,QComboBox:focus { border:1px solid #667181; }
QPushButton { background:#151a21; border:1px solid #2a3038; border-radius:11px; padding:10px 15px; font-weight:650; }
QPushButton:hover { background:#20262e; }
#Primary { background:#f3f5f7; color:#090b0f; border:0; font-weight:800; }
#Primary:hover { background:#fff; }
QListWidget { background:#0e1217; border:1px solid #252c35; border-radius:14px; padding:7px; }
QListWidget::item { padding:15px; border-radius:10px; margin:2px 0; }
QListWidget::item:hover { background:#171d25; }
QListWidget::item:selected { background:#242c36; }
#Panel,#OperationRow { background:#11161d; border:1px solid #272e37; border-radius:15px; }
#Thumbnail { background:#0a0d11; border:1px solid #292f38; border-radius:11px; color:#68727f; font-size:11px; font-weight:700; }
#MetricCard { background:#11161d; border:1px solid #252c34; border-radius:14px; padding:16px; font-size:16px; font-weight:750; }
QProgressBar { background:#0d1116; border:1px solid #252c33; border-radius:8px; height:13px; text-align:center; }
QProgressBar::chunk { border-radius:7px; }
QSplitter::handle { background:#1b222a; }
QScrollArea { background:transparent; }
QScrollBar:vertical { background:#0a0d11; width:10px; }
QScrollBar::handle:vertical { background:#303842; border-radius:5px; min-height:30px; }
'''


def main():
    app=QApplication(sys.argv); app.setApplicationName(APP_NAME); app.setLayoutDirection(Qt.RightToLeft); app.setStyleSheet(STYLE); window=MainWindow(); window.show(); sys.exit(app.exec())


if __name__=='__main__': main()
