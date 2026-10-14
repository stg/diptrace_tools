"""PySide6 viewer for compact placement studies and repeated PCB edits."""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime
import json
from pathlib import Path
import sys

sys.path.insert(0,str(Path(__file__).resolve().parent.parent))
from Shared.platform_helpers import open_file
from layout_core import (APP_DIR, SIZES_PATH, SETTINGS_PATH, DEFAULTS_PATH, Board, default_recipe,
                         group_refs, refs, load_sizes, size_match, polygon_gap,
                         replicate, intervals, write_json)
from lxml import etree as ET
from PySide6.QtCore import Qt, Signal, QThread, QTimer, QByteArray, QRectF
from PySide6.QtGui import QAction, QColor, QPen, QBrush, QPainterPath, QPolygonF, QTransform, QFont, QKeySequence, QPainter
from PySide6.QtCore import QPointF
from PySide6.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QFormLayout, QLabel, QPushButton, QToolButton, QCheckBox, QComboBox, QSpinBox,
    QDoubleSpinBox, QListWidget, QListWidgetItem, QGraphicsView, QGraphicsScene,
    QGraphicsItem, QSplitter, QFileDialog, QMessageBox, QMenu, QDialog,
    QDialogButtonBox, QLineEdit, QPlainTextEdit, QGroupBox, QStackedWidget, QProgressBar,
    QScrollArea, QInputDialog)


GROUP_COLORS = ('#9dd9f3', '#b6e3a4', '#f4cf8c', '#d0b8ed',
                '#f5b5c6', '#98dfd3', '#c5d2f2', '#e5d99a')


def color(index):
    if index < len(GROUP_COLORS):
        return QColor(GROUP_COLORS[index])
    return QColor.fromHsvF((index*.381966) % 1,.30,.95)


def spin(value, minimum=-100000, maximum=100000, decimals=3):
    box = QDoubleSpinBox(); box.setRange(minimum,maximum); box.setDecimals(decimals)
    box.setValue(value); box.setSingleStep(.1)
    return box


class Canvas(QGraphicsView):
    selected = Signal(str)
    context = Signal(str,object)

    def __init__(self):
        super().__init__()
        self.setScene(QGraphicsScene(self))
        self.setRenderHints(self.renderHints() | QPainter.Antialiasing)
        self.setDragMode(QGraphicsView.ScrollHandDrag)
        self.setTransformationAnchor(QGraphicsView.AnchorUnderMouse)
        self.setBackgroundBrush(QColor('#fafafa'))

    def ref_at(self,pos):
        item = self.itemAt(pos)
        while item:
            if item.data(0):
                return item.data(0)
            item = item.parentItem()
        return ''

    def mousePressEvent(self,event):
        if event.button() == Qt.LeftButton:
            self.selected.emit(self.ref_at(event.position().toPoint()))
        super().mousePressEvent(event)

    def contextMenuEvent(self,event):
        ref = self.ref_at(event.pos())
        if ref:
            self.selected.emit(ref); self.context.emit(ref,event.globalPos())

    def wheelEvent(self,event):
        factor = 1.15 if event.angleDelta().y() > 0 else 1/1.15
        if .05 < abs(self.transform().m11())*factor < 2000:
            self.scale(factor,factor)
        event.accept()

    def paintEvent(self,event):
        super().paintEvent(event)
        painter = QPainter(self.viewport()); painter.setPen(QColor('#374151'))
        scale = abs(self.transform().m11()); distance = 1 if scale >= 15 else 10 if scale >= 1.5 else 100
        x,y = 18,self.viewport().height()-18
        painter.drawLine(x,y,int(x+distance*scale),y); painter.drawText(x,y-7,f'{distance} mm'); painter.end()


class GroupDialog(QDialog):
    def __init__(self, board, existing=None, parent=None):
        super().__init__(parent); self.setWindowTitle('Part group'); self.resize(470,270)
        layout = QVBoxLayout(self); form = QFormLayout()
        self.name = QLineEdit((existing or {}).get('name',''))
        self.references = QPlainTextEdit(', '.join((existing or {}).get('refs',[])))
        self.references.setPlaceholderText('C107, D28, R111, T8')
        form.addRow('Name',self.name); layout.addLayout(form)
        layout.addWidget(QLabel('RefDes, separated by commas (order defines corresponding parts):'))
        layout.addWidget(self.references)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok|QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.check); buttons.rejected.connect(self.reject); layout.addWidget(buttons)
        self.board = board

    def check(self):
        try:
            names = refs(self.references.toPlainText())
            if not names:
                raise ValueError('Enter at least one RefDes.')
            missing = [r for r in names if r not in self.board.parts or self.board.parts[r].via]
            if missing:
                raise ValueError('Unknown part RefDes: '+', '.join(missing))
            self.group = {'name':self.name.text().strip() or 'Group','refs':names}; self.accept()
        except Exception as error:
            QMessageBox.warning(self,'Invalid group',str(error))


class SizeDialog(QDialog):
    def __init__(self, part, data, parent):
        super().__init__(parent); self.setWindowTitle('Component body size'); self.resize(430,280)
        self.part,self.data = part,data
        self.index,self.entry = size_match(part,data)
        form = QFormLayout(self)
        form.addRow(QLabel(f'{part.ref} — {part.name}\nPattern: {part.pattern}'))
        self.scope = QComboBox(); self.scope.addItems(['Pattern','Name','Pattern + name'])
        self.scope.setCurrentIndex(2)
        self.pattern = QLineEdit(part.pattern); self.name = QLineEdit(part.name)
        self.width = spin((self.entry or {}).get('width_mm',1),.001,10000)
        self.height = spin((self.entry or {}).get('height_mm',1),.001,10000)
        self.rotation = spin((self.entry or {}).get('rotation_deg',0),-360,360,2)
        form.addRow('Match',self.scope); form.addRow('Pattern',self.pattern); form.addRow('Name',self.name)
        form.addRow('Body width (mm)',self.width); form.addRow('Body height (mm)',self.height)
        form.addRow('Body rotation offset (°)',self.rotation)
        form.addRow(QLabel('Priority: pattern < name < pattern + name.\nEntries are shared by every layout recipe.'))
        buttons = QDialogButtonBox(QDialogButtonBox.Save|QDialogButtonBox.Cancel)
        remove = buttons.addButton('Remove current match',QDialogButtonBox.DestructiveRole)
        remove.setEnabled(self.entry is not None); remove.clicked.connect(self.remove)
        buttons.accepted.connect(self.save); buttons.rejected.connect(self.reject); form.addRow(buttons)
        self.scope.currentIndexChanged.connect(self.scope_changed); self.scope_changed()

    def scope_changed(self):
        self.pattern.setEnabled(self.scope.currentIndex() != 1)
        self.name.setEnabled(self.scope.currentIndex() != 0)

    def save(self):
        entry = {'width_mm':self.width.value(),'height_mm':self.height.value(),'rotation_deg':self.rotation.value()}
        if self.scope.currentIndex() != 1:
            entry['pattern'] = self.pattern.text().strip()
        if self.scope.currentIndex() != 0:
            entry['name'] = self.name.text().strip()
        if any(not entry.get(key,'').strip() for key in ('pattern','name') if key in entry):
            QMessageBox.warning(self,'Missing match','Enter the pattern/name to match.'); return
        data = deepcopy(self.data)
        data['sizes'] = [e for e in data['sizes'] if not (e.get('pattern') == entry.get('pattern') and e.get('name') == entry.get('name') and e.get('pattern_match','exact') == 'exact')]
        data['sizes'].append(entry)
        try:
            write_json(SIZES_PATH,data); self.accept()
        except Exception as error:
            QMessageBox.warning(self,'Cannot save sizes',str(error))

    def remove(self):
        data = deepcopy(self.data); del data['sizes'][self.index]
        try:
            write_json(SIZES_PATH,data); self.accept()
        except Exception as error:
            QMessageBox.warning(self,'Cannot save sizes',str(error))


class PreviewThread(QThread):
    ready = Signal(object,object,str)
    failed = Signal(str,str)
    progress = Signal(int,int)

    def __init__(self, board, recipe, key):
        super().__init__(); self.board,self.recipe,self.key = board,recipe,key

    def run(self):
        try:
            board,groups = replicate(self.board,self.recipe,self.isInterruptionRequested,self.progress.emit)
            self.ready.emit(board,groups,self.key)
        except InterruptedError:
            pass
        except Exception as error:
            self.failed.emit(str(error),self.key)


class Viewer(QMainWindow):
    def __init__(self,initial=None):
        super().__init__(); self.resize(1440,850)
        self.board = None; self.recipe = None; self.recipe_path = None; self.saved = ''
        self.selected = ''; self.display_board = None; self.worker = None; self.preview_board = None
        self.preview_groups = None; self.preview_key = ''; self.preview_error = ''; self.closing = False
        self.sizes = load_sizes()
        try:
            self.preferences = json.loads(SETTINGS_PATH.read_text(encoding='utf-8')) if SETTINGS_PATH.exists() else {}
        except Exception:
            self.preferences = {}
        self.preferences.setdefault('schema_version',1)
        self.timer = QTimer(self); self.timer.setSingleShot(True); self.timer.timeout.connect(self.refresh_display)
        self.build_ui(); self.create_menus(); self.title()
        if self.preferences.get('geometry'):
            self.restoreGeometry(QByteArray.fromHex(self.preferences['geometry'].encode()))
        if self.preferences.get('splitter'):
            self.splitter.restoreState(QByteArray.fromHex(self.preferences['splitter'].encode()))
        if initial:
            QTimer.singleShot(0,lambda:self.open_path(Path(initial)))

    def build_ui(self):
        root = QWidget(); outer = QVBoxLayout(root); self.setCentralWidget(root)
        toolbar = QHBoxLayout(); self.open_button = QPushButton('Open PCB / recipe…'); self.open_button.clicked.connect(self.open_dialog)
        toolbar.addWidget(self.open_button)
        fit = QPushButton('Fit'); fit.clicked.connect(self.fit); toolbar.addWidget(fit)
        self.show_group = QComboBox(); self.show_group.addItem('All groups'); self.show_group.currentIndexChanged.connect(self.choose_group)
        toolbar.addWidget(self.show_group)
        self.checks = {}
        for key,label in [('labels','Labels'),('pads','Pads'),('tracks','Tracks'),('vias','Vias')]:
            box = QCheckBox(label); box.setChecked(key == 'labels'); box.toggled.connect(self.change)
            self.checks[key] = box; toolbar.addWidget(box)
        toolbar.addStretch(); self.preview = QCheckBox('Preview'); self.preview.hide(); self.preview.toggled.connect(self.change)
        toolbar.addWidget(self.preview); outer.addLayout(toolbar)
        self.splitter = QSplitter(); sidebar = QWidget(); side = QVBoxLayout(sidebar)
        side.addWidget(QLabel('Part groups'))
        self.group_list = QListWidget(); self.group_list.itemDoubleClicked.connect(lambda _:self.edit_group())
        self.group_list.setContextMenuPolicy(Qt.CustomContextMenu); self.group_list.customContextMenuRequested.connect(self.group_context)
        self.group_list.setMaximumHeight(225); side.addWidget(self.group_list)
        row = QHBoxLayout()
        for label,callback in [('Add…',self.add_group),('Edit…',self.edit_group),('Remove',self.remove_group)]:
            button = QPushButton(label); button.clicked.connect(callback); row.addWidget(button)
        side.addLayout(row)
        self.layers_panel = QGroupBox('Internal copper'); layers_layout = QVBoxLayout(self.layers_panel)
        row = QHBoxLayout(); row.addWidget(QLabel('Show routing for'))
        self.scope = QComboBox(); self.scope.addItems(['All','Source only']); self.scope.currentTextChanged.connect(self.change); row.addWidget(self.scope)
        layers_layout.addLayout(row); self.layer_widgets = {}; self.layers_form = QVBoxLayout(); layers_layout.addLayout(self.layers_form)
        note = QLabel('Only connections between parts in a group.\nUnchecked layers are excluded from copies.'); note.setWordWrap(True); layers_layout.addWidget(note)
        self.layers_panel.hide(); side.addWidget(self.layers_panel)
        self.rep_toggle = QToolButton(); self.rep_toggle.setText('Replication'); self.rep_toggle.setCheckable(True)
        self.rep_toggle.setToolButtonStyle(Qt.ToolButtonTextBesideIcon); self.rep_toggle.setArrowType(Qt.RightArrow)
        self.rep_toggle.toggled.connect(self.toggle_replication); side.addWidget(self.rep_toggle)
        self.rep_panel = QWidget(); rep = QFormLayout(self.rep_panel)
        self.rep_fields = {}
        for key,labels in [('mode',['Create copies','Arrange existing groups']),('source',[]),('layout',['Linear','Grid','Rotational'])]:
            box = QComboBox(); box.addItems(labels); box.currentIndexChanged.connect(self.change)
            self.rep_fields[key] = box; rep.addRow({'mode':'Action','source':'Source group','layout':'Arrangement'}[key],box)
        self.rep_count = QSpinBox(); self.rep_count.setRange(2,1000); self.rep_count.setValue(2); self.rep_count.valueChanged.connect(self.change)
        self.count_label = QLabel('Total instances'); rep.addRow(self.count_label,self.rep_count)
        self.arrangements = QStackedWidget(); self.inputs = {}
        for kind,fields in [('Linear',[('dx','X step (mm)',7.62),('dy','Y step (mm)',0)]),('Grid',[('columns','Columns',2),('rows','Rows',2),('column_dx','Column X step (mm)',7.62),('column_dy','Column Y step (mm)',0),('row_dx','Row X step (mm)',0),('row_dy','Row Y step (mm)',7.62)]),('Rotational',[('center_x','Center X (mm)',0),('center_y','Center Y (mm)',0),('angle_deg','Angle step (°)',45)])]:
            page = QWidget(); form = QFormLayout(page)
            for key,label,default in fields:
                if key in ('columns','rows'):
                    box = QSpinBox(); box.setRange(1,1000); box.setValue(default)
                else:
                    box = spin(default)
                self.inputs[key] = box; box.valueChanged.connect(self.change); form.addRow(label,box)
            if kind == 'Rotational':
                self.auto_angle = QCheckBox('Evenly around 360°'); self.auto_angle.setChecked(True); self.auto_angle.toggled.connect(self.change); form.addRow(self.auto_angle)
            self.arrangements.addWidget(page)
        rep.addRow(self.arrangements)
        self.rep_note = QLabel(); self.rep_note.setWordWrap(True); rep.addRow(self.rep_note)
        self.export_button = QPushButton('Save replicated PCB as…'); self.export_button.clicked.connect(self.export); self.export_button.setEnabled(False); rep.addRow(self.export_button)
        self.rep_panel.hide(); side.addWidget(self.rep_panel)
        side.addStretch()
        scroll = QScrollArea(); scroll.setWidgetResizable(True); scroll.setWidget(sidebar); scroll.setMinimumWidth(290)
        self.canvas = Canvas(); self.canvas.selected.connect(self.select); self.canvas.context.connect(self.part_context)
        self.splitter.addWidget(scroll); self.splitter.addWidget(self.canvas); self.splitter.setSizes([310,1100]); outer.addWidget(self.splitter,1)
        self.progress = QProgressBar(); self.progress.hide(); outer.addWidget(self.progress)
        self.summary = QLabel('Open a DipTrace PCB XML or a saved layout recipe.'); outer.addWidget(self.summary)
        self.inspector = QLabel('Mouse wheel: zoom · Drag: pan · Click a part: inspect · Right-click: body size'); self.inspector.setWordWrap(True); outer.addWidget(self.inspector)

    def create_menus(self):
        def action(menu,label,callback,shortcut=None):
            a = menu.addAction(label); a.triggered.connect(callback)
            if shortcut:
                a.setShortcut(shortcut)
            return a
        file = self.menuBar().addMenu('&File')
        action(file,'Open PCB / recipe…',self.open_dialog,'Ctrl+O')
        self.recent_menu = file.addMenu('Recent files'); self.recent_menu.aboutToShow.connect(self.recent)
        action(file,'Reload',self.reload,'Ctrl+R'); file.addSeparator()
        action(file,'Save recipe',self.save,'Ctrl+S'); action(file,'Save recipe as…',self.save_as,'Ctrl+Shift+S')
        action(file,'Save replicated PCB as…',self.export); file.addSeparator(); action(file,'Exit',self.close,'Alt+F4')
        edit = self.menuBar().addMenu('&Edit'); action(edit,'Add group…',self.add_group)
        action(edit,'Edit selected group…',self.edit_group); action(edit,'Remove selected group',self.remove_group)
        edit.addSeparator(); action(edit,'Copy selected RefDes',self.copy_selected,'Ctrl+C')
        action(edit,'Define selected body size…',lambda:self.edit_size(self.selected))
        action(edit,'Fit',self.fit,'F'); action(edit,'Body rotation correction…',self.rotation_correction)
        tools = self.menuBar().addMenu('&Tools'); self.rep_action = action(tools,'Replication settings',lambda:self.rep_toggle.setChecked(not self.rep_toggle.isChecked()))
        self.rep_action.setCheckable(True); tools.addSeparator()
        action(tools,'Settings…',self.settings_dialog); action(tools,'Open program settings JSON',lambda:self.open_local(SETTINGS_PATH))
        action(tools,'Open recipe JSON',lambda:self.open_local(self.recipe_path) if self.recipe_path else self.save_as())
        action(tools,'Open new-recipe defaults',lambda:self.open_local(DEFAULTS_PATH))
        action(tools,'Open component sizes',lambda:self.open_local(SIZES_PATH)); action(tools,'Reload component sizes',self.reload_sizes)
        help_menu = self.menuBar().addMenu('&Help'); action(help_menu,'How to use',lambda:self.open_local(APP_DIR/'HELP.txt'))

    def open_local(self,path):
        try:
            if path == SETTINGS_PATH:
                self.save_preferences()
            open_file(path)
        except Exception as error:
            self.error(error)

    def error(self,error):
        QMessageBox.warning(self,'PCB Layout',str(error))

    def title(self):
        name = self.recipe_path.name if self.recipe_path else self.board.path.name if self.board else ''
        self.setWindowTitle('PCB Layout'+(' — '+name if name else '')+(' *' if self.dirty() else ''))

    def dirty(self):
        return self.recipe is not None and json.dumps(self.recipe,sort_keys=True) != self.saved

    def may_discard(self):
        if not self.dirty():
            return True
        answer = QMessageBox.question(self,'Save recipe?','Save changes to this layout recipe?',QMessageBox.Save|QMessageBox.Discard|QMessageBox.Cancel)
        return self.save() if answer == QMessageBox.Save else answer == QMessageBox.Discard

    def open_dialog(self):
        filename,_ = QFileDialog.getOpenFileName(self,'Open PCB / recipe',self.preferences.get('last_directory',''),'PCB / recipe (*.dipxml *.json);;All files (*)')
        if filename:
            self.open_path(Path(filename))

    def open_path(self,path,ask=True):
        if ask and not self.may_discard():
            return
        try:
            if path.suffix.casefold() == '.json':
                recipe = json.loads(path.read_text(encoding='utf-8-sig'))
                if recipe.get('schema_version') != 1 or not isinstance(recipe.get('pcb'),str):
                    raise ValueError('Not a PCB Layout recipe JSON.')
                source = Path(recipe['pcb']); source = source if source.is_absolute() else path.parent/source
                board = Board(source)
                if not all(key in recipe for key in ('groups','view','replication','clearance_mm','rotation_offsets')):
                    raise ValueError('Recipe is missing its group, view or replication settings.')
                parsed = group_refs(board,recipe)
                for i,group in enumerate(recipe['groups']):
                    group['refs'] = parsed[i]; group.setdefault('name',f'Group {i+1}')
                recipe_path = path.resolve()
            else:
                board = Board(path); recipe = default_recipe(board); recipe_path = None
            self.board,self.recipe,self.recipe_path = board,recipe,recipe_path
            self.preview_board = None; self.preview_key = ''; self.selected = ''; self.preview_error = ''
            self.saved = json.dumps(recipe,sort_keys=True) if recipe_path else ''
            self.title()
            self.populate(); self.remember(path); self.refresh_display(); self.fit()
            if recipe_path is None:
                self.save_as()
        except Exception as error:
            self.error(error)

    def remember(self,path):
        path = str(path.resolve()); self.preferences['recent'] = [path]+[p for p in self.preferences.get('recent',[]) if p != path][:9]
        self.preferences['last_directory'] = str(Path(path).parent); self.save_preferences()

    def recent(self):
        self.recent_menu.clear()
        for path in self.preferences.get('recent',[]):
            a = self.recent_menu.addAction(path); a.triggered.connect(lambda checked=False,p=path:self.open_path(Path(p)))

    def populate(self):
        self.loading = True
        try:
            self.group_list.clear(); self.rep_fields['source'].clear(); self.show_group.clear(); self.show_group.addItem('All groups')
            for i,g in enumerate(self.recipe['groups']):
                item = QListWidgetItem(f"{g['name']}  ({len(g['refs'])} parts)"); item.setForeground(color(i).darker(180)); self.group_list.addItem(item)
                self.rep_fields['source'].addItem(g['name']); self.show_group.addItem(g['name'])
            if not self.recipe['groups']:
                self.group_list.addItem('No groups — whole PCB'); self.rep_fields['source'].addItem('Whole PCB')
            self.show_group.setCurrentIndex(self.recipe['view'].get('display_group',0))
            for key,box in self.checks.items():
                box.setChecked(self.recipe['view'][key])
            self.scope.setCurrentText(self.recipe['view']['routing_scope']); self.preview.setChecked(self.recipe['view']['preview'])
            while self.layers_form.count():
                item = self.layers_form.takeAt(0)
                if item.widget():
                    item.widget().deleteLater()
            self.layer_widgets = {}
            for key,label in self.board.layers.items():
                box = QCheckBox(label); box.setChecked(key in self.recipe['view']['layers']); box.toggled.connect(self.change)
                self.layer_widgets[key] = box; self.layers_form.addWidget(box)
            settings = self.recipe['replication']
            for key,box in self.rep_fields.items():
                box.setCurrentIndex(settings[key]) if key == 'source' else box.setCurrentText(settings[key])
            self.rep_count.setValue(settings['count']); self.auto_angle.setChecked(settings['automatic_angle'])
            for key,box in self.inputs.items():
                box.setValue(settings[key])
            self.rep_toggle.setChecked(self.preferences.get('replication_open',False) or self.preview.isChecked())
        finally:
            self.loading = False
        self.update_controls()

    def update_controls(self):
        self.layers_panel.setVisible(self.checks['tracks'].isChecked() or self.checks['vias'].isChecked())
        self.arrangements.setCurrentIndex(self.rep_fields['layout'].currentIndex())
        create = self.rep_fields['mode'].currentIndex() == 0
        grid = self.rep_fields['layout'].currentText() == 'Grid'
        self.count_label.setVisible(create and not grid); self.rep_count.setVisible(create and not grid)
        self.inputs['angle_deg'].setEnabled(not self.auto_angle.isChecked())
        self.rep_note.setText('The source is instance 1. Copies get new RefDes and internal nets.' if create else 'Count comes from your groups. Parts correspond by list order; destination nets are preserved.')

    def toggle_replication(self,opened):
        self.rep_toggle.setArrowType(Qt.DownArrow if opened else Qt.RightArrow); self.rep_panel.setVisible(opened)
        self.preview.setVisible(opened); self.rep_action.setChecked(opened) if hasattr(self,'rep_action') else None
        self.preferences['replication_open'] = opened
        if not opened and self.preview.isChecked():
            self.preview.setChecked(False)
        self.change()

    def change(self,*args):
        if not self.recipe or getattr(self,'loading',False):
            return
        view = self.recipe['view']
        view.update({key:box.isChecked() for key,box in self.checks.items()})
        view.update(preview=self.preview.isChecked(),routing_scope=self.scope.currentText(),layers=[key for key,box in self.layer_widgets.items() if box.isChecked()])
        settings = self.recipe['replication']
        settings.update({key:box.currentIndex() if key == 'source' else box.currentText() for key,box in self.rep_fields.items()})
        settings.update(count=self.rep_count.value(),automatic_angle=self.auto_angle.isChecked())
        settings.update({key:box.value() for key,box in self.inputs.items()})
        self.update_controls(); self.title(); self.timer.start(180)

    def key(self):
        return self.board.fingerprint+json.dumps({'groups':self.recipe['groups'],'replication':self.recipe['replication'],'copper':{k:self.recipe['view'][k] for k in ('tracks','vias','layers')}},sort_keys=True)

    def choose_group(self,index):
        if self.recipe and not getattr(self,'loading',False):
            self.recipe['view']['display_group'] = index; self.title(); self.redraw()

    def refresh_display(self):
        if not self.board:
            return
        self.export_button.setEnabled(False)
        if not self.preview.isChecked():
            if self.worker:
                self.worker.requestInterruption()
            self.redraw(); return
        if self.preview_board is not None and self.preview_key == self.key():
            self.redraw(); self.export_button.setEnabled(True); return
        if self.worker:
            self.worker.requestInterruption(); return
        self.preview_error = ''; self.progress.setRange(0,0); self.progress.show()
        self.summary.setText('Calculating replication preview…')
        self.worker = PreviewThread(self.board,deepcopy(self.recipe),self.key())
        self.worker.ready.connect(self.preview_ready); self.worker.failed.connect(self.preview_failed)
        self.worker.progress.connect(self.preview_progress); self.worker.finished.connect(self.preview_finished); self.worker.start()

    def preview_progress(self,current,total):
        self.progress.setRange(0,total); self.progress.setValue(current)

    def preview_ready(self,board,groups,key):
        if self.preview.isChecked() and key == self.key():
            self.preview_board,self.preview_groups,self.preview_key = board,groups,key

    def preview_failed(self,message,key):
        if key == self.key():
            self.preview_error = message

    def preview_finished(self):
        old = self.worker; self.worker = None; old.deleteLater(); self.progress.hide()
        if self.closing:
            self.close(); return
        if self.preview_error:
            self.canvas.scene().clear(); self.summary.setText(self.preview_error); self.inspector.setText('Correct the replication settings to preview.'); return
        self.refresh_display()
        if self.preview_key == self.key():
            self.fit()

    def redraw(self,*args):
        if not self.board:
            return
        try:
            use_preview = self.preview.isChecked() and self.preview_board is not None and self.preview_key == self.key()
            if self.preview.isChecked() and not use_preview:
                return
            board = self.preview_board if use_preview else self.board
            groups = [g['refs'] for g in self.preview_groups] if use_preview else group_refs(board,self.recipe)
            if not use_preview and self.rep_toggle.isChecked() and self.recipe['replication']['mode'] == 'Create copies':
                source = self.recipe['replication']['source']; groups = [groups[source]]
            names = [g['name'] for g in self.preview_groups] if use_preview else [g['name'] for g in self.recipe['groups']] or ['Whole PCB']
            if not use_preview and self.rep_toggle.isChecked() and self.recipe['replication']['mode'] == 'Create copies':
                names = [names[self.recipe['replication']['source']]]
            current = self.show_group.currentIndex()
            self.show_group.blockSignals(True); self.show_group.clear(); self.show_group.addItems(['All groups']+names)
            self.show_group.setCurrentIndex(max(0,min(current,len(names)))); self.show_group.blockSignals(False)
            self.display_board = board; self.display_groups = groups
            scene = self.canvas.scene(); scene.clear(); self.body_items = {}
            geometries = {}; origins = {}; collisions = {}
            extra = dict(self.recipe['rotation_offsets'])
            if use_preview and self.recipe['replication']['mode'] == 'Create copies':
                for group in groups[1:]:
                    for a,b in zip(groups[0],group):
                        extra[b] = extra.get(a,0)
            for g in groups:
                for ref in g:
                    part = board.parts[ref]; paths,poly,origin,index = board.geometry(part,self.sizes,extra.get(ref,0))
                    geometries[ref] = paths,poly; origins[ref] = origin
            # Check every displayed plan group, including boundaries between groups.
            ordered = sorted([(min(x for x,y in poly),max(x for x,y in poly),ref,poly) for ref,(paths,poly) in geometries.items() if poly])
            active = []; pairs = 0; gap_limit = self.recipe['clearance_mm']
            for low,high,ref,poly in ordered:
                active = [row for row in active if row[1]+gap_limit > low]
                for _,_,other,p2 in active:
                    if board.parts[ref].side != board.parts[other].side:
                        continue
                    if max(y for x,y in poly)+gap_limit <= min(y for x,y in p2) or max(y for x,y in p2)+gap_limit <= min(y for x,y in poly):
                        continue
                    gap = polygon_gap(poly,p2)
                    if gap < gap_limit-1e-9:
                        collisions.setdefault(ref,[]).append((other,gap)); collisions.setdefault(other,[]).append((ref,gap)); pairs += 1
                active.append((low,high,ref,poly))
            self.collisions,self.origins = collisions,origins
            selected_group = self.show_group.currentIndex()-1
            tracks_count = vias_count = 0
            for index,group in enumerate(groups):
                if selected_group >= 0 and selected_group != index:
                    continue
                tint = color(index)
                source_group_index = 0 if len(groups) == 1 or (use_preview and self.recipe['replication']['mode'] == 'Create copies') else self.recipe['replication']['source']
                draw_routes = self.scope.currentText() == 'All' or index == source_group_index
                if draw_routes and (self.checks['tracks'].isChecked() or self.checks['vias'].isChecked()):
                    route = board.replication_routing[index] if use_preview else board.routing(group,self.checks['tracks'].isChecked(),self.checks['vias'].isChecked(),set(self.recipe['view']['layers']))
                    if self.checks['tracks'].isChecked():
                        for key,edges in route.segments.items():
                            points = board.traces[key].findall('./Points/Point'); tracks_count += len(intervals(edges))
                            for i in edges:
                                p,q = points[i:i+2]
                                path = QPainterPath(); path.moveTo(float(p.get('X'))*board.factor,float(p.get('Y'))*board.factor)
                                path.lineTo(float(q.get('X'))*board.factor,float(q.get('Y'))*board.factor)
                                pen = QPen(tint.darker(155)); pen.setWidthF(max(.03,float(q.get('Width',1))*board.factor))
                                scene.addPath(path,pen).setZValue(-2)
                    if self.checks['vias'].isChecked():
                        locations = []
                        for vid in route.vias:
                            part = board.by_id[vid]; locations.append((part.x,part.y,board.via_sizes.get(part.element.get('ViaStyle'),.5)))
                        for nid,tid,i in route.embedded:
                            p = board.traces[nid,tid].findall('./Points/Point')[i]
                            locations.append((float(p.get('X'))*board.factor,float(p.get('Y'))*board.factor,board.via_sizes.get(p.get('ViaStyle'),.5)))
                        for x,y,size in locations:
                            scene.addEllipse(x-size/2,y-size/2,size,size,QPen(tint.darker(170),.025),QBrush(tint)).setZValue(-1)
                        vias_count += len(locations)
                for ref in group:
                    part = board.parts[ref]; paths,poly = geometries[ref]
                    tint_body = QColor('#ff6969') if ref in collisions else tint
                    pen = QPen(QColor('#111111' if ref == self.selected else '#374151')); pen.setWidth(3 if ref == self.selected else 1); pen.setCosmetic(True)
                    if poly:
                        body = scene.addPolygon(QPolygonF([QPointF(*p) for p in poly]),pen,QBrush(tint_body) if origins[ref] == 'Size database' else QBrush(QColor(tint_body.red(),tint_body.green(),tint_body.blue(),30)))
                        if origins[ref] != 'Size database':
                            body.setPen(pen if ref == self.selected else QPen(Qt.NoPen))
                            for points,closed in paths:
                                path = QPainterPath(); path.moveTo(*points[0])
                                for point in points[1:]:
                                    path.lineTo(*point)
                                if closed:
                                    path.closeSubpath()
                                child = scene.addPath(path,QPen(tint_body.darker(170),.025)); child.setData(0,ref); child.setZValue(1)
                    else:
                        body = scene.addEllipse(part.x-.25,part.y-.25,.5,.5,pen,QBrush(QColor('#ffcf75')))
                    body.setData(0,ref); body.setZValue(0); self.body_items[ref] = body
                    body.setToolTip(f'{ref} · {part.name}\n{part.pattern}\n{origins[ref]}\nX {part.x:.3f}, Y {part.y:.3f} mm')
                    if self.checks['pads'].isChecked():
                        for pid,points in board.pad_polygons(part):
                            item = scene.addPolygon(QPolygonF([QPointF(*p) for p in points]),QPen(QColor('#8b6600'),.015),QBrush(QColor('#d6aa43'))); item.setData(0,ref); item.setZValue(2); item.setToolTip(f'{ref} pad {pid}')
                    if self.checks['labels'].isChecked():
                        text = scene.addSimpleText(ref,QFont('Segoe UI' if sys.platform == 'win32' else '',9)); text.setPos(part.x,part.y)
                        text.setFlag(QGraphicsItem.ItemIgnoresTransformations); text.setData(0,ref); text.setZValue(4)
                        center = text.boundingRect().center()
                        text.setTransform(QTransform().translate(-center.x(),-center.y()))
            no_geometry = sum(o == 'No body geometry' for o in origins.values()); fallback = sum(o != 'Size database' for o in origins.values())
            total_parts = sum(len(g) for g in groups)
            visible_parts = sum(len(g) for i,g in enumerate(groups) if selected_group < 0 or i == selected_group)
            count_text = str(total_parts) if visible_parts == total_parts else f'{visible_parts} / {total_parts}'
            self.summary.setText(f"{'Preview' if use_preview else 'Current PCB'} · {count_text} parts · {pairs} pairs below {gap_limit:g} mm · {fallback} outline/silk fallbacks"+ (f' · {no_geometry} missing geometry' if no_geometry else '')+(f' · {tracks_count} trace spans, {vias_count} vias' if tracks_count or vias_count else ''))
            self.inspect()
            if not scene.items():
                self.summary.setText('No parts in the selected display group.')
        except Exception as error:
            self.summary.setText(str(error)); self.export_button.setEnabled(False)

    def fit(self):
        if not self.canvas.scene().items():
            return
        view = self.recipe['view'] if self.recipe else {}
        sign = -1 if view.get('rotate_workspace',True) else 1
        self.canvas.setTransform(QTransform().scale(sign,sign*(1 if view.get('y_down',True) else -1)))
        bounds = QRectF()
        for item in self.canvas.scene().items():
            if not item.flags() & QGraphicsItem.ItemIgnoresTransformations:
                bounds = bounds.united(item.sceneBoundingRect())
        bounds = bounds.adjusted(-1,-1,1,1)
        self.canvas.scene().setSceneRect(bounds); self.canvas.fitInView(bounds,Qt.KeepAspectRatio)

    def select(self,ref):
        old = self.selected; self.selected = ref
        for key in (old,ref):
            if key in getattr(self,'body_items',{}):
                pen = QPen(QColor('#111111' if key == ref else '#374151')); pen.setWidth(3 if key == ref else 1); pen.setCosmetic(True)
                self.body_items[key].setPen(pen)
        self.inspect()

    def inspect(self):
        if not self.selected or self.display_board is None or self.selected not in self.display_board.parts:
            self.inspector.setText('Mouse wheel: zoom · Drag: pan · Click a part: inspect · Right-click: body size'); return
        part = self.display_board.parts[self.selected]
        _,entry = size_match(part,self.sizes)
        dimensions = f" · {entry['width_mm']:g} × {entry['height_mm']:g} mm" if entry else ''
        neighbors = ', '.join(f'{other} ({gap:.3f} mm)' for other,gap in self.collisions.get(part.ref,[]))
        self.inspector.setText(f'{part.ref} · {part.name} · {part.pattern} · {self.origins.get(part.ref,"")}{dimensions} · X {part.x:.3f}, Y {part.y:.3f} mm'+(' · Close: '+neighbors if neighbors else ''))

    def part_context(self,ref,pos):
        menu = QMenu(self); menu.addAction('Define body size…',lambda:self.edit_size(ref))
        menu.addAction('Body rotation correction…',self.rotation_correction); menu.addSeparator()
        menu.addAction('Copy RefDes',self.copy_selected)
        p = self.display_board.parts[ref]
        menu.addAction('Copy name',lambda:QApplication.clipboard().setText(p.name)); menu.addAction('Copy pattern',lambda:QApplication.clipboard().setText(p.pattern)); menu.exec(pos)

    def copy_selected(self):
        if self.selected:
            QApplication.clipboard().setText(self.selected)

    def edit_size(self,ref):
        if not ref or self.display_board is None or ref not in self.display_board.parts:
            return
        if SizeDialog(self.display_board.parts[ref],self.sizes,self).exec():
            self.reload_sizes()

    def reload_sizes(self):
        try:
            self.sizes = load_sizes(); self.redraw()
        except Exception as error:
            self.error(error)

    def rotation_correction(self):
        if not self.selected or not self.recipe:
            return
        value,ok = QInputDialog.getDouble(self,'Body rotation correction','Viewer-only offset (degrees)',self.recipe['rotation_offsets'].get(self.selected,0),-360,360,2)
        if ok:
            self.recipe['rotation_offsets'][self.selected] = value; self.title(); self.redraw()

    def group_context(self,pos):
        if self.group_list.itemAt(pos):
            self.group_list.setCurrentItem(self.group_list.itemAt(pos))
        menu = QMenu(self); menu.addAction('Add group…',self.add_group); menu.addAction('Edit group…',self.edit_group); menu.addAction('Remove group',self.remove_group); menu.exec(self.group_list.mapToGlobal(pos))

    def add_group(self):
        if not self.board:
            return
        dialog = GroupDialog(self.board,parent=self)
        if dialog.exec():
            candidate = deepcopy(self.recipe); candidate['groups'].append(dialog.group)
            self.set_groups(candidate)

    def edit_group(self):
        if not self.board or not self.recipe['groups']:
            return
        row = self.group_list.currentRow()
        if row < 0:
            return
        dialog = GroupDialog(self.board,self.recipe['groups'][row],self)
        if dialog.exec():
            candidate = deepcopy(self.recipe); candidate['groups'][row] = dialog.group; self.set_groups(candidate)

    def remove_group(self):
        if self.recipe and self.recipe['groups'] and self.group_list.currentRow() >= 0:
            candidate = deepcopy(self.recipe); del candidate['groups'][self.group_list.currentRow()]; candidate['replication']['source'] = 0; self.set_groups(candidate)

    def set_groups(self,candidate):
        try:
            group_refs(self.board,candidate); self.recipe = candidate; self.populate(); self.title(); self.refresh_display(); self.fit()
        except Exception as error:
            self.error(error)

    def settings_dialog(self):
        if not self.recipe:
            return
        dialog = QDialog(self); dialog.setWindowTitle('Settings'); form = QFormLayout(dialog)
        gap = spin(self.recipe['clearance_mm'],0,100,3); form.addRow('Minimum body gap (mm)',gap)
        down = QCheckBox('Y increases downward'); down.setChecked(self.recipe['view']['y_down']); form.addRow(down)
        rotated = QCheckBox('Rotate workspace 180°'); rotated.setChecked(self.recipe['view']['rotate_workspace']); form.addRow(rotated)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok|QDialogButtonBox.Cancel); buttons.accepted.connect(dialog.accept); buttons.rejected.connect(dialog.reject); form.addRow(buttons)
        if dialog.exec():
            self.recipe['clearance_mm'] = gap.value(); self.recipe['view'].update(y_down=down.isChecked(),rotate_workspace=rotated.isChecked()); self.title(); self.redraw(); self.fit()

    def save(self):
        if not self.recipe:
            return False
        if self.recipe_path is None:
            return self.save_as()
        try:
            data = deepcopy(self.recipe)
            import os
            try:
                data['pcb'] = os.path.relpath(self.board.path,self.recipe_path.parent)
            except ValueError:
                data['pcb'] = str(self.board.path)  # Different Windows drive.
            write_json(self.recipe_path,data); self.saved = json.dumps(self.recipe,sort_keys=True); self.title(); self.remember(self.recipe_path); return True
        except Exception as error:
            self.error(error); return False

    def save_as(self):
        if not self.recipe:
            return False
        proposed = self.recipe_path or self.board.path.with_suffix('.pcb_layout.json')
        filename,_ = QFileDialog.getSaveFileName(self,'Save layout recipe as',str(proposed),'Layout recipe (*.json)')
        if not filename:
            return False
        destination = Path(filename).resolve()
        if destination.suffix.casefold() != '.json':
            destination = destination.with_name(destination.name+'.json')
        # Never replace another tool's sidecar with a layout recipe.
        if destination.exists():
            try:
                existing = json.loads(destination.read_text(encoding='utf-8-sig'))
                if not isinstance(existing.get('pcb'),str) or not all(k in existing for k in ('groups','view','replication')):
                    raise ValueError('This JSON belongs to another program. Choose a different filename.')
            except Exception as error:
                self.error(error); return False
        old = self.recipe_path; self.recipe_path = destination
        if self.save():
            return True
        self.recipe_path = old; return False

    def reload(self):
        if not self.board or not self.may_discard():
            return
        if self.recipe_path:
            self.open_path(self.recipe_path,ask=False)
        else:
            try:
                self.board = Board(self.board.path); self.preview_board = None; self.preview_key = ''; self.populate(); self.refresh_display(); self.fit()
            except Exception as error:
                self.error(error)

    def export(self):
        if not self.recipe:
            return
        if not self.rep_toggle.isChecked():
            self.rep_toggle.setChecked(True)
        if not self.preview.isChecked() or self.preview_board is None or self.preview_key != self.key():
            self.preview.setChecked(True); self.refresh_display(); self.statusBar().showMessage('Review the preview, then choose Save replicated PCB as…',6000); return
        filename,_ = QFileDialog.getSaveFileName(self,'Save replicated PCB as',str(self.board.path.with_name(self.board.path.stem+'_replicated.dipxml')),'DipTrace PCB XML (*.dipxml)')
        if not filename:
            return
        try:
            if self.board.path.read_bytes() != self.board.data:
                raise ValueError('The source PCB changed on disk. Reload before replicating.')
            output = Path(filename).resolve()
            if output.suffix.casefold() != '.dipxml':
                output = output.with_name(output.name+'.dipxml')
            data = ET.tostring(self.preview_board.root,encoding='utf-8',xml_declaration=True)
            if output == self.board.path:
                backup = output.with_name(output.name+'.'+datetime.now().strftime('%Y%m%d-%H%M%S-%f')+'.bak'); backup.write_bytes(self.board.data)
            temp = output.with_name(output.name+'.tmp'); temp.write_bytes(data); temp.replace(output)
            old_groups = self.preview_groups; old_source = old_groups[0]['refs']; offsets = dict(self.recipe['rotation_offsets'])
            if self.recipe['replication']['mode'] == 'Create copies':
                for group in old_groups[1:]:
                    for a,b in zip(old_source,group['refs']):
                        if a in offsets:
                            offsets[b] = offsets[a]
            self.board = Board(output); self.recipe['pcb'] = str(output); self.recipe['groups'] = old_groups; self.recipe['rotation_offsets'] = offsets
            source_index = 0 if self.recipe['replication']['mode'] == 'Create copies' else self.recipe['replication']['source']
            self.recipe['view']['preview'] = False; self.recipe['replication'].update(mode='Arrange existing groups',source=source_index)
            self.preview_board = None; self.preview_key = ''; self.populate(); self.refresh_display(); self.fit(); self.title()
            # The filled groups are part of the recipe and immediately reusable.
            self.save()
            self.statusBar().showMessage('PCB saved. Re-import the XML in DipTrace and run its checks.',10000)
        except Exception as error:
            self.error(error)

    def save_preferences(self):
        self.preferences['geometry'] = bytes(self.saveGeometry().toHex()).decode()
        self.preferences['splitter'] = bytes(self.splitter.saveState().toHex()).decode()
        write_json(SETTINGS_PATH,self.preferences)

    def closeEvent(self,event):
        if self.worker:
            self.closing = True; self.worker.requestInterruption(); event.ignore(); return
        if not self.may_discard():
            self.closing = False; event.ignore(); return
        try:
            self.save_preferences()
        except Exception as error:
            self.error(error)
        event.accept()


def main():
    import argparse
    parser = argparse.ArgumentParser(description=__doc__); parser.add_argument('pcb_or_recipe',nargs='?'); args = parser.parse_args()
    app = QApplication(sys.argv[:1]); app.setStyle('Fusion')
    if sys.platform == 'win32':
        app.setFont(QFont('Segoe UI',9))
    window = Viewer(args.pcb_or_recipe); window.show(); return app.exec()


if __name__ == '__main__':
    raise SystemExit(main())
