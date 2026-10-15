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
                         replicate, intervals, write_json, migrate_view, migrate_replication)
from lxml import etree as ET
from PySide6.QtCore import Qt, Signal, QThread, QTimer, QByteArray, QRectF, QEvent
from PySide6.QtGui import QAction, QColor, QPen, QBrush, QPainterPath, QPolygonF, QTransform, QFont, QKeySequence, QPainter
from PySide6.QtCore import QPointF
from PySide6.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QFormLayout, QLabel, QPushButton, QToolButton, QCheckBox, QComboBox, QSpinBox,
    QDoubleSpinBox, QListWidget, QListWidgetItem, QGraphicsView, QGraphicsScene,
    QGraphicsItem, QSplitter, QFileDialog, QMessageBox, QMenu, QDialog,
    QDialogButtonBox, QLineEdit, QPlainTextEdit, QGroupBox, QStackedWidget, QProgressBar,
    QScrollArea, QInputDialog, QRadioButton, QButtonGroup, QTextEdit, QAbstractSpinBox,
    QGraphicsItemGroup, QGraphicsOpacityEffect)


GROUP_COLORS = ('#9dd9f3', '#b6e3a4', '#f4cf8c', '#d0b8ed',
                '#f5b5c6', '#98dfd3', '#c5d2f2', '#e5d99a')
COPPER_COLOR = '#d6aa43'


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
        self.body_items = {}

    def ref_at(self,pos):
        scene_pos = self.mapToScene(pos)
        for ref,item in reversed(list(self.body_items.items())):
            point = item.mapFromScene(scene_pos)
            if hasattr(item,'polygon'):
                inside = item.polygon().containsPoint(point,Qt.OddEvenFill)
            else:
                rect = item.rect()
                inside = ((point.x()-rect.center().x())/(rect.width()/2))**2+((point.y()-rect.center().y())/(rect.height()/2))**2 <= 1
            if inside:
                return ref
        for item in self.items(pos):
            if item.data(1) == 'body':
                continue
            if item.data(0):
                return item.data(0)
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
        super().__init__(parent); self.setWindowTitle('Component body size'); self.resize(470,390)
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
        self.origin_x = spin((self.entry or {}).get('origin_x_mm',0),-10000,10000,6)
        self.origin_y = spin((self.entry or {}).get('origin_y_mm',0),-10000,10000,6)
        form.addRow('Match',self.scope); form.addRow('Pattern',self.pattern); form.addRow('Name',self.name)
        form.addRow('Body width (mm)',self.width); form.addRow('Body height (mm)',self.height)
        form.addRow('Body rotation offset (°)',self.rotation)
        form.addRow('Body offset X (mm)',self.origin_x); form.addRow('Body offset Y (mm)',self.origin_y)
        use_origin = QPushButton('Reset body offset'); use_origin.clicked.connect(self.use_footprint_origin)
        form.addRow(use_origin)
        form.addRow(QLabel('Footprint origin is (0, 0). Body offsets are added\nbefore component mirroring/rotation.'))
        form.addRow(QLabel('Priority: pattern < name < pattern + name.\nEntries are shared by every layout recipe.'))
        buttons = QDialogButtonBox(QDialogButtonBox.Save|QDialogButtonBox.Cancel)
        remove = buttons.addButton('Remove current match',QDialogButtonBox.DestructiveRole)
        remove.setEnabled(self.entry is not None); remove.clicked.connect(self.remove)
        buttons.accepted.connect(self.save); buttons.rejected.connect(self.reject); form.addRow(buttons)
        self.scope.currentIndexChanged.connect(self.scope_changed); self.scope_changed()

    def scope_changed(self):
        self.pattern.setEnabled(self.scope.currentIndex() != 1)
        self.name.setEnabled(self.scope.currentIndex() != 0)

    def use_footprint_origin(self):
        self.origin_x.setValue(0); self.origin_y.setValue(0)

    def save(self):
        entry = {'width_mm':self.width.value(),'height_mm':self.height.value(),'rotation_deg':self.rotation.value(),
                 'origin_x_mm':self.origin_x.value(),'origin_y_mm':self.origin_y.value()}
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
        self.pending_output = None
        self.preview_groups = None; self.preview_key = ''; self.preview_error = ''; self.closing = False
        self.sizes = load_sizes()
        try:
            self.preferences = json.loads(SETTINGS_PATH.read_text(encoding='utf-8')) if SETTINGS_PATH.exists() else {}
        except Exception:
            self.preferences = {}
        self.preferences.setdefault('schema_version',1)
        self.timer = QTimer(self); self.timer.setSingleShot(True); self.timer.timeout.connect(self.refresh_display)
        self.build_ui(); self.create_menus(); self.title()
        QApplication.instance().installEventFilter(self)
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
        toolbar.addWidget(QLabel('Show groups'))
        self.show_group = QComboBox(); self.show_group.addItem('All groups'); self.show_group.currentIndexChanged.connect(self.choose_group)
        toolbar.addWidget(self.show_group)
        toolbar.addWidget(QLabel('inspect layer'))
        self.layer_combo = QComboBox(); self.layer_combo.setMinimumContentsLength(10)
        self.layer_combo.setToolTip('Rendering only. Selected-layer copper is opaque; other layers are composited at 1/3 opacity. Keys 1–9 or Page Up/Down switch layers outside text fields.')
        self.layer_combo.currentIndexChanged.connect(self.change); toolbar.addWidget(self.layer_combo)
        self.checks = {}
        toolbar.addWidget(QLabel('Show:'))
        for key,label in [('labels','Labels'),('pads','Pads'),('tracks','Tracks'),('vias','Vias')]:
            box = QCheckBox(label); box.setChecked(key == 'labels'); box.toggled.connect(self.change)
            box.setToolTip('Display only; replication options are in the Replication panel.')
            self.checks[key] = box; toolbar.addWidget(box)
        toolbar.addSpacing(12); toolbar.addWidget(QLabel('Minimum error gap'))
        self.gap = spin(.2,0,100,3); self.gap.setSuffix(' mm'); self.gap.setSingleStep(.05); self.gap.setMaximumWidth(110); self.gap.setKeyboardTracking(False)
        self.gap.setToolTip('Highlight bodies with a smaller gap; pads are excluded.')
        self.gap.valueChanged.connect(self.change); toolbar.addWidget(self.gap)
        self.mirror = QCheckBox('Mirror view'); self.mirror.setToolTip('Mirror the view horizontally; PCB coordinates stay unchanged.')
        self.mirror.toggled.connect(self.change); toolbar.addWidget(self.mirror)
        toolbar.addStretch()
        toolbar.addWidget(QLabel('Show routing for'))
        self.mode_group = QButtonGroup(self); self.scope_buttons = {}
        for name in ('Source','All','Preview'):
            button = QRadioButton(name); self.mode_group.addButton(button); self.scope_buttons[name] = button
            button.toggled.connect(lambda checked:self.change() if checked else None); toolbar.addWidget(button)
        self.scope_buttons['All'].setChecked(True)
        self.preview = self.scope_buttons['Preview']
        self.preview.setEnabled(False)
        outer.addLayout(toolbar)
        self.splitter = QSplitter(); sidebar = QWidget(); side = QVBoxLayout(sidebar)
        side.addWidget(QLabel('Part groups'))
        self.group_list = QListWidget(); self.group_list.itemDoubleClicked.connect(lambda _:self.edit_group())
        self.group_list.setContextMenuPolicy(Qt.CustomContextMenu); self.group_list.customContextMenuRequested.connect(self.group_context)
        self.group_list.setMaximumHeight(225); side.addWidget(self.group_list)
        row = QHBoxLayout()
        for label,callback in [('Add…',self.add_group),('Edit…',self.edit_group),('Remove',self.remove_group)]:
            button = QPushButton(label); button.clicked.connect(callback); row.addWidget(button)
        side.addLayout(row)
        self.layers_panel = QGroupBox('What to replicate'); layers_layout = QVBoxLayout(self.layers_panel)
        columns = QHBoxLayout(); types = QVBoxLayout(); self.copy_checks = {}
        for key,label in [('tracks','Tracks'),('vias','Vias')]:
            box = QCheckBox(label); box.setChecked(True); box.toggled.connect(self.change)
            box.setToolTip('Include this routing in replication. Viewing controls above are independent.')
            self.copy_checks[key] = box; types.addWidget(box)
        types.addStretch(); columns.addLayout(types)
        self.layer_widgets = {}; self.layers_form = QVBoxLayout(); columns.addLayout(self.layers_form)
        layers_layout.addLayout(columns)
        note = QLabel('Placement is always included. Routing is limited to connections within a group on the checked layers.'); note.setWordWrap(True); layers_layout.addWidget(note)
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
        rep.addRow(self.layers_panel)
        self.rep_note = QLabel(); self.rep_note.setWordWrap(True); rep.addRow(self.rep_note)
        row = QHBoxLayout(); row.addWidget(QLabel('Replicated:'))
        self.export_button = QPushButton('Save'); self.export_button.clicked.connect(self.export); self.export_button.setEnabled(False); row.addWidget(self.export_button)
        self.export_as_button = QPushButton('Save as…'); self.export_as_button.clicked.connect(self.export_as); self.export_as_button.setEnabled(False); row.addWidget(self.export_as_button)
        rep.addRow(row)
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
        action(file,'Save recipe',self.save); action(file,'Save recipe as…',self.save_as)
        replicated = file.addMenu('Replicated PCB')
        action(replicated,'Save',self.export,'Ctrl+S'); action(replicated,'Save as…',self.export_as,'Ctrl+Shift+S')
        file.addSeparator(); action(file,'Exit',self.close,'Alt+F4')
        edit = self.menuBar().addMenu('&Edit'); action(edit,'Add group…',self.add_group)
        action(edit,'Edit selected group…',self.edit_group); action(edit,'Remove selected group',self.remove_group)
        edit.addSeparator(); action(edit,'Copy selected RefDes',self.copy_selected,'Ctrl+C')
        action(edit,'Define selected body size…',lambda:self.edit_size(self.selected))
        action(edit,'Fit',self.fit,'F'); action(edit,'Body rotation correction…',self.rotation_correction)
        tools = self.menuBar().addMenu('&Tools'); self.rep_action = action(tools,'Replication settings',lambda:self.rep_toggle.setChecked(not self.rep_toggle.isChecked()))
        self.rep_action.setCheckable(True); tools.addSeparator()
        action(tools,'Open program settings JSON',lambda:self.open_local(SETTINGS_PATH))
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
                output = recipe.get('replicated_pcb')
                if output:
                    output = Path(output)
                    recipe['replicated_pcb'] = str((output if output.is_absolute() else recipe_path.parent/output).resolve())
            else:
                board = Board(path); recipe = default_recipe(board); recipe_path = None
            self.board,self.recipe,self.recipe_path = board,recipe,recipe_path
            migrate_view(recipe['view'])
            migrate_replication(recipe,board)
            self.preview_board = None; self.preview_key = ''; self.selected = ''; self.preview_error = ''
            self.pending_output = None
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
            self.layer_combo.clear()
            for i,(key,label) in enumerate(self.board.layers.items()):
                self.layer_combo.addItem(f'{i+1}: {label}' if i < 9 else label,key)
            active = self.layer_combo.findData(self.recipe['view'].get('active_layer'))
            self.layer_combo.setCurrentIndex(max(0,active))
            for key,box in self.checks.items():
                box.setChecked(self.recipe['view'][key])
            self.gap.setValue(self.recipe['clearance_mm']); self.mirror.setChecked(self.recipe['view']['mirror'])
            mode = 'Preview' if self.recipe['view']['preview'] else 'Source' if self.recipe['view']['routing_scope'] in ('Source','Source only') else 'All'
            self.scope_buttons[mode].setChecked(True)
            while self.layers_form.count():
                item = self.layers_form.takeAt(0)
                if item.widget():
                    item.widget().deleteLater()
            self.layer_widgets = {}
            for key,label in self.board.layers.items():
                box = QCheckBox(label); box.setChecked(key in self.recipe['replication']['layers']); box.toggled.connect(self.change)
                self.layer_widgets[key] = box; self.layers_form.addWidget(box)
            settings = self.recipe['replication']
            for key,box in self.copy_checks.items():
                box.setChecked(settings[key])
            for key,box in self.rep_fields.items():
                box.setCurrentIndex(settings[key]) if key == 'source' else box.setCurrentText(settings[key])
            self.rep_count.setValue(settings['count']); self.auto_angle.setChecked(settings['automatic_angle'])
            for key,box in self.inputs.items():
                box.setValue(settings[key])
            self.rep_toggle.setChecked(self.preferences.get('replication_open',False) or self.preview.isChecked())
        finally:
            self.loading = False
        self.update_controls()
        self.export_button.setEnabled(True); self.export_as_button.setEnabled(True)

    def update_controls(self):
        for box in self.layer_widgets.values():
            box.setEnabled(any(c.isChecked() for c in self.copy_checks.values()))
        self.arrangements.setCurrentIndex(self.rep_fields['layout'].currentIndex())
        self.arrangements.setFixedHeight(self.arrangements.currentWidget().sizeHint().height())
        create = self.rep_fields['mode'].currentIndex() == 0
        grid = self.rep_fields['layout'].currentText() == 'Grid'
        self.count_label.setVisible(create and not grid); self.rep_count.setVisible(create and not grid)
        self.inputs['angle_deg'].setEnabled(not self.auto_angle.isChecked())
        self.rep_note.setText('The source is instance 1. Copies get new RefDes and internal nets.' if create else 'Count comes from your groups. Parts correspond by list order; destination nets are preserved.')

    def toggle_replication(self,opened):
        self.rep_toggle.setArrowType(Qt.DownArrow if opened else Qt.RightArrow); self.rep_panel.setVisible(opened)
        self.preview.setEnabled(opened)
        self.rep_action.setChecked(opened) if hasattr(self,'rep_action') else None
        self.preferences['replication_open'] = opened
        if not opened and self.preview.isChecked():
            self.pending_output = None
            self.scope_buttons['All'].setChecked(True)
        self.change()

    def change(self,*args):
        if not self.recipe or getattr(self,'loading',False):
            return
        view = self.recipe['view']
        mirror_changed = view.get('mirror',False) != self.mirror.isChecked()
        if mirror_changed:
            view.pop('legacy_rotation_deg',None)
        view['mirror'] = self.mirror.isChecked(); self.recipe['clearance_mm'] = self.gap.value()
        view['active_layer'] = self.layer_combo.currentData()
        view.update({key:box.isChecked() for key,box in self.checks.items()})
        view.update(preview=self.preview.isChecked(),routing_scope='Source' if self.scope_buttons['Source'].isChecked() else 'All')
        settings = self.recipe['replication']
        settings.update({key:box.isChecked() for key,box in self.copy_checks.items()})
        settings['layers'] = [key for key,box in self.layer_widgets.items() if box.isChecked()]
        settings.update({key:box.currentIndex() if key == 'source' else box.currentText() for key,box in self.rep_fields.items()})
        settings.update(count=self.rep_count.value(),automatic_angle=self.auto_angle.isChecked())
        settings.update({key:box.value() for key,box in self.inputs.items()})
        self.update_controls(); self.title(); self.timer.start(180)
        if self.preview.isChecked() and not self.rep_toggle.isChecked():
            self.rep_toggle.setChecked(True)
        if mirror_changed:
            center = self.canvas.mapToScene(self.canvas.viewport().rect().center())
            scale = abs(self.canvas.transform().m11())
            self.canvas.setTransform(QTransform().scale((-1 if view['mirror'] else 1)*scale,-scale))
            self.canvas.centerOn(center)

    def key(self):
        settings = {k:float(v) if type(v) in (int,float) else v for k,v in self.recipe['replication'].items()}
        return self.board.fingerprint+json.dumps({'groups':self.recipe['groups'],'replication':settings},sort_keys=True)

    def eventFilter(self,receiver,event):
        if event.type() == QEvent.KeyPress and self.isActiveWindow() and QApplication.activePopupWidget() is None:
            focus = QApplication.focusWidget()
            editable = isinstance(focus,(QLineEdit,QPlainTextEdit,QTextEdit,QAbstractSpinBox)) or (isinstance(focus,QComboBox) and focus.isEditable())
            if not editable and not event.modifiers() & (Qt.ControlModifier|Qt.AltModifier|Qt.MetaModifier|Qt.ShiftModifier):
                key = event.key(); count = self.layer_combo.count(); index = self.layer_combo.currentIndex()
                if count and Qt.Key_1 <= key <= Qt.Key_9:
                    target = key-Qt.Key_1
                    if target < count:
                        self.layer_combo.setCurrentIndex(target); return True
                elif count and key in (Qt.Key_PageUp,Qt.Key_PageDown):
                    self.layer_combo.setCurrentIndex(max(0,min(count-1,index+(-1 if key == Qt.Key_PageUp else 1))))
                    return True
        return super().eventFilter(receiver,event)

    def choose_group(self,index):
        if self.recipe and not getattr(self,'loading',False):
            self.recipe['view']['display_group'] = index; self.title(); self.redraw()

    def refresh_display(self):
        if not self.board:
            return
        if not self.preview.isChecked():
            self.pending_output = None
            if self.worker:
                self.worker.requestInterruption()
            self.redraw(); return
        if self.preview_board is not None and self.preview_key == self.key():
            self.redraw(); return
        if self.worker:
            if self.worker.key != self.key():
                self.worker.requestInterruption()
            return
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
            self.pending_output = None
            self.canvas.scene().clear(); self.body_items = {}; self.canvas.body_items = self.body_items
            self.summary.setText(self.preview_error); self.inspector.setText('Correct the replication settings to preview.'); return
        self.refresh_display()
        if self.preview_key == self.key():
            self.fit()
            if self.pending_output is not None:
                output = self.pending_output; self.pending_output = None
                self.save_replicated_to(output)

    def redraw(self,*args):
        if not self.board:
            return
        try:
            use_preview = self.preview.isChecked() and self.preview_board is not None and self.preview_key == self.key()
            if self.preview.isChecked() and not use_preview:
                return
            board = self.preview_board if use_preview else self.board
            groups = [g['refs'] for g in self.preview_groups] if use_preview else group_refs(board,self.recipe)
            names = [g['name'] for g in self.preview_groups] if use_preview else [g['name'] for g in self.recipe['groups']] or ['Whole PCB']
            current = self.show_group.currentIndex()
            self.show_group.blockSignals(True); self.show_group.clear(); self.show_group.addItems(['All groups']+names)
            self.show_group.setCurrentIndex(max(0,min(current,len(names)))); self.show_group.blockSignals(False)
            self.display_board = board; self.display_groups = groups
            scene = self.canvas.scene(); scene.clear(); self.body_items = {}
            self.canvas.body_items = self.body_items
            copper_layers = {}
            for layer in board.layers:
                group_item = QGraphicsItemGroup(); scene.addItem(group_item)
                active = layer == self.layer_combo.currentData()
                group_item.setZValue(1 if active else 0)
                if not active:
                    effect = QGraphicsOpacityEffect(); effect.setOpacity(1/3)
                    group_item.setGraphicsEffect(effect)
                copper_layers[layer] = group_item
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
                draw_routes = not self.scope_buttons['Source'].isChecked() or index == source_group_index
                if draw_routes and (self.checks['tracks'].isChecked() or self.checks['vias'].isChecked()):
                    route = board.replication_routing[index] if use_preview else board.routing(group,True,True,set(board.layers))
                    if self.checks['tracks'].isChecked():
                        for key,edges in route.segments.items():
                            points = board.traces[key].findall('./Points/Point'); tracks_count += len(intervals(edges))
                            for i in edges:
                                p,q = points[i:i+2]
                                path = QPainterPath(); path.moveTo(float(p.get('X'))*board.factor,float(p.get('Y'))*board.factor)
                                path.lineTo(float(q.get('X'))*board.factor,float(q.get('Y'))*board.factor)
                                pen = QPen(QColor(COPPER_COLOR)); pen.setWidthF(max(.03,float(q.get('Width',1))*board.factor))
                                pen.setCapStyle(Qt.RoundCap); pen.setJoinStyle(Qt.RoundJoin)
                                track = scene.addPath(path,pen)
                                track.setData(1,'track'); copper_layers[q.get('Lay')].addToGroup(track)
                    if self.checks['vias'].isChecked():
                        locations = []
                        for vid in route.vias:
                            part = board.by_id[vid]; style = part.element.get('ViaStyle')
                            locations.append((part.x,part.y,board.via_sizes.get(style,.5),board.via_drills.get(style,0)))
                        for nid,tid,i in route.embedded:
                            p = board.traces[nid,tid].findall('./Points/Point')[i]
                            style = p.get('ViaStyle')
                            locations.append((float(p.get('X'))*board.factor,float(p.get('Y'))*board.factor,board.via_sizes.get(style,.5),board.via_drills.get(style,0)))
                        for x,y,size,drill in locations:
                            for group_item in copper_layers.values():
                                ring = scene.addEllipse(x-size/2,y-size/2,size,size,QPen(Qt.NoPen),QBrush(QColor(COPPER_COLOR)))
                                ring.setData(1,'via'); group_item.addToGroup(ring)
                            if drill > 0:
                                hole = scene.addEllipse(x-drill/2,y-drill/2,drill,drill,QPen(Qt.NoPen),self.canvas.backgroundBrush())
                                hole.setData(1,'drill'); hole.setZValue(2.5)
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
                                child = scene.addPath(path,QPen(tint_body.darker(170),.025)); child.setData(0,ref); child.setZValue(3.1)
                    else:
                        body = scene.addEllipse(part.x-.25,part.y-.25,.5,.5,pen,QBrush(QColor('#ffcf75')))
                    body.setData(0,ref); body.setData(1,'body'); body.setZValue(3); self.body_items[ref] = body
                    body.setToolTip(f'{ref} · {part.name}\n{part.pattern}\n{origins[ref]}\nX {part.x:.3f}, Y {part.y:.3f} mm')
                    if self.checks['pads'].isChecked():
                        for pid,points in board.pad_polygons(part):
                            for layer in board.pad_layers(part,pid):
                                if layer not in copper_layers:
                                    continue
                                item = scene.addPolygon(QPolygonF([QPointF(*p) for p in points]),QPen(Qt.NoPen),QBrush(QColor(COPPER_COLOR)))
                                item.setData(0,ref); item.setData(1,'pad'); item.setToolTip(f'{ref} pad {pid}'); copper_layers[layer].addToGroup(item)
                    if self.checks['labels'].isChecked():
                        anchor = ((min(x for x,y in poly)+max(x for x,y in poly))/2,(min(y for x,y in poly)+max(y for x,y in poly))/2) if poly else (part.x,part.y)
                        text = scene.addSimpleText(ref,QFont('Segoe UI' if sys.platform == 'win32' else '',9)); text.setPos(*anchor)
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
            self.summary.setText(str(error))

    def fit(self):
        if not self.canvas.scene().items():
            return
        view = self.recipe['view'] if self.recipe else {}
        legacy_sign = -1 if view.get('legacy_rotation_deg') == 180 else 1
        self.canvas.setTransform(QTransform().scale((-1 if view.get('mirror',False) else 1)*legacy_sign,-legacy_sign))
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
            if data.get('replicated_pcb'):
                try:
                    data['replicated_pcb'] = os.path.relpath(data['replicated_pcb'],self.recipe_path.parent)
                except ValueError:
                    pass
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

    def export_as(self):
        self.export(save_as=True)

    def export(self,save_as=False):
        if not self.recipe:
            return
        filename = self.recipe.get('replicated_pcb')
        if save_as or not filename:
            proposed = filename or self.board.path.with_name(self.board.path.stem+'_replicated.dipxml')
            filename,_ = QFileDialog.getSaveFileName(self,'Save replicated PCB as',str(proposed),'DipTrace PCB XML (*.dipxml)')
            if not filename:
                return
        output = Path(filename).resolve()
        if output.suffix.casefold() != '.dipxml':
            output = output.with_name(output.name+'.dipxml')
        if self.preview_board is not None and self.preview_key == self.key():
            self.save_replicated_to(output); return
        if not self.rep_toggle.isChecked():
            self.rep_toggle.setChecked(True)
        self.pending_output = output
        self.preview.setChecked(True); self.refresh_display()

    def save_replicated_to(self,output):
        try:
            if self.board.path.read_bytes() != self.board.data:
                raise ValueError('The source PCB changed on disk. Reload before replicating.')
            data = ET.tostring(self.preview_board.root,encoding='utf-8',xml_declaration=True)
            if output == self.board.path:
                backup = output.with_name(output.name+'.'+datetime.now().strftime('%Y%m%d-%H%M%S-%f')+'.bak'); backup.write_bytes(self.board.data)
            temp = output.with_name(output.name+'.tmp'); temp.write_bytes(data); temp.replace(output)
            self.recipe['replicated_pcb'] = str(output)
            if output == self.board.path:
                old_groups = self.preview_groups; old_source = old_groups[0]['refs']; offsets = dict(self.recipe['rotation_offsets'])
                if self.recipe['replication']['mode'] == 'Create copies':
                    for group in old_groups[1:]:
                        for a,b in zip(old_source,group['refs']):
                            if a in offsets:
                                offsets[b] = offsets[a]
                self.board = Board(output); self.recipe['pcb'] = str(output); self.recipe['groups'] = old_groups; self.recipe['rotation_offsets'] = offsets
                source_index = 0 if self.recipe['replication']['mode'] == 'Create copies' else self.recipe['replication']['source']
                self.recipe['view']['preview'] = False; self.recipe['replication'].update(mode='Arrange existing groups',source=source_index)
                self.preview_board = None; self.preview_key = ''; self.populate(); self.refresh_display(); self.fit()
            if self.save():
                self.statusBar().showMessage('Replicated PCB saved. Re-import the XML in DipTrace and run its checks.',10000)
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
        QApplication.instance().removeEventFilter(self)
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
