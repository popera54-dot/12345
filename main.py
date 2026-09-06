from __future__ import annotations
import json, os, sys, urllib.request
from pathlib import Path
from PySide6.QtCore import QObject, QRunnable, QThreadPool, Qt, Signal, Slot
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import QApplication,QComboBox,QDialog,QFileDialog,QFrame,QHBoxLayout,QLabel,QLineEdit,QListWidget,QListWidgetItem,QMainWindow,QMessageBox,QPushButton,QSplitter,QStackedWidget,QVBoxLayout,QWidget,QInputDialog,QProgressBar
from tunevault_engine import ApplyEngine,LibraryModel,Operation,resolve,safe_name

APP='TuneVault'; APP_DIR=Path(os.getenv('APPDATA',str(Path.home())))/APP; APP_DIR.mkdir(parents=True,exist_ok=True); CONFIG=APP_DIR/'config.json'
def cfg():
    try:
        x=json.loads(CONFIG.read_text(encoding='utf-8')) if CONFIG.exists() else {}
        return x if isinstance(x,dict) else {'roots':[]}
    except Exception:return {'roots':[]}
def save(x):
    t=CONFIG.with_suffix('.tmp'); t.write_text(json.dumps(x,ensure_ascii=False,indent=2),encoding='utf-8'); t.replace(CONFIG)
def safe_file_name(name,old_suffix=''):
    n=safe_name(name).strip(); p=Path(n)
    if not p.suffix and old_suffix:n+=old_suffix
    return n

def icon(p):
    if p.is_dir():return '📁'
    if p.suffix.lower() in {'.mp3','.wav','.flac','.m4a','.aac','.ogg','.opus'}:return '🎵'
    if p.suffix.lower() in {'.mp4','.mkv','.webm','.mov','.avi','.m4v'}:return '🎬'
    return '📄'
class Sig(QObject):
    result=Signal(object); error=Signal(str); done=Signal()
class Work(QRunnable):
    def __init__(self,fn,*a):super().__init__();self.fn=fn;self.a=a;self.s=Sig()
    @Slot()
    def run(self):
        try:self.s.result.emit(self.fn(*self.a))
        except Exception as e:self.s.error.emit(str(e))
        finally:self.s.done.emit()

class MediaDialog(QDialog):
    def __init__(self,target,parent=None):
        super().__init__(parent);self.target=target;self.info={};self.entries=[];self.setWindowTitle('הוספת מדיה · TuneVault');self.resize(780,650);l=QVBoxLayout(self)
        h=QLabel('הוספת מדיה');h.setObjectName('DialogTitle');l.addWidget(h);s=QLabel('הדבק קישור. בחר שם ופורמט. ההורדה עצמה תתחיל רק אחרי «שמירת שינויים».');s.setObjectName('Muted');l.addWidget(s)
        r=QHBoxLayout();self.url=QLineEdit();self.url.setPlaceholderText('קישור YouTube / מדיה / פלייליסט…');b=QPushButton('זיהוי');b.setObjectName('Primary');b.clicked.connect(self.detect);r.addWidget(self.url,1);r.addWidget(b);l.addLayout(r);self.detect_btn=b
        panel=QFrame();panel.setObjectName('Panel');pv=QHBoxLayout(panel);self.thumb=QLabel('תצוגה מקדימה');self.thumb.setAlignment(Qt.AlignCenter);self.thumb.setFixedSize(210,118);self.thumb.setObjectName('Thumb');pv.addWidget(self.thumb);m=QVBoxLayout();self.title=QLabel('לא זוהתה מדיה');self.title.setObjectName('MediaTitle');self.meta=QLabel('');self.meta.setObjectName('Muted');m.addWidget(self.title);m.addWidget(self.meta);m.addStretch();pv.addLayout(m,1);l.addWidget(panel)
        self.name=QLineEdit();self.name.setPlaceholderText('שם הקובץ');self.name.setEnabled(False);self.format=QComboBox();self.format.addItems(['MP3','MP4']);self.format.setEnabled(False);l.addWidget(QLabel('שם השיר / הקובץ'));l.addWidget(self.name);fr=QHBoxLayout();fr.addWidget(QLabel('פורמט'));fr.addWidget(self.format,1);l.addLayout(fr)
        self.list=QListWidget();self.list.hide();l.addWidget(self.list,1);ar=QHBoxLayout();ar.addStretch();c=QPushButton('ביטול');ok=QPushButton('הוסף לתור');ok.setObjectName('Primary');c.clicked.connect(self.reject);ok.clicked.connect(self.accept);ar.addWidget(c);ar.addWidget(ok);l.addLayout(ar);self.ok=ok
        b.clicked.connect(self.detect)
    def detect(self):
        u=self.url.text().strip()
        if not u:return
        self.detect_btn.setEnabled(False);self.detect_btn.setText('מזהה…');w=Work(self.extract,u);w.s.result.connect(self.detected);w.s.error.connect(lambda e:QMessageBox.warning(self,'שגיאת זיהוי','לא ניתן לזהות את הקישור.\n\n'+e));w.s.done.connect(lambda:(self.detect_btn.setEnabled(True),self.detect_btn.setText('זיהוי')));QThreadPool.globalInstance().start(w)
    @staticmethod
    def extract(u):
        import yt_dlp
        with yt_dlp.YoutubeDL({'quiet':True,'no_warnings':True,'skip_download':True,'noplaylist':False}) as y:return y.extract_info(u,download=False)
    def detected(self,x):
        self.info=x or {};self.entries=[e for e in (self.info.get('entries') or []) if e];title=self.info.get('title') or 'ללא שם'; creator=self.info.get('uploader') or self.info.get('channel') or ''; self.title.setText(title);self.meta.setText((' · '.join(v for v in [creator,self.info.get('webpage_url') and 'מדיה' or ''] if v)) or 'מדיה');self.name.setText(title);self.name.setEnabled(True);self.format.setEnabled(True);self.ok.setEnabled(True)
        if len(self.entries)>1:
            self.list.show();self.list.clear();self.list.addItem(f'פלייליסט · {len(self.entries)} פריטים')
            for e in self.entries:self.list.addItem(e.get('title') or 'ללא שם')
        thumb=self.info.get('thumbnail')
        if thumb:
            w=Work(self.thumb_data,thumb);w.s.result.connect(self.show_thumb);QThreadPool.globalInstance().start(w)
    @staticmethod
    def thumb_data(u):
        with urllib.request.urlopen(u,timeout=10) as r:return r.read()
    def show_thumb(self,d):
        p=QPixmap();
        if p.loadFromData(d):self.thumb.setPixmap(p.scaled(self.thumb.size(),Qt.KeepAspectRatio,Qt.SmoothTransformation));self.thumb.setText('')
    def payloads(self):
        if len(self.entries)>1:
            return [{'url':e.get('webpage_url') or e.get('original_url'),'title':e.get('title') or 'ללא שם','format':'MP3','target':str(self.target)} for e in self.entries if e.get('webpage_url') or e.get('original_url')]
        return [{'url':self.url.text().strip(),'title':self.name.text().strip() or 'ללא שם','format':self.format.currentText(),'target':str(self.target)}]

class PlaylistDialog(QDialog):
    def __init__(self,items,parent=None):
        super().__init__(parent);self.items=items;self.setWindowTitle('אפשרויות פלייליסט');self.resize(760,560);l=QVBoxLayout(self);t=QLabel(f'פלייליסט · {len(items)} שירים');t.setObjectName('DialogTitle');l.addWidget(t);q=QHBoxLayout()
        for text,fmt in [('הכול MP3','MP3'),('הכול MP4','MP4')]:
            b=QPushButton(text);b.clicked.connect(lambda _,f=fmt:self.all(f));q.addWidget(b)
        q.addStretch();l.addLayout(q);self.rows=QListWidget();l.addWidget(self.rows,1)
        for x in items:
            it=QListWidgetItem();w=QWidget();r=QHBoxLayout(w);r.setContentsMargins(8,4,8,4);r.addWidget(QLabel(x['title']),1);c=QComboBox();c.addItems(['MP3','MP4']);c.currentTextChanged.connect(lambda v,x=x:x.__setitem__('format',v));r.addWidget(c);self.rows.addItem(it);self.rows.setItemWidget(it,w)
        a=QHBoxLayout();a.addStretch();c=QPushButton('ביטול');o=QPushButton('הוסף לתור');o.setObjectName('Primary');c.clicked.connect(self.reject);o.clicked.connect(self.accept);a.addWidget(c);a.addWidget(o);l.addLayout(a)
    def all(self,f):
        for i,x in enumerate(self.items):
            x['format']=f; w=self.rows.itemWidget(self.rows.item(i)); w.findChild(QComboBox).setCurrentText(f)

class Main(QMainWindow):
    def __init__(self):
        super().__init__();self.c=cfg();self.model=LibraryModel(self.c.get('roots',[]));self.current=None;self.pool=QThreadPool.globalInstance();self.engine=None;self.setWindowTitle('TuneVault');self.resize(1450,900);self.build();self.refresh()
    def build(self):
        root=QHBoxLayout();cen=QWidget();cen.setLayout(root);self.setCentralWidget(cen);side=QFrame();side.setObjectName('Sidebar');side.setFixedWidth(245);sl=QVBoxLayout(side);brand=QLabel('TuneVault');brand.setObjectName('Brand');sl.addWidget(brand);tag=QLabel('המדיה שלך. הסדר שלך. המחשב שלך.');tag.setObjectName('Muted');tag.setWordWrap(True);sl.addWidget(tag);sl.addSpacing(25)
        self.pages=QStackedWidget();
        for text,i in [('ספרייה',0),('הורדות',1),('חיפוש',2)]:
            b=QPushButton(text);b.setObjectName('NavButton');b.clicked.connect(lambda _,i=i:self.pages.setCurrentIndex(i));sl.addWidget(b)
        sl.addStretch();self.status=QLabel('מוכן');self.status.setObjectName('StatusPill');sl.addWidget(self.status);root.addWidget(side);root.addWidget(self.pages,1);self.pages.addWidget(self.library());self.pages.addWidget(self.downloads());self.pages.addWidget(self.search())
    def library(self):
        p=QWidget();l=QVBoxLayout(p);head=QHBoxLayout();v=QVBoxLayout();t=QLabel('ספרייה');t.setObjectName('PageTitle');v.addWidget(t);sub=QLabel('ניהול מקומי. שום שינוי בדיסק לא מתבצע לפני «שמירת שינויים».');sub.setObjectName('Muted');v.addWidget(sub);head.addLayout(v,1);self.filter=QLineEdit();self.filter.setPlaceholderText('חיפוש בתיקייה…');self.filter.textChanged.connect(self.render);head.addWidget(self.filter);a=QPushButton('+ תיקייה');a.setObjectName('Primary');a.clicked.connect(self.add_root);head.addWidget(a);l.addLayout(head);sp=QSplitter();left=QWidget();ll=QVBoxLayout(left);ll.addWidget(QLabel('מיקומים מורשים'));self.roots=QListWidget();ll.addWidget(self.roots,1);rm=QPushButton('הסר גישה');rm.clicked.connect(self.remove_root);ll.addWidget(rm);sp.addWidget(left);right=QWidget();rl=QVBoxLayout(right);bar=QHBoxLayout();self.path=QLabel('בחר תיקייה');self.path.setObjectName('SectionTitle');bar.addWidget(self.path,1);up=QPushButton('↑ אחורה');up.clicked.connect(self.up);rf=QPushButton('↻ רענן');rf.clicked.connect(self.render);bar.addWidget(up);bar.addWidget(rf);rl.addLayout(bar);self.items=QListWidget();self.items.itemDoubleClicked.connect(self.open);rl.addWidget(self.items,1);acts=QHBoxLayout();
        for txt,fn in [('+ תיקייה',self.mkdir),('+ הוסף שיר',self.media),('שנה שם',self.rename),('העבר',self.move),('מחק',self.delete)]:b=QPushButton(txt);b.clicked.connect(fn);acts.addWidget(b)
        acts.addStretch();self.saveb=QPushButton('שמירת שינויים');self.saveb.setObjectName('Primary');self.saveb.clicked.connect(self.save_changes);acts.addWidget(self.saveb);rl.addLayout(acts);sp.addWidget(right);sp.setSizes([320,1000]);l.addWidget(sp,1);self.roots.currentItemChanged.connect(lambda x,_:self.select_root(x));return p
    def downloads(self):
        p=QWidget();l=QVBoxLayout(p);t=QLabel('הורדות');t.setObjectName('PageTitle');l.addWidget(t);s=QLabel('תור ההורדות ופעולות אחרונות');s.setObjectName('Muted');l.addWidget(s);self.progress=QProgressBar();self.progress.setTextVisible(True);self.progress.setValue(0);l.addWidget(self.progress);self.queue=QListWidget();l.addWidget(self.queue,1);r=QHBoxLayout();u=QPushButton('בטל שינוי אחרון');u.clicked.connect(self.undo);r.addWidget(u);r.addStretch();l.addLayout(r);return p
    def search(self):
        p=QWidget();l=QVBoxLayout(p);t=QLabel('חיפוש');t.setObjectName('PageTitle');l.addWidget(t);self.g=QLineEdit();self.g.setPlaceholderText('חפש בכל הספריות…');b=QPushButton('חיפוש');b.setObjectName('Primary');b.clicked.connect(self.search_go);r=QHBoxLayout();r.addWidget(self.g,1);r.addWidget(b);l.addLayout(r);self.results=QListWidget();self.results.itemDoubleClicked.connect(self.search_open);l.addWidget(self.results,1);return p
    def refresh(self):
        self.roots.clear();valid=[]
        for x in self.c.get('roots',[]):
            p=resolve(x)
            if p.exists() and p.is_dir():valid.append(str(p));it=QListWidgetItem('📁 '+(p.name or str(p)));it.setData(Qt.UserRole,str(p));self.roots.addItem(it)
        self.c['roots']=valid;save(self.c);self.model.roots=valid
        if self.roots.count():self.roots.setCurrentRow(0)
        else:self.current=None;self.path.setText('הוסף תיקייה כדי להתחיל');self.items.clear()
    def add_root(self):
        x=QFileDialog.getExistingDirectory(self,'בחר תיקייה');
        if not x:return
        p=resolve(x);rs=[resolve(z) for z in self.c.get('roots',[])]
        if any(p==r or p.is_relative_to(r) or r.is_relative_to(p) for r in rs):QMessageBox.information(self,'כבר קיימת','התיקייה כבר נמצאת בתוך אזור מורשה.');return
        self.c['roots'].append(str(p));save(self.c);self.model.roots=self.c['roots'];self.refresh()
    def remove_root(self):
        it=self.roots.currentItem();
        if not it:return
        p=resolve(it.data(Qt.UserRole))
        if QMessageBox.question(self,'הסר גישה',f'להסיר את {p.name} מהספרייה?\nהקבצים לא יימחקו.')==QMessageBox.Yes:self.c['roots']=[x for x in self.c['roots'] if resolve(x)!=p];save(self.c);self.refresh()
    def select_root(self,it):
        if it:self.current=resolve(it.data(Qt.UserRole));self.render()
    def render(self,*_):
        if not self.current:return
        self.path.setText(str(self.current));self.items.clear();q=self.filter.text().lower().strip()
        for p,state in self.model.virtual_entries(self.current):
            if q and q not in p.name.lower():continue
            mark='  • בהמתנה' if state=='pending' else ''
            it=QListWidgetItem(f'{icon(p)}  {p.name}{mark}');it.setData(Qt.UserRole,str(p));self.items.addItem(it)
        self.activity()
    def open(self,it):
        p=resolve(it.data(Qt.UserRole));
        if p.is_dir():self.current=p;self.render()
    def up(self):
        if self.current and self.current not in [resolve(x) for x in self.c['roots']]:self.current=self.current.parent;self.render()
    def mkdir(self):
        if not self.current:return
        n,ok=QInputDialog.getText(self,'תיקייה חדשה','שם התיקייה:');
        if ok and n:self.model.stage(Operation('mkdir',f'יצירת תיקייה · {n}',path=str(self.current/safe_name(n))));self.dirty()
    def media(self):
        if not self.current:return
        d=MediaDialog(self.current,self)
        if d.exec()!=QDialog.Accepted:return
        ps=d.payloads()
        if len(ps)>1:
            q=PlaylistDialog(ps,self)
            if q.exec()!=QDialog.Accepted:return
        for x in ps:self.model.stage(Operation('download',f'הורדה · {x["title"]} [{x["format"]}]',payload=x))
        self.dirty();self.pages.setCurrentIndex(1);self.render()
    def selected(self):
        i=self.items.currentItem();return resolve(i.data(Qt.UserRole)) if i else None
    def rename(self):
        old=self.selected();
        if not old:return
        n,ok=QInputDialog.getText(self,'שינוי שם','שם חדש:',text=old.stem)
        if not ok:return
        new=old.parent/safe_file_name(n,old.suffix)
        if new==old:return
        if any(resolve(p)==new for p,_ in self.model.virtual_entries(old.parent)):QMessageBox.warning(self,'שם קיים','כבר קיים פריט בשם הזה.');return
        self.model.stage(Operation('rename',f'שינוי שם · {old.name} → {new.name}',old=str(old),new=str(new)));self.dirty()
    def move(self):
        old=self.selected();
        if not old:return
        d=QFileDialog.getExistingDirectory(self,'בחר יעד',str(old.parent));
        if not d:return
        new=resolve(d)/old.name
        if not self.model.allowed(new):QMessageBox.warning(self,'פעולה חסומה','היעד חייב להיות בתוך תיקייה מורשית.');return
        self.model.stage(Operation('move',f'העברה · {old.name}',old=str(old),new=str(new)));self.dirty()
    def delete(self):
        p=self.selected();
        if p and QMessageBox.question(self,'מחיקה','לסמן למחיקה?\nהקובץ לא יימחק עד שמירת שינויים.')==QMessageBox.Yes:self.model.stage(Operation('delete',f'מחיקה · {p.name}',path=str(p)));self.dirty()
    def dirty(self):
        self.status.setText(f'יש {len(self.model.pending)} שינויים ממתינים');self.saveb.setText(f'שמירת שינויים ({len(self.model.pending)})');self.saveb.setEnabled(True);self.render();self.activity()
    def save_changes(self):
        if not self.model.pending:return
        self.saveb.setEnabled(False);self.saveb.setText('מבצע שינויים…');self.progress.setValue(0);self.engine=ApplyEngine(self.model);w=Work(self.engine.apply);w.s.result.connect(self.saved);w.s.error.connect(lambda e:QMessageBox.critical(self,'שגיאה',e));w.s.done.connect(lambda:None);self.pool.start(w)
    def saved(self,res):
        for op in res:
            if op.status=='success':self.model.history.append(op)
        self.model.pending=[x for x in self.model.pending if x.status not in {'success','cancelled'}];failed=[x for x in res if x.status=='failed']
        self.saveb.setEnabled(bool(self.model.pending));self.saveb.setText('שמירת שינויים' if not self.model.pending else f'שמירת שינויים ({len(self.model.pending)})');self.status.setText('נשמר בהצלחה' if not failed else f'{len(failed)} פעולות נכשלו');self.progress.setValue(100 if not failed else 50);self.render();self.activity()
        if failed:QMessageBox.warning(self,'חלק מההורדות נכשלו','הפעולות שנכשלו נשארו בתור כדי שאפשר יהיה לנסות שוב.\n\n'+ '\n'.join(x.error for x in failed[:5]))
    def undo(self):
        if self.model.undo_last():self.dirty()
    def activity(self):
        if not hasattr(self,'queue'):return
        self.queue.clear()
        for x in self.model.pending:self.queue.addItem(('🔴 ' if x.status=='failed' else '🟡 ')+x.label+(f' · {x.error}' if x.error else ''))
        for x in reversed(self.model.history[-100:]):self.queue.addItem('🟢 '+x.label)
    def search_go(self):
        q=self.g.text().strip().lower();self.results.clear()
        if not q:return
        roots=[resolve(x) for x in self.c['roots']];w=Work(self.do_search,roots,q);w.s.result.connect(self.show_search);self.pool.start(w)
    @staticmethod
    def do_search(roots,q):
        out=[]
        for r in roots:
            try:
                for p in r.rglob('*'):
                    if q in p.name.lower():out.append(str(p))
                    if len(out)>=2000:return out
            except OSError:pass
        return out
    def show_search(self,arr):
        for x in arr:
            p=Path(x);it=QListWidgetItem(f'{icon(p)}  {p.name}  ·  {p.parent}');it.setData(Qt.UserRole,x);self.results.addItem(it)
    def search_open(self,it):
        p=resolve(it.data(Qt.UserRole));self.current=p if p.is_dir() else p.parent;self.pages.setCurrentIndex(0);self.render()
    def closeEvent(self,e):
        if self.model.pending and QMessageBox.question(self,'יש שינויים','יש שינויים שלא נשמרו. לצאת בלי לשמור?')!=QMessageBox.Yes:e.ignore();return
        e.accept()

STYLE='''*{font-family:"Segoe UI";} QWidget{background:#0a0d11;color:#eef2f6;font-size:14px;} #Sidebar{background:#0f1319;border-left:1px solid #252b34;} #Brand{font-size:31px;font-weight:800;} #PageTitle{font-size:35px;font-weight:800;} #DialogTitle{font-size:25px;font-weight:750;} #MediaTitle,#SectionTitle{font-size:19px;font-weight:700;} #Muted{color:#8993a0;} #StatusPill{background:#171d25;border:1px solid #2a323d;border-radius:10px;padding:10px;font-weight:700;} #NavButton{background:transparent;border:0;text-align:right;padding:13px;border-radius:10px;color:#aeb8c4;} #NavButton:hover{background:#1a2028;color:#fff;} QLineEdit,QComboBox{background:#11161d;border:1px solid #2b333e;border-radius:10px;padding:11px;} QPushButton{background:#161c23;border:1px solid #2b333e;border-radius:10px;padding:10px 15px;font-weight:650;} QPushButton:hover{background:#202730;} #Primary{background:#f2f5f8;color:#090b0e;border:0;} QListWidget{background:#10151b;border:1px solid #252c35;border-radius:13px;padding:7px;} QListWidget::item{padding:13px;border-radius:9px;} QListWidget::item:hover{background:#191f27;} QListWidget::item:selected{background:#242c36;} #Panel{background:#11171e;border:1px solid #29313b;border-radius:14px;} #Thumb{background:#0b0e12;border:1px solid #2a323c;border-radius:10px;color:#687381;} QProgressBar{background:#151b22;border:0;border-radius:7px;height:12px;text-align:center;} QProgressBar::chunk{background:#f2f5f8;border-radius:7px;}'''
def main():
    app=QApplication(sys.argv);app.setApplicationName(APP);app.setStyleSheet(STYLE);app.setLayoutDirection(Qt.RightToLeft);w=Main();w.show();sys.exit(app.exec())
if __name__=='__main__':main()
