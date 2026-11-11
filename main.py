"""Script Tool: local screenplay search, keeper timeline and writer."""
import json
import re
import sys
import uuid
from pathlib import Path

import fitz
from rapidfuzz import fuzz
from PySide6.QtCore import QStandardPaths, QTimer
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QTabWidget, QVBoxLayout, QHBoxLayout,
    QPushButton, QLineEdit, QLabel, QListWidget, QTextEdit, QFileDialog,
    QMessageBox, QInputDialog, QSplitter, QComboBox
)

SCENE = re.compile(r'^\s*(?:INT\.|EXT\.|INT/EXT\.|EXT/INT\.|I/E\.)\s+', re.I)

def split_scenes(text):
    scenes = []
    current = None
    for line in text.replace('\r', '').split('\n'):
        if SCENE.match(line):
            if current:
                scenes.append(current)
            current = {'id': str(uuid.uuid4()), 'slug': line.strip(), 'text': line + '\n'}
        elif current:
            current['text'] += line + '\n'
    if current:
        scenes.append(current)
    return scenes

class Studio(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle('Script Tool — Screenplay Studio')
        self.resize(1250, 820)
        self.data = {'sources': [], 'keepers': [], 'title': 'Untitled', 'draft': ''}
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
        self.tabs.currentChanged.connect(lambda _: self.refresh())
        if self.autosave_file.exists():
            try:
                loaded = json.loads(self.autosave_file.read_text())
                if isinstance(loaded.get('sources'), list) and isinstance(loaded.get('keepers'), list):
                    self.data = loaded
            except (OSError, ValueError, TypeError):
                pass
        self.title_edit.setText(self.data.get('title', 'Untitled'))
        self.editor.setPlainText(self.data.get('draft', ''))
        self.refresh()

    def button(self, text, fn, layout):
        b = QPushButton(text)
        b.clicked.connect(fn)
        layout.addWidget(b)
        return b

    def build_search(self):
        tab = QWidget()
        layout = QVBoxLayout(tab)
        bar = QHBoxLayout()
        self.button('Import PDF / TXT / Fountain', self.import_files, bar)
        self.query = QLineEdit()
        self.query.setPlaceholderText('Search diner scene, dialogue, character, location…')
        self.query.textChanged.connect(self.refresh_search)
        bar.addWidget(self.query)
        layout.addLayout(bar)
        self.sources_label = QLabel()
        layout.addWidget(self.sources_label)
        splitter = QSplitter()
        self.results = QListWidget()
        self.results.currentRowChanged.connect(self.preview_result)
        splitter.addWidget(self.results)
        right = QWidget()
        r = QVBoxLayout(right)
        self.preview = QTextEdit()
        self.preview.setReadOnly(True)
        self.preview.setFont(QFont('Courier New', 12))
        r.addWidget(self.preview)
        self.button('Add selected scene to Keepers', self.add_keeper, r)
        splitter.addWidget(right)
        layout.addWidget(splitter)
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
        self.button('Open project', self.open_project, bar)
        self.button('Save project', self.save_project, bar)
        self.button('Export Fountain', self.export_fountain, bar)
        self.button('Export PDF', self.export_pdf, bar)
        layout.addLayout(bar)
        splitter = QSplitter()
        self.editor = QTextEdit()
        self.editor.setFont(QFont('Courier New', 12))
        self.editor.setPlaceholderText('INT. DINER - NIGHT\n\nRONNIE\nCoffee. Black.')
        self.editor.textChanged.connect(self.changed)
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
        splitter.addWidget(sidebar)
        splitter.setSizes([850, 350])
        layout.addWidget(splitter)
        self.tabs.addTab(tab, 'Writing')

    def changed(self):
        self.data['title'] = self.title_edit.text()
        self.data['draft'] = self.editor.toPlainText()
        self.timer.start(500)

    def save_local(self):
        try:
            self.autosave_file.write_text(json.dumps(self.data, ensure_ascii=False), encoding='utf-8')
        except OSError as e:
            self.statusBar().showMessage('Autosave failed: ' + str(e), 10000)

    def import_files(self):
        paths, _ = QFileDialog.getOpenFileNames(self, 'Import screenplays', '', 'Screenplays (*.pdf *.txt *.fountain)')
        for name in paths:
            try:
                if name.lower().endswith('.pdf'):
                    with fitz.open(name) as doc:
                        text = '\n'.join(page.get_text(sort=True) for page in doc)
                else:
                    text = Path(name).read_text(encoding='utf-8-sig')
                scenes = split_scenes(text)
                if not scenes:
                    QMessageBox.warning(self, 'No scenes', f'No INT./EXT. headings detected in {name}.')
                    continue
                self.data['sources'].append({'name': Path(name).name, 'id': str(uuid.uuid4()), 'scenes': scenes})
            except Exception as e:
                QMessageBox.warning(self, 'Import failed', f'{name}\n{e}')
        self.save_local()
        self.refresh()

    def all_scenes(self):
        return [{**scene, 'source': source['name']} for source in self.data['sources'] for scene in source['scenes']]

    def find_scene(self, scene_id):
        return next((s for s in self.all_scenes() if s['id'] == scene_id), None)

    def refresh_search(self):
        query = self.query.text().lower().strip()
        terms = [w for w in query.split() if w not in ('scene', 'the', 'a', 'in')]
        candidates = self.all_scenes()
        def rank(s):
            body = s['text'].lower()
            slug = s['slug'].lower()
            return sum(5 if t in slug else 1 if t in body else 0 for t in terms) + (fuzz.partial_ratio(query, slug) / 100 if query else 0)
        self.matches = sorted((s for s in candidates if not terms or rank(s) > 0), key=rank, reverse=True)[:300]
        self.results.blockSignals(True)
        self.results.clear()
        self.results.addItems([s['slug'] + '  —  ' + s['source'] for s in self.matches])
        self.results.blockSignals(False)
        self.sources_label.setText(f"{len(self.data['sources'])} imported scripts · {len(candidates)} indexed scenes")
        self.preview_result(0)
        if self.matches:
            self.results.setCurrentRow(0)

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
                self.editor.setPlainText(data.get('draft', ''))
                self.save_local()
                self.refresh()
            except (OSError, ValueError, TypeError) as e:
                QMessageBox.warning(self, 'Open failed', str(e))

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
