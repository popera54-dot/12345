from __future__ import annotations

import shutil
import threading
import uuid
from dataclasses import dataclass, field
from pathlib import Path

MEDIA_EXTS={'.mp3','.wav','.flac','.m4a','.aac','.ogg','.opus','.mp4','.mkv','.webm','.mov','.avi','.m4v'}

def resolve(path):
    try:return Path(path).resolve()
    except OSError:return Path(path).absolute()

def under(path,root):
    try:resolve(path).relative_to(resolve(root));return True
    except ValueError:return False

def safe_name(value):
    invalid='<>:/\\|?*"'
    text=''.join('-' if c in invalid or ord(c)<32 else c for c in str(value)).strip().rstrip('.')
    return text or 'ללא שם'

@dataclass
class Operation:
    kind:str
    label:str
    old:str=''
    new:str=''
    path:str=''
    payload:dict=field(default_factory=dict)
    id:str=field(default_factory=lambda:uuid.uuid4().hex)
    status:str='pending'
    error:str=''
    progress:int=0

class LibraryModel:
    def __init__(self,roots=None):
        self.roots=[str(resolve(r)) for r in (roots or [])];self.pending=[];self.history=[];self._lock=threading.RLock()
    def allowed(self,path):
        p=resolve(path);return any(p==resolve(r) or under(p,resolve(r)) for r in self.roots)
    def stage(self,op):
        targets=[op.path,op.old,op.new,op.payload.get('target','')]
        if any(targets) and not any(self.allowed(x) for x in targets if x):raise PermissionError('הפעולה מחוץ לתיקייה שהורשתה')
        with self._lock:self.pending.append(op)
        return op
    def undo_last(self):
        with self._lock:return self.pending.pop() if self.pending else None
    def virtual_entries(self,folder):
        folder=resolve(folder);visible={}
        if folder.exists():
            try:
                for p in folder.iterdir():visible[str(p)]=(p,'')
            except OSError:pass
        for op in self.pending:
            if op.kind=='mkdir':
                p=resolve(op.path)
                if p.parent==folder:visible[str(p)]=(p,'pending')
            elif op.kind=='delete':visible.pop(str(resolve(op.path)),None)
            elif op.kind in {'rename','move'}:
                old=resolve(op.old);new=resolve(op.new or op.path);visible.pop(str(old),None)
                if new.parent==folder:visible[str(new)]=(new,'pending')
            elif op.kind=='download':
                p=resolve(op.payload['target'])/(safe_name(op.payload.get('title') or 'ללא שם')+('.mp3' if op.payload.get('format')=='MP3' else '.mp4'))
                if p.parent==folder:visible[str(p)]=(p,'pending')
        return sorted(visible.values(),key=lambda x:(not x[0].is_dir(),x[0].name.casefold()))

class ApplyEngine:
    def __init__(self,model):self.model=model;self.cancel_event=threading.Event()
    def cancel(self):self.cancel_event.set()
    def apply(self,progress=None):
        with self.model._lock:batch=list(self.model.pending)
        results=[]
        for i,op in enumerate(batch,1):
            if self.cancel_event.is_set():op.status='cancelled';op.error='הפעולה בוטלה';results.append(op);continue
            try:self._one(op,progress);op.status='success';op.error='';op.progress=100
            except Exception as e:op.status='failed';op.error=str(e)
            results.append(op)
            if progress:progress(op,i,len(batch))
        return results
    def _one(self,op,progress=None):
        if op.kind=='mkdir':Path(op.path).mkdir(parents=False,exist_ok=False)
        elif op.kind=='rename':Path(op.old).rename(op.new)
        elif op.kind=='move':
            dest=Path(op.new or op.path);dest.parent.mkdir(parents=True,exist_ok=True);Path(op.old).rename(dest)
        elif op.kind=='delete':
            p=Path(op.path);shutil.rmtree(p) if p.is_dir() else p.unlink()
        elif op.kind=='download':self._download(op.payload,lambda x:setattr(op,'progress',x))
        else:raise ValueError('סוג פעולה לא מוכר')
    @staticmethod
    def _download(payload,report=None):
        import imageio_ffmpeg,yt_dlp
        target=resolve(payload['target']);target.mkdir(parents=True,exist_ok=True)
        title=safe_name(payload.get('title') or 'ללא שם');fmt=(payload.get('format') or 'MP3').upper()
        ext='.mp3' if fmt=='MP3' else '.mp4';final=target/(title+ext)
        temp=target/'.tunevault_tmp';temp.mkdir(parents=True,exist_ok=True)
        ffmpeg=imageio_ffmpeg.get_ffmpeg_exe();opts={'outtmpl':str(temp/'%(id)s.%(ext)s'),'ffmpeg_location':ffmpeg,'noplaylist':True,'quiet':True,'no_warnings':True,'retries':5,'fragment_retries':5,'continuedl':True,'windowsfilenames':False,'overwrites':True}
        if report:
            def hook(d):
                if d.get('status')=='downloading':
                    total=d.get('total_bytes') or d.get('total_bytes_estimate');done=d.get('downloaded_bytes',0)
                    if total:report(min(95,max(0,int(done*95/total))))
                elif d.get('status')=='finished':report(96)
            opts['progress_hooks']=[hook]
        if fmt=='MP3':opts.update({'format':'bestaudio/best','postprocessors':[{'key':'FFmpegExtractAudio','preferredcodec':'mp3','preferredquality':'192'}]})
        else:opts.update({'format':'bestvideo+bestaudio/best','merge_output_format':'mp4'})
        with yt_dlp.YoutubeDL(opts) as ydl:
            info=ydl.extract_info(payload['url'],download=True);requested=Path(ydl.prepare_filename(info))
            candidates=[p for p in [requested,*temp.glob(requested.stem+'.*'),*temp.glob('*')] if p.is_file()]
        source=next((p for p in candidates if p.suffix.lower() in MEDIA_EXTS),None)
        if not source:raise FileNotFoundError('לא נמצא קובץ מדיה תקין לאחר ההורדה')
        if final.exists():final.unlink()
        source.replace(final)
        try:
            for p in temp.iterdir():
                if p.is_file():p.unlink(missing_ok=True)
            temp.rmdir()
        except OSError:pass
        if report:report(100)
