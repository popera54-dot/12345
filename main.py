from __future__ import annotations

import json
import os
import sys
import urllib.request
from pathlib import Path

from PySide6.QtCore import QObject, QRunnable, QThreadPool, Qt, Signal, Slot
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QDialog,
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QProgressBar,
    QScrollArea,
    QSplitter,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from tunevault_engine import ApplyEngine, LibraryModel, MediaPayload, Operation, resolve, safe_name

APP_NAME = 'TuneVault'
APP_DIR = Path(os.getenv('APPDATA', str(Path.home()))) / APP_NAME
APP_DIR.mkdir(parents=True, exist_ok=True)
CONFIG_FILE = APP_DIR / 'config.json'


# ---------------------------------------------------------------------------
# persistence
# ---------------------------------------------------------------------------

def load_config() -> dict:
    try:
        data = json.loads(CONFIG_FILE.read_text(encoding='utf-8')) if CONFIG_FILE.exists() else {}
        return data if isinstance(data, dict) else {'roots': []}
    except (OSError, json.JSONDecodeError):
        return {'roots': []}


def save_config(data: dict) -> None:
    temp = CONFIG_FILE.with_suffix('.tmp')
    temp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
    temp.replace(CONFIG_FILE)


def media_icon(path: Path) -> str:
    if path.is_dir():
        return '📁'
    if path.suffix.lower() in {'.mp3', '.wav', '.flac', '.m4a', '.aac', '.ogg', '.opus'}:
        return '🎵'
    if path.suffix.lower() in {'.mp4', '.mkv', '.webm', '.mov', '.avi', '.m4v'}:
        return '🎬'
    return '📄'


def format_duration(seconds) -> str:
    if not seconds:
        return ''
    total = max(0, int(seconds))
    minutes, seconds = divmod(total, 60)
    hours, minutes = divmod(minutes, 60)
    return f'{hours}:{minutes:02d}:{seconds:02d}' if hours else f'{minutes}:{seconds:02d}'


# ---------------------------------------------------------------------------
# worker infrastructure
# ---------------------------------------------------------------------------

class WorkerSignals(QObject):
    result = Signal(object)
    error = Signal(str)
    done = Signal()
    progress = Signal(object)


class Worker(QRunnable):
    def __init__(self, function, *args, **kwargs):
        super().__init__()
        self.function = function
        self.args = args
        self.kwargs = kwargs
        self.signals = WorkerSignals()

    @Slot()
    def run(self):
        try:
            self.signals.result.emit(self.function(*self.args, **self.kwargs))
        except Exception as exc:
            self.signals.error.emit(str(exc))
        finally:
            self.signals.done.emit()


# ---------------------------------------------------------------------------
# media dialog
# ---------------------------------------------------------------------------

class MediaDialog(QDialog):
    def __init__(self, target: Path, parent=None):
        super().__init__(parent)
        self.target = resolve(target)
        self.info = {}
        self.entries = []
        self.setWindowTitle('הוספת מדיה')
        self.resize(860, 680)
        self._build()

    def _build(self):
        layout = QVBoxLayout(self)
        title = QLabel('הוספת מדיה')
        title.setObjectName('DialogTitle')
        subtitle = QLabel('הדבק קישור. TuneVault יזהה את המדיה, ותוכל לבחור שם ופורמט לפני שהפעולה נכנסת לתור.')
        subtitle.setObjectName('Muted')
        subtitle.setWordWrap(True)
        layout.addWidget(title)
        layout.addWidget(subtitle)

        url_row = QHBoxLayout()
        self.url = QLineEdit()
        self.url.setPlaceholderText('קישור למדיה או פלייליסט…')
        self.detect_button = QPushButton('זיהוי')
        self.detect_button.setObjectName('Primary')
        self.detect_button.clicked.connect(self.detect)
        url_row.addWidget(self.url, 1)
        url_row.addWidget(self.detect_button)
        layout.addLayout(url_row)

        preview = QFrame()
        preview.setObjectName('Panel')
        pv = QHBoxLayout(preview)
        self.thumb = QLabel('תצוגה מקדימה')
        self.thumb.setObjectName('Thumbnail')
        self.thumb.setAlignment(Qt.AlignCenter)
        self.thumb.setFixedSize(240, 135)
        pv.addWidget(self.thumb)
        metadata = QVBoxLayout()
        self.media_title = QLabel('לא זוהתה מדיה')
        self.media_title.setObjectName('MediaTitle')
        self.media_meta = QLabel('הדבק קישור ולחץ על «זיהוי».')
        self.media_meta.setObjectName('Muted')
        self.media_meta.setWordWrap(True)
        metadata.addWidget(self.media_title)
        metadata.addWidget(self.media_meta)
        metadata.addStretch()
        pv.addLayout(metadata, 1)
        layout.addWidget(preview)

        self.single_box = QFrame()
        single = QVBoxLayout(self.single_box)
        name_label = QLabel('שם הקובץ')
        self.name = QLineEdit()
        self.name.setPlaceholderText('שם שיר / קובץ')
        self.name.setEnabled(False)
        single.addWidget(name_label)
        single.addWidget(self.name)
        format_row = QHBoxLayout()
        format_row.addWidget(QLabel('פורמט'))
        self.format = QComboBox()
        self.format.addItems(['MP3', 'MP4'])
        self.format.setEnabled(False)
        format_row.addWidget(self.format, 1)
        single.addLayout(format_row)
        layout.addWidget(self.single_box)

        self.playlist_box = QFrame()
        playlist_layout = QVBoxLayout(self.playlist_box)
        self.playlist_header = QLabel('')
        self.playlist_header.setObjectName('SectionTitle')
        playlist_layout.addWidget(self.playlist_header)
        self.playlist_list = QListWidget()
        playlist_layout.addWidget(self.playlist_list, 1)
        self.playlist_box.hide()
        layout.addWidget(self.playlist_box, 1)

        buttons = QHBoxLayout()
        buttons.addStretch()
        cancel = QPushButton('ביטול')
        cancel.clicked.connect(self.reject)
        self.add_button = QPushButton('הוספה לתור')
        self.add_button.setObjectName('Primary')
        self.add_button.setEnabled(False)
        self.add_button.clicked.connect(self.accept)
        buttons.addWidget(cancel)
        buttons.addWidget(self.add_button)
        layout.addLayout(buttons)

    @staticmethod
    def extract(url: str):
        import yt_dlp
        options = {'quiet': True, 'no_warnings': True, 'skip_download': True, 'noplaylist': False}
        with yt_dlp.YoutubeDL(options) as downloader:
            return downloader.extract_info(url, download=False)

    def detect(self):
        url = self.url.text().strip()
        if not url:
            return
        self.detect_button.setEnabled(False)
        self.detect_button.setText('מזהה…')
        worker = Worker(self.extract, url)
        worker.signals.result.connect(self.detected)
        worker.signals.error.connect(lambda e: QMessageBox.warning(self, 'הזיהוי נכשל', e))
        worker.signals.done.connect(lambda: (self.detect_button.setEnabled(True), self.detect_button.setText('זיהוי')))
        QThreadPool.globalInstance().start(worker)

    def detected(self, info):
        self.info = info or {}
        self.entries = [entry for entry in (self.info.get('entries') or []) if entry]
        title = self.info.get('title') or 'ללא שם'
        uploader = self.info.get('uploader') or self.info.get('channel') or ''
        duration = format_duration(self.info.get('duration'))
        meta = ' · '.join(part for part in (uploader, duration) if part)
        if self.entries:
            meta = (meta + ' · ' if meta else '') + f'פלייליסט · {len(self.entries)} פריטים'
        self.media_title.setText(title)
        self.media_meta.setText(meta or 'מדיה')
        self.name.setText(title)
        self.name.setEnabled(True)
        self.format.setEnabled(True)
        self.add_button.setEnabled(True)

        if self.entries:
            self.single_box.hide()
            self.playlist_box.show()
            self.playlist_header.setText(f'נמצאו {len(self.entries)} פריטים')
            self.playlist_list.clear()
            for entry in self.entries:
                self.playlist_list.addItem(entry.get('title') or 'ללא שם')
        else:
            self.single_box.show()
            self.playlist_box.hide()

        thumbnail_url = self.info.get('thumbnail')
        if thumbnail_url:
            worker = Worker(self.fetch_thumbnail, thumbnail_url)
            worker.signals.result.connect(self.show_thumbnail)
            QThreadPool.globalInstance().start(worker)

    @staticmethod
    def fetch_thumbnail(url: str) -> bytes:
        request = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(request, timeout=10) as response:
            return response.read()

    def show_thumbnail(self, data: bytes):
        pixmap = QPixmap()
        if pixmap.loadFromData(data):
            self.thumb.setText('')
            self.thumb.setPixmap(pixmap.scaled(self.thumb.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation))

    def payloads(self) -> list[MediaPayload]:
        if self.entries:
            return [
                MediaPayload(
                    url=entry.get('webpage_url') or entry.get('original_url') or '',
                    title=entry.get('title') or 'ללא שם',
                    target=str(self.target),
                    format='MP3',
                )
                for entry in self.entries
                if entry.get('webpage_url') or entry.get('original_url')
            ]
        return [
            MediaPayload(
                url=self.url.text().strip(),
                title=self.name.text().strip() or 'ללא שם',
                target=str(self.target),
                format=self.format.currentText(),
            )
        ]


# ---------------------------------------------------------------------------
# playlist editor
# ---------------------------------------------------------------------------

class PlaylistDialog(QDialog):
    def __init__(self, payloads: list[MediaPayload], parent=None):
        super().__init__(parent)
        self.payloads_data = payloads
        self.setWindowTitle('עריכת פלייליסט')
        self.resize(900, 640)
        layout = QVBoxLayout(self)

        title = QLabel(f'עריכת פלייליסט · {len(payloads)} שירים')
        title.setObjectName('DialogTitle')
        layout.addWidget(title)
        info = QLabel('כל שורה ניתנת לעריכה. השינויים נשארים בתור עד «שמירת שינויים».')
        info.setObjectName('Muted')
        layout.addWidget(info)

        quick = QHBoxLayout()
        for label, fmt in [('הכול MP3', 'MP3'), ('הכול MP4', 'MP4')]:
            button = QPushButton(label)
            button.clicked.connect(lambda _, f=fmt: self.set_all(f))
            quick.addWidget(button)
        quick.addStretch()
        layout.addLayout(quick)

        self.rows = QListWidget()
        layout.addWidget(self.rows, 1)

        for payload in payloads:
            item = QListWidgetItem()
            widget = QWidget()
            row = QHBoxLayout(widget)
            row.setContentsMargins(12, 8, 12, 8)
            editor = QLineEdit(payload.title)
            editor.setPlaceholderText('שם הקובץ')
            editor.textChanged.connect(lambda value, p=payload: setattr(p, 'title', value or 'ללא שם'))
            combo = QComboBox()
            combo.addItems(['MP3', 'MP4'])
            combo.currentTextChanged.connect(lambda value, p=payload: setattr(p, 'format', value))
            row.addWidget(editor, 2)
            row.addWidget(combo)
            self.rows.addItem(item)
            self.rows.setItemWidget(item, widget)

        actions = QHBoxLayout()
        actions.addStretch()
        cancel = QPushButton('ביטול')
        cancel.clicked.connect(self.reject)
        done = QPushButton('הוספה לתור')
        done.setObjectName('Primary')
        done.clicked.connect(self.accept)
        actions.addWidget(cancel)
        actions.addWidget(done)
        layout.addLayout(actions)

    def set_all(self, fmt: str):
        for index, payload in enumerate(self.payloads_data):
            payload.format = fmt
            widget = self.rows.itemWidget(self.rows.item(index))
            combo = widget.findChild(QComboBox) if widget else None
            if combo:
                combo.setCurrentText(fmt)


# ---------------------------------------------------------------------------
# operation row
# ---------------------------------------------------------------------------

class OperationRow(QFrame):
    def __init__(self, operation: Operation):
        super().__init__()
        self.operation = operation
        self.setObjectName('OperationRow')
        outer = QVBoxLayout(self)
        outer.setContentsMargins(16, 14, 16, 14)
        top = QHBoxLayout()
        self.state = QLabel('🟡')
        self.state.setFixedWidth(24)
        self.title = QLabel(operation.label)
        self.title.setObjectName('RowTitle')
        self.title.setWordWrap(True)
        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setValue(operation.progress)
        self.progress.setFixedWidth(180)
        self.percent = QLabel(f'{operation.progress}%')
        self.percent.setObjectName('Muted')
        top.addWidget(self.state)
        top.addWidget(self.title, 1)
        top.addWidget(self.percent)
        outer.addLayout(top)
        outer.addWidget(self.progress)
        self.update_state()

    def update_operation(self, operation: Operation):
        self.operation = operation
        self.progress.setValue(operation.progress)
        self.percent.setText(f'{operation.progress}%')
        self.title.setText(operation.label + (f'  ·  {operation.error}' if operation.error else ''))
        self.update_state()

    def update_state(self):
        status = self.operation.status
        self.state.setText({'pending': '🟡', 'running': '🔵', 'success': '🟢', 'failed': '🔴', 'cancelled': '⚪'}.get(status, '🟡'))
        if status == 'success':
            self.progress.setVisible(False)
            self.percent.setVisible(False)
        else:
            self.progress.setVisible(True)
            self.percent.setVisible(True)


# ---------------------------------------------------------------------------
# main window
# ---------------------------------------------------------------------------

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.config = load_config()
        self.model = LibraryModel(self.config.get('roots', []))
        self.current_folder: Path | None = None
        self.pool = QThreadPool.globalInstance()
        self.engine: ApplyEngine | None = None
        self.saving = False
        self.operation_rows: dict[str, OperationRow] = {}
        self.setWindowTitle(APP_NAME)
        self.resize(1500, 940)
        self.setMinimumSize(1180, 760)
        self._build()
        self.refresh_roots()

    # ---------------------------------------------------------
    # layout
    # ---------------------------------------------------------

    def _build(self):
        central = QWidget()
        main = QHBoxLayout(central)
        main.setContentsMargins(0, 0, 0, 0)
        main.setSpacing(0)
        self.setCentralWidget(central)

        sidebar = QFrame()
        sidebar.setObjectName('Sidebar')
        sidebar.setFixedWidth(270)
        sl = QVBoxLayout(sidebar)
        sl.setContentsMargins(22, 28, 22, 24)

        brand = QLabel('TuneVault')
        brand.setObjectName('Brand')
        sl.addWidget(brand)
        tagline = QLabel('המדיה שלך.\nהסדר שלך.\nהמחשב שלך.')
        tagline.setObjectName('Muted')
        tagline.setWordWrap(True)
        sl.addWidget(tagline)
        sl.addSpacing(30)

        self.nav_buttons = []
        for text, index in [('ספרייה', 0), ('הורדות', 1), ('חיפוש', 2)]:
            button = QPushButton(text)
            button.setObjectName('NavButton')
            button.clicked.connect(lambda _, i=index: self.navigate(i))
            sl.addWidget(button)
            self.nav_buttons.append(button)

        sl.addStretch()
        self.status_pill = QLabel('מוכן')
        self.status_pill.setObjectName('StatusPill')
        sl.addWidget(self.status_pill)
        main.addWidget(sidebar)

        self.pages = QStackedWidget()
        main.addWidget(self.pages, 1)
        self.pages.addWidget(self.library_page())
        self.pages.addWidget(self.downloads_page())
        self.pages.addWidget(self.search_page())

    def navigate(self, index: int):
        self.pages.setCurrentIndex(index)
        for position, button in enumerate(self.nav_buttons):
            button.setProperty('active', position == index)
            button.style().unpolish(button)
            button.style().polish(button)

    # ---------------------------------------------------------
    # library page
    # ---------------------------------------------------------

    def library_page(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(34, 30, 34, 28)

        header = QHBoxLayout()
        heading = QVBoxLayout()
        title = QLabel('הספרייה שלך')
        title.setObjectName('PageTitle')
        subtitle = QLabel('כל השינויים נבנים מראש ונכנסים למחשב רק אחרי שמירה.')
        subtitle.setObjectName('Muted')
        heading.addWidget(title)
        heading.addWidget(subtitle)
        header.addLayout(heading, 1)

        self.folder_filter = QLineEdit()
        self.folder_filter.setPlaceholderText('חיפוש בתיקייה…')
        self.folder_filter.textChanged.connect(self.render_folder)
        header.addWidget(self.folder_filter)

        add_root = QPushButton('+ הוסף ספרייה')
        add_root.setObjectName('Primary')
        add_root.clicked.connect(self.add_library)
        header.addWidget(add_root)
        layout.addLayout(header)

        cards = QHBoxLayout()
        self.card_location = QLabel('0\nמיקומים')
        self.card_pending = QLabel('0\nממתינים')
        self.card_media = QLabel('0\nפריטים')
        for card in [self.card_location, self.card_pending, self.card_media]:
            card.setObjectName('MetricCard')
            cards.addWidget(card)
        layout.addLayout(cards)

        splitter = QSplitter(Qt.Horizontal)

        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 14, 0, 0)
        label = QLabel('מיקומים מורשים')
        label.setObjectName('Eyebrow')
        left_layout.addWidget(label)
        self.roots_list = QListWidget()
        left_layout.addWidget(self.roots_list, 1)
        remove = QPushButton('הסר גישה')
        remove.clicked.connect(self.remove_library)
        left_layout.addWidget(remove)
        splitter.addWidget(left)

        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(18, 14, 0, 0)

        path_row = QHBoxLayout()
        self.path_label = QLabel('בחר ספרייה')
        self.path_label.setObjectName('SectionTitle')
        path_row.addWidget(self.path_label, 1)
        self.back_button = QPushButton('↑ אחורה')
        self.back_button.clicked.connect(self.go_up)
        refresh = QPushButton('↻ רענן')
        refresh.clicked.connect(self.render_folder)
        path_row.addWidget(self.back_button)
        path_row.addWidget(refresh)
        right_layout.addLayout(path_row)

        self.items_list = QListWidget()
        self.items_list.itemDoubleClicked.connect(self.open_item)
        right_layout.addWidget(self.items_list, 1)

        actions = QHBoxLayout()
        for text, callback in [
            ('+ תיקייה', self.stage_folder),
            ('+ הוסף מדיה', self.stage_media),
            ('שנה שם', self.rename_selected),
            ('העבר', self.move_selected),
            ('מחק', self.delete_selected),
        ]:
            button = QPushButton(text)
            button.clicked.connect(callback)
            actions.addWidget(button)
        actions.addStretch()
        self.save_button = QPushButton('שמירת שינויים')
        self.save_button.setObjectName('Primary')
        self.save_button.setEnabled(False)
        self.save_button.clicked.connect(self.save_changes)
        actions.addWidget(self.save_button)
        right_layout.addLayout(actions)
        splitter.addWidget(right)
        splitter.setSizes([320, 1100])
        layout.addWidget(splitter, 1)

        self.roots_list.currentItemChanged.connect(self.select_root)
        return page

    # ---------------------------------------------------------
    # downloads page
    # ---------------------------------------------------------

    def downloads_page(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(34, 30, 34, 28)
        title = QLabel('הורדות')
        title.setObjectName('PageTitle')
        layout.addWidget(title)
        subtitle = QLabel('כאן רואים בדיוק מה ממתין, מה מתבצע ומה קרה בפועל.')
        subtitle.setObjectName('Muted')
        layout.addWidget(subtitle)
        self.downloads_summary = QLabel('אין פעולות כרגע')
        self.downloads_summary.setObjectName('SectionTitle')
        layout.addWidget(self.downloads_summary)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        container = QWidget()
        self.queue_layout = QVBoxLayout(container)
        self.queue_layout.setAlignment(Qt.AlignTop)
        scroll.setWidget(container)
        layout.addWidget(scroll, 1)
        actions = QHBoxLayout()
        undo = QPushButton('בטל פעולה אחרונה')
        undo.clicked.connect(self.undo_last)
        actions.addWidget(undo)
        actions.addStretch()
        self.stop_button = QPushButton('עצור שמירה')
        self.stop_button.setEnabled(False)
        self.stop_button.clicked.connect(self.stop_saving)
        actions.addWidget(self.stop_button)
        layout.addLayout(actions)
        return page

    # ---------------------------------------------------------
    # search page
    # ---------------------------------------------------------

    def search_page(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(34, 30, 34, 28)
        title = QLabel('חיפוש')
        title.setObjectName('PageTitle')
        layout.addWidget(title)
        subtitle = QLabel('חיפוש מהיר בכל המיקומים שהרשית ל-TuneVault.')
        subtitle.setObjectName('Muted')
        layout.addWidget(subtitle)
        row = QHBoxLayout()
        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText('חפש שיר, אלבום, תיקייה או קובץ…')
        self.search_input.returnPressed.connect(self.run_search)
        button = QPushButton('חיפוש')
        button.setObjectName('Primary')
        button.clicked.connect(self.run_search)
        row.addWidget(self.search_input, 1)
        row.addWidget(button)
        layout.addLayout(row)
        self.search_results = QListWidget()
        self.search_results.itemDoubleClicked.connect(self.open_search_result)
        layout.addWidget(self.search_results, 1)
        return page

    # ---------------------------------------------------------
    # roots
    # ---------------------------------------------------------

    def refresh_roots(self):
        self.roots_list.clear()
        valid = []
        for raw in self.config.get('roots', []):
            path = resolve(raw)
            if path.exists() and path.is_dir():
                valid.append(str(path))
                item = QListWidgetItem(f'📁  {path.name or path}')
                item.setData(Qt.UserRole, str(path))
                self.roots_list.addItem(item)
        self.config['roots'] = valid
        save_config(self.config)
        self.model.roots = valid
        self.card_location.setText(f'{len(valid)}\nמיקומים')
        if self.roots_list.count():
            self.roots_list.setCurrentRow(0)
        else:
            self.current_folder = None
            self.path_label.setText('הוסף ספרייה כדי להתחיל')
            self.items_list.clear()
            self.card_media.setText('0\nפריטים')

    def add_library(self):
        selected = QFileDialog.getExistingDirectory(self, 'בחר תיקייה')
        if not selected:
            return
        target = resolve(selected)
        roots = [resolve(x) for x in self.config.get('roots', [])]
        if any(target == root or target.is_relative_to(root) or root.is_relative_to(target) for root in roots):
            QMessageBox.information(self, 'כבר קיים', 'התיקייה כבר מכוסה על ידי מיקום מורשה.')
            return
        self.config.setdefault('roots', []).append(str(target))
        save_config(self.config)
        self.model.roots = self.config['roots']
        self.refresh_roots()

    def remove_library(self):
        item = self.roots_list.currentItem()
        if not item:
            return
        target = resolve(item.data(Qt.UserRole))
        answer = QMessageBox.question(self, 'הסר גישה', f'להסיר את «{target.name}»?\n\nשום קובץ לא יימחק מהמחשב.')
        if answer != QMessageBox.Yes:
            return
        self.config['roots'] = [raw for raw in self.config.get('roots', []) if resolve(raw) != target]
        save_config(self.config)
        self.model.roots = self.config['roots']
        self.refresh_roots()

    # ---------------------------------------------------------
    # navigation
    # ---------------------------------------------------------

    def select_root(self, item, _previous=None):
        if item:
            self.current_folder = resolve(item.data(Qt.UserRole))
            self.render_folder()

    def render_folder(self, *_):
        if not self.current_folder:
            return
        self.path_label.setText(str(self.current_folder))
        search = self.folder_filter.text().strip().casefold()
        self.items_list.clear()
        entries = self.model.virtual_entries(self.current_folder)
        media_count = 0
        for path, state in entries:
            if search and search not in path.name.casefold():
                continue
            if path.is_file() and path.suffix.lower() in {'.mp3', '.wav', '.flac', '.m4a', '.aac', '.ogg', '.opus', '.mp4', '.mkv', '.webm', '.mov', '.avi', '.m4v'}:
                media_count += 1
            label = f'{media_icon(path)}  {path.name}'
            if state == 'pending':
                label += '   ·   ממתין'
            item = QListWidgetItem(label)
            item.setData(Qt.UserRole, str(path))
            item.setToolTip(str(path))
            self.items_list.addItem(item)
        self.card_media.setText(f'{media_count}\nפריטים בתצוגה')
        self.card_pending.setText(f'{len(self.model.pending)}\nממתינים')
        self.refresh_queue()

    def open_item(self, item):
        path = resolve(item.data(Qt.UserRole))
        if path.is_dir():
            self.current_folder = path
            self.render_folder()

    def go_up(self):
        if not self.current_folder:
            return
        roots = [resolve(x) for x in self.config.get('roots', [])]
        if self.current_folder not in roots:
            parent = self.current_folder.parent
            if self.model.allowed(parent):
                self.current_folder = parent
                self.render_folder()

    def selected_path(self):
        item = self.items_list.currentItem()
        return resolve(item.data(Qt.UserRole)) if item else None

    def path_conflict(self, path: Path) -> bool:
        target = resolve(path)
        return any(resolve(existing) == target or existing.name.casefold() == target.name.casefold() for existing, _ in self.model.virtual_entries(target.parent))

    # ---------------------------------------------------------
    # staging
    # ---------------------------------------------------------

    def stage_folder(self):
        if not self.current_folder:
            return
        value, accepted = self.text_dialog('תיקייה חדשה', 'שם התיקייה:')
        if not accepted:
            return
        target = resolve(self.current_folder / safe_name(value))
        if self.path_conflict(target):
            QMessageBox.warning(self, 'שם בשימוש', 'כבר קיים פריט בשם הזה.')
            return
        try:
            self.model.stage(Operation('mkdir', f'יצירת תיקייה · {target.name}', path=str(target)))
        except Exception as exc:
            QMessageBox.warning(self, 'לא ניתן ליצור', str(exc))
            return
        self.mark_dirty()

    def stage_media(self):
        if not self.current_folder:
            return
        dialog = MediaDialog(self.current_folder, self)
        if dialog.exec() != QDialog.Accepted:
            return
        payloads = dialog.payloads()
        if len(payloads) > 1:
            playlist = PlaylistDialog(payloads, self)
            if playlist.exec() != QDialog.Accepted:
                return
        for payload in payloads:
            try:
                self.model.stage(Operation('download', f'הורדה · {payload.title} [{payload.normalized_format()}]', payload=payload))
            except Exception as exc:
                QMessageBox.warning(self, 'לא ניתן להוסיף', str(exc))
        self.mark_dirty()
        self.navigate(1)

    def rename_selected(self):
        old = self.selected_path()
        if not old:
            return
        current = old.stem if old.is_file() else old.name
        value, accepted = self.text_dialog('שינוי שם', 'השם החדש:', current)
        if not accepted:
            return
        clean = safe_name(value)
        if old.is_file() and old.suffix:
            clean += old.suffix
        new = resolve(old.parent / clean)
        if new == old:
            return
        if self.path_conflict(new):
            QMessageBox.warning(self, 'שם בשימוש', 'כבר קיים פריט בשם הזה.')
            return
        try:
            self.model.stage(Operation('rename', f'שינוי שם · {old.name} → {new.name}', old=str(old), new=str(new)))
        except Exception as exc:
            QMessageBox.warning(self, 'לא ניתן לשנות שם', str(exc))
            return
        self.mark_dirty()

    def move_selected(self):
        old = self.selected_path()
        if not old:
            return
        destination = QFileDialog.getExistingDirectory(self, 'בחר תיקיית יעד', str(old.parent))
        if not destination:
            return
        destination = resolve(destination)
        if not self.model.allowed(destination):
            QMessageBox.warning(self, 'העברה חסומה', 'יעד ההעברה אינו מיקום מורשה.')
            return
        new = destination / old.name
        if self.path_conflict(new):
            QMessageBox.warning(self, 'שם בשימוש', 'קיים פריט באותו שם ביעד.')
            return
        try:
            self.model.stage(Operation('move', f'העברה · {old.name} → {destination.name}', old=str(old), new=str(new)))
        except Exception as exc:
            QMessageBox.warning(self, 'לא ניתן להעביר', str(exc))
            return
        self.mark_dirty()

    def delete_selected(self):
        path = self.selected_path()
        if not path:
            return
        answer = QMessageBox.question(self, 'מחיקה', f'לסמן את «{path.name}» למחיקה?\n\nשום דבר לא יימחק עד «שמירת שינויים».')
        if answer != QMessageBox.Yes:
            return
        try:
            self.model.stage(Operation('delete', f'מחיקה · {path.name}', path=str(path)))
        except Exception as exc:
            QMessageBox.warning(self, 'לא ניתן למחוק', str(exc))
            return
        self.mark_dirty()

    def text_dialog(self, title, prompt, default=''):
        from PySide6.QtWidgets import QInputDialog
        return QInputDialog.getText(self, title, prompt, text=default)

    # ---------------------------------------------------------
    # save / stop
    # ---------------------------------------------------------

    def mark_dirty(self):
        count = len(self.model.pending)
        self.status_pill.setText(f'{count} פעולות ממתינות')
        self.save_button.setEnabled(count > 0)
        self.save_button.setText(f'שמירת שינויים · {count}' if count else 'הכול נשמר')
        self.card_pending.setText(f'{count}\nממתינים')
        self.render_folder()
        self.refresh_queue()

    def save_changes(self):
        if self.saving or not self.model.pending:
            return
        self.saving = True
        self.stop_button.setEnabled(True)
        self.save_button.setEnabled(False)
        self.save_button.setText('שומר…')
        self.status_pill.setText('מבצע שינויים…')
        self.engine = ApplyEngine(self.model)
        worker = Worker(self.run_engine)
        worker.signals.result.connect(self.save_finished)
        worker.signals.error.connect(self.save_error)
        worker.signals.progress.connect(self.handle_progress)
        # result is emitted by run_engine; progress travels through this Worker wrapper only via explicit emit below
        self._active_worker = worker
        self.pool.start(worker)

    def run_engine(self):
        if not self.engine:
            return []
        return self.engine.apply(lambda op, index, total: self._emit_progress(op))

    def _emit_progress(self, operation):
        worker = getattr(self, '_active_worker', None)
        if worker:
            worker.signals.progress.emit(operation)

    def handle_progress(self, operation):
        row = self.operation_rows.get(operation.id)
        if row:
            row.update_operation(operation)
        self.downloads_summary.setText(f'{operation.label}  ·  {operation.progress}%')
        self.refresh_queue(update_only=True)

    def stop_saving(self):
        if self.engine:
            self.engine.cancel()
            self.status_pill.setText('עוצר…')

    def save_finished(self, results):
        successful = [op for op in results if op.status == 'success']
        remaining = [op for op in results if op.status in {'failed', 'cancelled'}]
        with self.model._lock:
            self.model.history.extend(successful)
            self.model.pending = remaining
        self.saving = False
        self.stop_button.setEnabled(False)
        self.engine = None
        self.overall_message = ''
        self.mark_dirty()
        if remaining:
            self.status_pill.setText(f'{len(remaining)} פעולות דורשות טיפול')
        else:
            self.status_pill.setText('הכול נשמר בהצלחה')
            self.save_button.setEnabled(False)
            self.save_button.setText('הכול נשמר')
        if successful and remaining:
            QMessageBox.warning(self, 'שמירה חלקית', f'{len(successful)} פעולות הצליחו. {len(remaining)} נשארו בתור.')
        elif successful:
            QMessageBox.information(self, 'השמירה הושלמה', f'{len(successful)} פעולות בוצעו בהצלחה.')
        self.refresh_queue()

    def save_error(self, error):
        self.saving = False
        self.stop_button.setEnabled(False)
        self.engine = None
        self.mark_dirty()
        self.status_pill.setText('שגיאה')
        QMessageBox.critical(self, 'שגיאת שמירה', error)

    # ---------------------------------------------------------
    # queue/history
    # ---------------------------------------------------------

    def refresh_queue(self, update_only=False):
        if not hasattr(self, 'queue_layout'):
            return
        pending = list(self.model.pending)
        history = list(self.model.history)
        self.downloads_summary.setText(f'{len(pending)} פעולות ממתינות · {len(history)} פעולות הושלמו')

        if update_only:
            current_ids = {op.id for op in pending}
            for op_id, row in list(self.operation_rows.items()):
                if row.operation.id not in current_ids:
                    row.deleteLater()
                    self.operation_rows.pop(op_id, None)
            for op in pending:
                row = self.operation_rows.get(op.id)
                if row:
                    row.update_operation(op)
            return

        while self.queue_layout.count():
            item = self.queue_layout.takeAt(0)
            widget = item.widget()
            if widget:
                widget.deleteLater()
        self.operation_rows.clear()

        for operation in pending:
            row = OperationRow(operation)
            self.operation_rows[operation.id] = row
            self.queue_layout.addWidget(row)

        if history:
            header = QLabel('היסטוריה אחרונה')
            header.setObjectName('Eyebrow')
            self.queue_layout.addWidget(header)
            for operation in reversed(history[-40:]):
                row = OperationRow(operation)
                self.queue_layout.addWidget(row)

        if not pending and not history:
            empty = QLabel('אין עדיין פעולות. הוסף מדיה או בצע שינוי בספרייה.')
            empty.setObjectName('Muted')
            self.queue_layout.addWidget(empty)

    def undo_last(self):
        if self.saving:
            return
        operation = self.model.undo_last()
        if operation:
            self.status_pill.setText('הפעולה הוסרה מהתור')
            self.mark_dirty()

    # ---------------------------------------------------------
    # search
    # ---------------------------------------------------------

    @staticmethod
    def search_worker(roots, query):
        found = []
        for root in roots:
            if not root.exists():
                continue
            try:
                for path in root.rglob('*'):
                    if query in path.name.casefold():
                        found.append(str(path))
                    if len(found) >= 2500:
                        return found
            except OSError:
                continue
        return found

    def run_search(self):
        query = self.search_input.text().strip().casefold()
        if not query:
            return
        self.search_results.clear()
        roots = [resolve(root) for root in self.config.get('roots', [])]
        worker = Worker(self.search_worker, roots, query)
        worker.signals.result.connect(self.show_search_results)
        worker.signals.error.connect(lambda e: QMessageBox.warning(self, 'החיפוש נכשל', e))
        self.status_pill.setText('מחפש…')
        self.pool.start(worker)

    def show_search_results(self, results):
        self.search_results.clear()
        for raw in results:
            path = Path(raw)
            item = QListWidgetItem(f'{media_icon(path)}  {path.name}\n{path.parent}')
            item.setData(Qt.UserRole, raw)
            self.search_results.addItem(item)
        self.status_pill.setText(f'נמצאו {len(results)} תוצאות')

    def open_search_result(self, item):
        path = resolve(item.data(Qt.UserRole))
        self.current_folder = path if path.is_dir() else path.parent
        self.navigate(0)
        self.render_folder()

    # ---------------------------------------------------------
    # shutdown
    # ---------------------------------------------------------

    def closeEvent(self, event):
        if self.saving:
            QMessageBox.warning(self, 'שמירה בתהליך', 'יש להמתין לסיום השמירה או לעצור אותה לפני סגירת TuneVault.')
            event.ignore()
            return
        if self.model.pending:
            answer = QMessageBox.question(self, 'שינויים שלא נשמרו', 'יש שינויים בתור. לסגור בלי לשמור?')
            if answer != QMessageBox.Yes:
                event.ignore()
                return
        event.accept()


STYLE = r'''
* { font-family: "Segoe UI"; }
QWidget { background: #080a0e; color: #f2f5f8; font-size: 14px; }
#Sidebar { background: #0c0f14; border-right: 1px solid #202630; }
#Brand { font-size: 32px; font-weight: 800; letter-spacing: -1px; }
#PageTitle { font-size: 36px; font-weight: 800; letter-spacing: -1px; }
#DialogTitle { font-size: 27px; font-weight: 750; }
#SectionTitle { font-size: 19px; font-weight: 700; }
#MediaTitle { font-size: 21px; font-weight: 750; }
#RowTitle { font-size: 14px; font-weight: 650; }
#Eyebrow { color: #76808e; font-size: 11px; font-weight: 800; letter-spacing: 1px; }
#Muted { color: #929cab; }
#StatusPill { background: #171c23; border: 1px solid #2b323c; border-radius: 11px; padding: 11px 13px; color: #c3c9d1; font-weight: 700; }
#NavButton { background: transparent; border: 0; border-radius: 11px; text-align: right; padding: 13px 15px; color: #abb4c0; font-weight: 650; }
#NavButton:hover, #NavButton[active="true"] { background: #181e26; color: #ffffff; }
QLineEdit, QComboBox { background: #10151b; border: 1px solid #2a313a; border-radius: 11px; padding: 11px 13px; selection-background-color: #2c3540; }
QLineEdit:focus, QComboBox:focus { border: 1px solid #667181; }
QPushButton { background: #151a21; border: 1px solid #2a3038; border-radius: 11px; padding: 10px 15px; font-weight: 650; }
QPushButton:hover { background: #20262e; }
#Primary { background: #f3f5f7; color: #090b0f; border: 0; font-weight: 800; }
#Primary:hover { background: #ffffff; }
QListWidget { background: #0e1217; border: 1px solid #252c35; border-radius: 14px; padding: 7px; }
QListWidget::item { padding: 15px; border-radius: 10px; margin: 2px 0; }
QListWidget::item:hover { background: #171d25; }
QListWidget::item:selected { background: #242c36; }
#Panel, #OperationRow { background: #11161d; border: 1px solid #272e37; border-radius: 15px; }
#Thumbnail { background: #0a0d11; border: 1px solid #292f38; border-radius: 11px; color: #68727f; font-size: 11px; font-weight: 700; }
#MetricCard { background: #11161d; border: 1px solid #252c34; border-radius: 14px; padding: 16px; font-size: 16px; font-weight: 750; }
QProgressBar { background: #0d1116; border: 1px solid #252c33; border-radius: 8px; height: 13px; text-align: center; }
QProgressBar::chunk { border-radius: 7px; }
QSplitter::handle { background: #1b222a; }
QScrollArea { background: transparent; }
QScrollBar:vertical { background: #0a0d11; width: 10px; }
QScrollBar::handle:vertical { background: #303842; border-radius: 5px; min-height: 30px; }
'''


def main():
    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setLayoutDirection(Qt.RightToLeft)
    app.setStyleSheet(STYLE)
    window = MainWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == '__main__':
    main()
