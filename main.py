"""Script Tool: local screenplay search, keeper timeline and writer."""
import json
import textwrap
import re
import sys
import uuid
from pathlib import Path

import fitz
from rapidfuzz import fuzz
from PySide6.QtCore import QStandardPaths, QTimer, Qt, QStringListModel
from PySide6.QtGui import QFont, QTextBlockFormat, QTextCursor, QTextCharFormat
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QTabWidget, QVBoxLayout, QHBoxLayout,
    QPushButton, QLineEdit, QLabel, QListWidget, QTextEdit, QFileDialog,
    QMessageBox, QInputDialog, QSplitter, QComboBox, QCompleter, QFrame
)

# Standard sluglines, plus numbered shooting-script shots such as
# "3  CLOSER - FRANK   3" and "63A  INT. ELDO - NIGHT  63A".
SCENE = re.compile(
    r'^\s*(?:(?:\d{1,4}[A-Z]?[.)]?|[A-Z]\d{1,3})\s+)?'
    r'(?:INT\s*[./-]\s*EXT|EXT\s*[./-]\s*INT|INT|EXT|I\s*/\s*E)'
    r'\s*[.:-]?\s+(?=\S)', re.I
)
NUMBERED_SHOT = re.compile(r'^\s*(\d{1,4}[A-Z]?)\s{2,}(.+?)\s{2,}\1(?:\s+\*)?\s*$', re.I)
CONTINUATION = re.compile(r'^\s*(?:CONTINUED\b|OMITTED\b)', re.I)

def normalize_pdf_text(text):
    """Repair common PDF extraction whitespace and punctuation."""
    text = text.replace('\u00a0', ' ').replace('\u200b', '').replace('\ufeff', '')
    text = text.replace('\u2013', '-').replace('\u2014', '-')
    return re.sub(
        r'(?im)^(\s*(?:\d+\s+)?)(I\s+N\s+T|E\s+X\s+T)\s*\.',
        lambda m: m.group(1) + m.group(2).replace(' ', '') + '.', text
    )

def split_scenes(text):
    scenes = []
    current = None
    for line in normalize_pdf_text(text).replace('\r', '').split('\n'):
        numbered = NUMBERED_SHOT.match(line)
        is_heading = bool(SCENE.match(line))
        if numbered and not CONTINUATION.match(numbered.group(2)):
            is_heading = True
        if is_heading:
            if current:
                scenes.append(current)
            heading = line.strip()
            current = {'id': str(uuid.uuid4()), 'slug': heading, 'text': line + '\n'}
        elif current:
            current['text'] += line + '\n'
    if current:
        scenes.append(current)
    return scenes

class ScriptEditor(QTextEdit):
    """Screenplay editor with smart element switching and character recall."""
    STYLES = ('Scene', 'Action', 'Character', 'Dialogue', 'Parenthetical', 'Transition')
    INDENTS = (0, 0, 180, 110, 145, 220)

    def __init__(self):
        super().__init__()
        self.mode = 'Action'
        self.setFont(QFont('Courier New', 12))
        self.setLineWrapMode(QTextEdit.FixedPixelWidth)
        self.setLineWrapColumnOrWidth(650)
        self.document().setDocumentMargin(38)
        self.setStyleSheet('QTextEdit { background: #f9f8f4; color: #242424; border: 1px solid #bdbdbd; selection-background-color: #3469a5; }')
        self.characters = set()
        self.completer = QCompleter(self)
        self.completer.setCaseSensitivity(Qt.CaseInsensitive)
        self.completer.setCompletionMode(QCompleter.PopupCompletion)
        self.completer.setWidget(self)
        self.completer.activated.connect(self.accept_character)
        self.textChanged.connect(self.suggest_character)
        self.cursorPositionChanged.connect(self.sync_mode)
        self.set_mode('Action')

    def reset_characters(self):
        self.characters.clear()
        self.completer.popup().hide()

    def rebuild_characters(self):
        self.characters.clear()
        block = self.document().firstBlock()
        while block.isValid():
            if block.userState() == self.STYLES.index('Character'):
                name = block.text().strip().upper()
                if name and len(name) < 60:
                    self.characters.add(name)
            block = block.next()

    def sync_mode(self):
        state = self.textCursor().block().userState()
        if 0 <= state < len(self.STYLES):
            self.mode = self.STYLES[state]
        else:
            self.mode = 'Action'

    def set_mode(self, mode):
        if mode not in self.STYLES:
            return
        self.mode = mode
        cursor = self.textCursor()
        fmt = QTextBlockFormat()
        fmt.setLeftMargin(self.INDENTS[self.STYLES.index(mode)])
        fmt.setRightMargin(10)
        fmt.setLineHeight(125, QTextBlockFormat.ProportionalHeight)
        fmt.setTopMargin(10 if mode in ('Scene', 'Character', 'Transition') else 2)
        fmt.setBottomMargin(5 if mode in ('Dialogue', 'Action') else 2)
        cursor.mergeBlockFormat(fmt)
        cursor.block().setUserState(self.STYLES.index(mode))
        self.setTextCursor(cursor)
        if mode != 'Character':
            self.completer.popup().hide()

    def remember_character(self, name):
        name = name.strip().upper()
        if name and len(name) < 60:
            self.characters.add(name)
            if hasattr(self, 'characters_changed'):
                self.characters_changed()

    def suggest_character(self):
        if self.mode != 'Character':
            self.completer.popup().hide()
            return
        cursor = self.textCursor()
        if cursor.hasSelection():
            return
        prefix = cursor.block().text().strip().upper()
        if not prefix or len(prefix) > 50:
            self.completer.popup().hide()
            return
        matches = sorted(name for name in self.characters if name.startswith(prefix) and name != prefix)
        if not matches:
            self.completer.popup().hide()
            return
        self.completer.setModel(QStringListModel(matches, self.completer))
        self.completer.setCompletionPrefix(prefix)
        rect = self.cursorRect()
        rect.setWidth(max(190, self.completer.popup().sizeHintForColumn(0) + 30))
        self.completer.complete(rect)

    def accept_character(self, name):
        cursor = self.textCursor()
        cursor.movePosition(QTextCursor.StartOfBlock, QTextCursor.KeepAnchor)
        cursor.insertText(name)
        self.setTextCursor(cursor)
        self.set_mode('Character')
        self.completer.popup().hide()

    def keyPressEvent(self, event):
        key = event.key()
        modifiers = event.modifiers()
        popup = self.completer.popup()
        if popup.isVisible():
            if key in (Qt.Key_Tab, Qt.Key_Return, Qt.Key_Enter):
                item = popup.currentIndex()
                name = item.data() if item.isValid() else self.completer.currentCompletion()
                if name:
                    self.accept_character(name)
                event.accept()
                return
            if key == Qt.Key_Escape:
                popup.hide()
                event.accept()
                return

        if modifiers & Qt.ControlModifier and Qt.Key_1 <= key <= Qt.Key_6:
            self.set_mode(self.STYLES[int(key) - int(Qt.Key_1)])
            event.accept()
            return

        if key == Qt.Key_Tab and modifiers == Qt.NoModifier:
            # Tab cycles through screenplay element types without inserting whitespace.
            index = (self.STYLES.index(self.mode) + 1) % len(self.STYLES)
            self.set_mode(self.STYLES[index])
            event.accept()
            return

        if key in (Qt.Key_Return, Qt.Key_Enter) and modifiers == Qt.NoModifier:
            previous = self.textCursor().block().text().strip()
            current = self.mode
            if current == 'Character':
                self.remember_character(previous)
            super().keyPressEvent(event)
            if current == 'Scene' or SCENE.match(previous):
                next_mode = 'Action'
            elif current == 'Character':
                next_mode = 'Dialogue'
            elif current in ('Dialogue', 'Parenthetical'):
                next_mode = 'Dialogue' if previous else 'Action'
            elif current == 'Transition':
                next_mode = 'Scene'
            elif current == 'Action' and previous.isupper() and len(previous) < 45 and not previous.endswith(':'):
                self.remember_character(previous)
                next_mode = 'Dialogue'
            else:
                next_mode = 'Action'
            self.set_mode(next_mode)
            return

        super().keyPressEvent(event)


class Studio(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle('Script Tool — Screenplay Studio')
        self.resize(1250, 820)
        self.data = {'sources': [], 'keepers': [], 'title': 'Untitled', 'draft': '', 'alternates': []}
        folder = Path(QStandardPaths.writableLocation(QStandardPaths.AppDataLocation))
        folder.mkdir(parents=True, exist_ok=True)
        self.autosave_file = folder / 'autosave.json'
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.timeout.connect(self.save_local)
        self.tabs = QTabWidget()
        self.setCentralWidget(self.tabs)
        self.build_search()
        self.build_keepers()
        self.build_writer()
        self.build_menus()
        self.tabs.currentChanged.connect(lambda _: self.refresh())
        if self.autosave_file.exists():
            try:
                loaded = json.loads(self.autosave_file.read_text())
                if isinstance(loaded.get('sources'), list) and isinstance(loaded.get('keepers'), list):
                    self.data = loaded
            except (OSError, ValueError, TypeError):
                pass
        self.title_edit.setText(self.data.get('title', 'Untitled'))
        if self.data.get('draft_html'):
            self.editor.setHtml(self.data['draft_html'])
        else:
            self.editor.setPlainText(self.data.get('draft', ''))
        self.editor.characters.update(self.data.get('characters', []))
        self.refresh()

    def button(self, text, fn, layout):
        b = QPushButton(text)
        b.clicked.connect(fn)
        layout.addWidget(b)
        return b

    def build_menus(self):
        file_menu = self.menuBar().addMenu('&File')
        file_menu.addAction('New Project', self.new_project, 'Ctrl+N')
        file_menu.addAction('Open Project…', self.open_project, 'Ctrl+O')
        file_menu.addAction('Save Project…', self.save_project, 'Ctrl+S')
        file_menu.addSeparator()
        file_menu.addAction('Import Script…', self.import_files, 'Ctrl+I')
        export_menu = file_menu.addMenu('Export')
        export_menu.addAction('Fountain…', self.export_fountain)
        export_menu.addAction('PDF…', self.export_pdf)

        scene_menu = self.menuBar().addMenu('&Scene')
        scene_menu.addAction('Remove Selected Scene from Library', self.remove_scene)

    def build_search(self):
        tab = QWidget()
        layout = QVBoxLayout(tab)
        bar = QHBoxLayout()
        self.query = QLineEdit()
        self.query.setPlaceholderText('Search diner scene, dialogue, character, location…')
        self.query.textChanged.connect(self.refresh_search)
        bar.addWidget(self.query)
        layout.addLayout(bar)
        self.sources_label = QLabel()
        self.sources_label.setFixedHeight(22)
        layout.addWidget(self.sources_label, 0)
        splitter = QSplitter()
        splitter.setOrientation(Qt.Horizontal)
        self.results = QListWidget()
        self.results.currentRowChanged.connect(self.preview_result)
        splitter.addWidget(self.results)
        right = QWidget()
        r = QVBoxLayout(right)
        self.preview = QTextEdit()
        self.preview.setReadOnly(True)
        self.preview.setFont(QFont('Courier New', 12))
        r.addWidget(self.preview)
        keeper_controls = QHBoxLayout()
        self.button('Add to Keepers', self.add_keeper, keeper_controls)
        self.button('Remove from Keepers', self.remove_scene_keeper, keeper_controls)
        r.addLayout(keeper_controls)
        splitter.addWidget(right)
        splitter.setStretchFactor(0, 2)
        splitter.setStretchFactor(1, 3)
        splitter.setSizes([480, 720])
        layout.addWidget(splitter, 1)
        self.tabs.addTab(tab, 'Search')
        self.matches = []

    def build_keepers(self):
        tab = QWidget()
        layout = QHBoxLayout(tab)
        left = QVBoxLayout()
        self.keeper_list = QListWidget()
        self.keeper_list.currentRowChanged.connect(self.preview_keeper)
        left.addWidget(self.keeper_list)
        controls = QHBoxLayout()
        self.button('Up', lambda: self.move_keeper(-1), controls)
        self.button('Down', lambda: self.move_keeper(1), controls)
        self.button('Remove', self.remove_keeper, controls)
        left.addLayout(controls)
        self.button('Export Keepers as PDF…', self.export_keepers_pdf, left)
        layout.addLayout(left, 1)
        right = QVBoxLayout()
        self.keeper_preview = QTextEdit()
        self.keeper_preview.setReadOnly(True)
        self.keeper_preview.setFont(QFont('Courier New', 11))
        right.addWidget(self.keeper_preview)
        self.button('Rename character in this keeper', self.rename_character, right)
        right.addWidget(QLabel('Keeper notes'))
        self.notes = QTextEdit()
        self.notes.textChanged.connect(self.update_notes)
        right.addWidget(self.notes)
        layout.addLayout(right, 2)
        self.tabs.addTab(tab, 'Keepers')

    def build_writer(self):
        tab = QWidget()
        layout = QVBoxLayout(tab)
        bar = QHBoxLayout()
        self.title_edit = QLineEdit()
        self.title_edit.setPlaceholderText('Screenplay title')
        self.title_edit.textChanged.connect(self.changed)
        bar.addWidget(self.title_edit)
        layout.addLayout(bar)
        style_bar = QHBoxLayout()
        style_bar.addWidget(QLabel('Paragraph style:'))
        self.style_select = QComboBox()
        self.style_select.addItems(ScriptEditor.STYLES)
        self.style_select.currentTextChanged.connect(self.editor_style_changed)
        style_bar.addWidget(self.style_select)
        style_bar.addWidget(QLabel('Tab: next style  |  ⌘1–6: select style  |  Enter: auto-format next line'))
        style_bar.addStretch()
        layout.addLayout(style_bar)
        splitter = QSplitter()
        self.editor = ScriptEditor()
        self.editor.setPlaceholderText('INT. DINER - NIGHT\n\nRONNIE\nCoffee. Black.')
        self.editor.textChanged.connect(self.changed)
        self.editor.cursorPositionChanged.connect(self.update_style_select)
        self.editor.cursorPositionChanged.connect(self.refresh_alternates)
        self.editor.characters_changed = self.changed
        splitter.addWidget(self.editor)
        sidebar = QWidget()
        side = QVBoxLayout(sidebar)
        side.addWidget(QLabel('Keeper references'))
        self.references = QListWidget()
        self.references.currentRowChanged.connect(self.preview_reference)
        side.addWidget(self.references)
        self.reference_text = QTextEdit()
        self.reference_text.setReadOnly(True)
        self.reference_text.setFont(QFont('Courier New', 10))
        side.addWidget(self.reference_text)
        side.addWidget(QLabel('Writing alternates'))
        self.alternate_list = QListWidget()
        self.alternate_list.setMaximumHeight(170)
        side.addWidget(self.alternate_list)
        alternate_buttons = QHBoxLayout()
        self.button('Save alternate', self.add_alternate, alternate_buttons)
        self.button('Use selected', self.use_alternate, alternate_buttons)
        side.addLayout(alternate_buttons)
        side.addWidget(QLabel('Select text in the editor, then save an alternate.'))
        splitter.addWidget(sidebar)
        splitter.setSizes([850, 350])
        layout.addWidget(splitter)
        self.tabs.addTab(tab, 'Writing')

    def add_alternate(self):
        cursor = self.editor.textCursor()
        selected = cursor.selectedText().replace('\u2029', '\n').strip()
        if not selected:
            QMessageBox.information(self, 'Select text', 'Select a passage in Writing first.')
            return
        alternates = self.data.setdefault('alternates', [])
        group = next((g for g in alternates if selected in g['versions']), None)
        if group is None:
            group = {'versions': [selected], 'active': selected}
            alternates.append(group)
        new, ok = QInputDialog.getMultiLineText(
            self, 'Add writing alternate', 'Alternate wording:', selected
        )
        if ok and new.strip() and new.strip() not in group['versions']:
            group['versions'].append(new.strip())
            self.changed()
        self.refresh_alternates()

    def refresh_alternates(self):
        if not hasattr(self, 'alternate_list'):
            return
        selected = self.editor.textCursor().selectedText().replace('\u2029', '\n').strip()
        current = self.editor.textCursor().block().text().strip()
        groups = self.data.get('alternates', [])
        group = next((g for g in groups if selected in g['versions'] or current in g['versions']), None)
        self.active_alternate_group = group
        self.alternate_list.clear()
        if group:
            for version in group['versions']:
                self.alternate_list.addItem(('✓ ' if version == group['active'] else '  ') + version.replace('\n', ' / ')[:130])

    def use_alternate(self):
        group = getattr(self, 'active_alternate_group', None)
        row = self.alternate_list.currentRow()
        if not group or not 0 <= row < len(group['versions']):
            return
        new = group['versions'][row]
        old = group['active']
        cursor = self.editor.textCursor()
        selected = cursor.selectedText().replace('\u2029', '\n').strip()
        if selected != old:
            # Find the active passage nearest the current cursor.
            document_text = self.editor.toPlainText()
            locations = [m.start() for m in re.finditer(re.escape(old), document_text)]
            if not locations:
                QMessageBox.warning(self, 'Passage not found', 'Select the active passage to switch its alternate.')
                return
            start = min(locations, key=lambda p: abs(p - cursor.position()))
            cursor.setPosition(start)
            cursor.setPosition(start + len(old), QTextCursor.KeepAnchor)
        cursor.insertText(new)
        group['active'] = new
        self.editor.setTextCursor(cursor)
        self.changed()
        self.refresh_alternates()

    def editor_style_changed(self, style):
        if hasattr(self, 'editor'):
            self.editor.set_mode(style)
            self.editor.setFocus()

    def update_style_select(self):
        if hasattr(self, 'style_select'):
            self.style_select.blockSignals(True)
            self.style_select.setCurrentText(self.editor.mode)
            self.style_select.blockSignals(False)

    def remove_scene_keeper(self):
        index = self.results.currentRow()
        if not 0 <= index < len(self.matches):
            return
        scene_id = self.matches[index]['id']
        before = len(self.data['keepers'])
        self.data['keepers'] = [k for k in self.data['keepers'] if k['scene_id'] != scene_id]
        if len(self.data['keepers']) != before:
            self.save_local()
            self.refresh_keepers()

    def changed(self):
        self.data['title'] = self.title_edit.text()
        self.data['draft'] = self.editor.toPlainText()
        self.data['draft_html'] = self.editor.toHtml()
        self.data['characters'] = sorted(self.editor.characters)
        self.timer.start(500)

    def save_local(self):
        try:
            self.autosave_file.write_text(json.dumps(self.data, ensure_ascii=False), encoding='utf-8')
        except OSError as e:
            self.statusBar().showMessage('Autosave failed: ' + str(e), 10000)

    def new_project(self):
        if QMessageBox.question(
            self, 'New project',
            'Start a new blank project? This clears the current script library, Keepers, and draft. Save your project first if you want to keep it.',
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No
        ) != QMessageBox.Yes:
            return
        self.timer.stop()
        self.data = {'sources': [], 'keepers': [], 'title': 'Untitled', 'draft': ''}
        self.query.clear()
        self.title_edit.setText('Untitled')
        self.editor.clear()
        self.editor.reset_characters()
        self.alternate_list.clear()
        self.active_alternate_group = None
        self.reference_text.clear()
        self.save_local()
        self.refresh()

    def remove_scene(self):
        index = self.results.currentRow()
        if not 0 <= index < len(self.matches):
            return
        scene = self.matches[index]
        if QMessageBox.question(
            self, 'Remove scene',
            f"Remove this scene from the current library?\n\n{scene['slug']}\n\nExisting Keepers will be preserved.",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No
        ) != QMessageBox.Yes:
            return
        for source in self.data['sources']:
            source['scenes'] = [item for item in source['scenes'] if item['id'] != scene['id']]
        self.save_local()
        self.refresh()

    def import_files(self):
        paths, _ = QFileDialog.getOpenFileNames(self, 'Import screenplays', '', 'Screenplays (*.pdf *.txt *.fountain)')
        if not paths:
            return
        if self.data['sources'] or self.data['keepers'] or self.editor.toPlainText().strip():
            if QMessageBox.question(
                self, 'Replace current project?',
                'Importing a new script starts a new project and clears the existing library, Keepers, and draft. Save your current project first if needed. Continue?',
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No
            ) != QMessageBox.Yes:
                return
        imported = []
        for name in paths:
            try:
                if name.lower().endswith('.pdf'):
                    with fitz.open(name) as doc:
                        text = '\n'.join(page.get_text(sort=True) for page in doc)
                else:
                    text = Path(name).read_text(encoding='utf-8-sig')
                scenes = split_scenes(text)
                if not scenes:
                    QMessageBox.warning(self, 'No scenes', f'No recognizable scene headings were extracted from {name}. This PDF may use unusual formatting or scanned images.')
                    continue
                imported.append({'name': Path(name).name, 'id': str(uuid.uuid4()), 'scenes': scenes})
            except Exception as e:
                QMessageBox.warning(self, 'Import failed', f'{name}\\n{e}')
        if not imported:
            return
        self.timer.stop()
        self.data = {'sources': imported, 'keepers': [], 'title': Path(paths[0]).stem, 'draft': '', 'alternates': []}
        self.query.clear()
        self.title_edit.setText(self.data['title'])
        self.editor.clear()
        self.save_local()
        self.refresh()

    def all_scenes(self):
        return [{**scene, 'source': source['name']} for source in self.data['sources'] for scene in source['scenes']]

    def find_scene(self, scene_id):
        return next((s for s in self.all_scenes() if s['id'] == scene_id), None)

    def refresh_search(self):
        query = self.query.text().strip()
        words = re.findall(r"\w+(?:['’]\w+)*", query, flags=re.UNICODE)
        phrase = (re.compile(r"(?<!\w)" + r"\s+".join(map(re.escape, words)) + r"(?!\w)", re.I)
                  if words else None)
        candidates = self.all_scenes()
        matches = []
        for scene in candidates:
            if phrase:
                match = phrase.search(scene['text'])
                if not match:
                    continue
                start, end = match.span()
                context = re.sub(r"\s+", " ", scene['text'][max(0, start-45):end+70]).strip()
                heading_hit = bool(phrase.search(scene['slug']))
            else:
                context = ""
                heading_hit = False
            matches.append((not heading_hit, scene, context))
        matches.sort(key=lambda item: item[0])
        self.matches = [scene for _, scene, _ in matches[:300]]
        self.results.blockSignals(True)
        self.results.clear()
        self.results.addItems([
            scene['slug'] + '  —  ' + scene['source'] +
            ('\n…' + context + '…' if context else '')
            for _, scene, context in matches[:300]
        ])
        self.results.blockSignals(False)
        self.sources_label.setText(
            f"{len(self.data['sources'])} imported scripts · "
            f"{len(candidates)} indexed scenes · {len(matches)} exact phrase matches"
        )
        if self.matches:
            self.results.setCurrentRow(0)
        else:
            self.preview.setPlainText('')

    def preview_result(self, index):
        self.preview.setPlainText(self.matches[index]['text'] if 0 <= index < len(self.matches) else '')

    def add_keeper(self):
        i = self.results.currentRow()
        if i < 0 or i >= len(self.matches):
            return
        scene = self.matches[i]
        self.data['keepers'].append({'id': str(uuid.uuid4()), 'scene_id': scene['id'], 'text': scene['text'], 'slug': scene['slug'], 'source': scene['source'], 'notes': ''})
        self.save_local()
        self.refresh_keepers()

    def refresh_keepers(self):
        self.keeper_list.blockSignals(True)
        self.keeper_list.clear()
        self.keeper_list.addItems([f"{i+1}. {k['slug']}" for i,k in enumerate(self.data['keepers'])])
        self.keeper_list.blockSignals(False)
        self.references.blockSignals(True)
        self.references.clear()
        self.references.addItems([k['slug'] for k in self.data['keepers']])
        self.references.blockSignals(False)
        self.reference_text.clear()
        self.keeper_preview.clear()
        self.notes.blockSignals(True)
        self.notes.clear()
        self.notes.blockSignals(False)
        self.preview_keeper(self.keeper_list.currentRow())

    def preview_keeper(self, i):
        k = self.data['keepers'][i] if 0 <= i < len(self.data['keepers']) else None
        self.keeper_preview.setPlainText(k['text'] if k else '')
        self.notes.blockSignals(True)
        self.notes.setPlainText(k.get('notes', '') if k else '')
        self.notes.blockSignals(False)

    def update_notes(self):
        i = self.keeper_list.currentRow()
        if 0 <= i < len(self.data['keepers']):
            self.data['keepers'][i]['notes'] = self.notes.toPlainText()
            self.timer.start(500)

    def move_keeper(self, offset):
        i = self.keeper_list.currentRow()
        j = i + offset
        if 0 <= i < len(self.data['keepers']) and 0 <= j < len(self.data['keepers']):
            ks = self.data['keepers']
            ks[i], ks[j] = ks[j], ks[i]
            self.refresh_keepers()
            self.keeper_list.setCurrentRow(j)
            self.save_local()

    def remove_keeper(self):
        i = self.keeper_list.currentRow()
        if 0 <= i < len(self.data['keepers']):
            self.data['keepers'].pop(i)
            self.refresh_keepers()
            self.save_local()

    def rename_character(self):
        i = self.keeper_list.currentRow()
        if i < 0:
            return
        old, ok = QInputDialog.getText(self, 'Rename character', 'Original character name:')
        if not ok or not old.strip():
            return
        new, ok = QInputDialog.getText(self, 'Rename character', 'Replacement name:')
        if not ok or not new.strip():
            return
        k = self.data['keepers'][i]
        k['text'] = re.sub(r'(?<![A-Za-z])' + re.escape(old.strip()) + r'(?![A-Za-z])', lambda _: new.strip(), k['text'], flags=re.I)
        self.preview_keeper(i)
        self.save_local()

    def preview_reference(self, i):
        self.reference_text.setPlainText(self.data['keepers'][i]['text'] if 0 <= i < len(self.data['keepers']) else '')

    def refresh(self):
        self.refresh_search()
        self.refresh_keepers()

    def save_project(self):
        path, _ = QFileDialog.getSaveFileName(self, 'Save project', self.data['title'] + '.json', 'Script Tool project (*.json)')
        if path:
            try:
                Path(path).write_text(json.dumps(self.data, ensure_ascii=False, indent=2), encoding='utf-8')
            except OSError as e:
                QMessageBox.warning(self, 'Save failed', str(e))

    def open_project(self):
        path, _ = QFileDialog.getOpenFileName(self, 'Open project', '', 'Script Tool project (*.json)')
        if path:
            try:
                data = json.loads(Path(path).read_text(encoding='utf-8'))
                if not isinstance(data.get('sources'), list) or not isinstance(data.get('keepers'), list):
                    raise ValueError('Invalid project file')
                self.data = data
                self.title_edit.setText(data.get('title', 'Untitled'))
                if data.get('draft_html'):
                    self.editor.setHtml(data['draft_html'])
                else:
                    self.editor.setPlainText(data.get('draft', ''))
                self.editor.reset_characters()
                self.editor.characters.update(data.get('characters', []))
                self.reference_text.clear()
                self.alternate_list.clear()
                self.active_alternate_group = None
                self.save_local()
                self.refresh()
            except (OSError, ValueError, TypeError) as e:
                QMessageBox.warning(self, 'Open failed', str(e))

    def export_keepers_pdf(self):
        """Export edited Keeper scenes in their current order as one screenplay PDF."""
        keepers = self.data.get('keepers', [])
        if not keepers:
            QMessageBox.information(self, 'Nothing to export', 'Add scenes to Keepers first.')
            return
        filename = (self.data.get('title') or 'Untitled') + ' - Keepers.pdf'
        path, _ = QFileDialog.getSaveFileName(
            self, 'Export assembled screenplay', filename, 'PDF files (*.pdf)'
        )
        if not path:
            return
        # Each Keeper stores its own edited text, including character renames.
        # Separate scenes with one blank line, without forcing a page break.
        assembled = '\n\n'.join(k.get('text', '').rstrip('\n') for k in keepers)
        try:
            self.write_screenplay_pdf(path, assembled)
        except Exception as exc:
            QMessageBox.warning(self, 'Export failed', str(exc))
            return
        self.statusBar().showMessage(f'Exported {len(keepers)} Keeper scenes to {path}', 10000)

    @staticmethod
    def write_screenplay_pdf(path, screenplay):
        """Paginate monospaced screenplay text with preserved indentation."""
        doc = fitz.open()
        page = None
        y = 72
        line_height = 14
        font_size = 11
        # Letter paper, 1.5-inch left and 1-inch right margins.
        # Courier at 11pt allows approximately 65 characters per line.
        left = 108
        max_columns = 65
        try:
            for raw in screenplay.expandtabs(4).split('\n'):
                wrapped = textwrap.wrap(
                    raw, width=max_columns, expand_tabs=False,
                    replace_whitespace=False, drop_whitespace=False,
                    break_long_words=True, break_on_hyphens=False
                ) if raw.strip() else ['']
                for line in wrapped:
                    if page is None or y > 720:
                        page = doc.new_page(width=612, height=792)
                        y = 72
                    if line:
                        page.insert_text((left, y), line, fontname='cour', fontsize=font_size)
                    y += line_height
            if page is None:
                doc.new_page(width=612, height=792)
            doc.save(path)
        finally:
            doc.close()

    def export_fountain(self):
        path, _ = QFileDialog.getSaveFileName(self, 'Export Fountain', self.data['title'] + '.fountain', 'Fountain (*.fountain)')
        if path:
            Path(path).write_text('Title: ' + self.data['title'] + '\n\n' + self.editor.toPlainText(), encoding='utf-8')

    def export_pdf(self):
        path, _ = QFileDialog.getSaveFileName(self, 'Export PDF', self.data['title'] + '.pdf', 'PDF (*.pdf)')
        if not path:
            return
        doc = fitz.open()
        lines = self.editor.toPlainText().splitlines()
        page = None
        y = 72
        for line in lines:
            if page is None or y > 730:
                page = doc.new_page(width=612, height=792)
                y = 72
            page.insert_text((72, y), line[:85], fontname='cour', fontsize=11)
            y += 15
        if not lines:
            doc.new_page()
        doc.save(path)
        doc.close()

if __name__ == '__main__':
    app = QApplication(sys.argv)
    app.setApplicationName('Script Tool')
    window = Studio()
    window.show()
    sys.exit(app.exec())
