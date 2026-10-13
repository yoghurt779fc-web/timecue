"""Главное окно TimeCue."""

from __future__ import annotations

import os
import queue
import time

from PySide6.QtCore import QSettings, Qt, QTimer
from PySide6.QtGui import QAction, QBrush, QColor, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QAbstractItemView, QComboBox, QFileDialog, QFrame, QGridLayout, QHBoxLayout, QHeaderView, QLabel,
    QLineEdit, QMainWindow, QMessageBox, QPlainTextEdit, QPushButton, QSplitter, QTableWidget,
    QTableWidgetItem, QVBoxLayout, QWidget,
)

from .. import APP_NAME, APP_VERSION
from ..engine import SOURCE_LABELS, Engine
from ..show import CUE_TYPES, Cue, DmxRow, Show
from ..tc import format_tc, parse_tc
from .inspector import AUDIO_FILTER, Inspector
from .settings_dialog import SettingsDialog
from .style import AMBER, BLUE, GREEN, MUTED, RED, TEXT, TYPE_COLORS, VIOLET, lamp_style

SHOW_FILTER = "Шоу TimeCue (*.tcshow);;Все файлы (*.*)"
AUDIO_EXT = (".wav", ".aif", ".aiff", ".flac", ".ogg", ".mp3")

COL_MARK, COL_NUM, COL_TYPE, COL_NAME, COL_TC, COL_INFO = range(6)

HELP_TEXT = """<h3>Как пользоваться</h3>
<p><b>1.</b> Добавьте кью кнопками <b>+ Звук</b>, <b>+ DMX</b>, <b>+ OSC</b> (звуковые файлы можно просто перетащить в окно).<br>
<b>2.</b> Справа заполните, что кью делает. Если указать <b>таймкод</b> — кью сработает сама, когда время до него дойдёт.
Без таймкода — только по кнопке <b>GO</b>.<br>
<b>3.</b> В <b>Настройках</b> выберите источник времени: внутренние часы (программа — мастер) или следование за MTC/LTC.</p>
<h3>Горячие клавиши</h3>
<table cellpadding="3">
<tr><td><b>Пробел</b></td><td>GO — запустить следующую кью</td></tr>
<tr><td><b>Esc</b></td><td>Остановить весь звук</td></tr>
<tr><td><b>F5</b></td><td>Пуск / пауза времени</td></tr>
<tr><td><b>F6</b></td><td>Стоп и возврат к старту</td></tr>
<tr><td><b>Ctrl+T</b></td><td>Поставить выбранной кью текущий таймкод</td></tr>
<tr><td><b>Ctrl+G</b></td><td>Перейти к времени</td></tr>
<tr><td><b>Ctrl+D</b></td><td>Дублировать кью</td></tr>
<tr><td><b>Delete</b></td><td>Удалить кью</td></tr>
<tr><td><b>Ctrl+S / Ctrl+O</b></td><td>Сохранить / открыть шоу</td></tr>
</table>"""


def _lamp(text: str) -> QLabel:
    lab = QLabel(text)
    lab.setObjectName("lamp")
    lab.setAlignment(Qt.AlignCenter)
    lab.setMinimumWidth(66)
    return lab


class MainWindow(QMainWindow):
    def __init__(self, show: Show | None = None, open_devices: bool = True):
        super().__init__()
        self.qs = QSettings("TimeCue", "TimeCue")
        self.show_obj = show or Show()
        self.engine = Engine(self.show_obj, open_devices=open_devices)
        self._row_cache: dict[int, tuple] = {}
        self._last_osc_count = 0
        self._osc_flash_until = 0.0
        self._selecting = False

        self.setWindowTitle(APP_NAME)
        self.resize(1360, 820)
        self.setAcceptDrops(True)

        root = QWidget()
        root.setObjectName("root")
        self.setCentralWidget(root)
        v = QVBoxLayout(root)
        v.setContentsMargins(12, 10, 12, 10)
        v.setSpacing(10)
        v.addWidget(self._build_topbar())
        v.addWidget(self._build_gobar())

        split = QSplitter(Qt.Horizontal)
        split.setHandleWidth(10)
        split.addWidget(self._build_cuelist())
        self.inspector = Inspector(self.engine)
        self.inspector.setMinimumWidth(380)
        split.addWidget(self.inspector)
        split.setStretchFactor(0, 3)
        split.setStretchFactor(1, 2)
        split.setSizes([800, 520])

        vsplit = QSplitter(Qt.Vertical)
        vsplit.setHandleWidth(8)
        vsplit.addWidget(split)
        vsplit.addWidget(self._build_log())
        vsplit.setStretchFactor(0, 5)
        vsplit.setStretchFactor(1, 1)
        vsplit.setSizes([680, 90])
        v.addWidget(vsplit, 1)

        self._build_menu()
        self._build_shortcuts()

        self.inspector.changed.connect(self._on_cue_edited)
        self.inspector.fire_requested.connect(lambda c: self.engine.fire(c, "go"))
        self.inspector.stop_requested.connect(lambda c: self.engine.stop_cue(c))

        self.timer = QTimer(self)
        self.timer.timeout.connect(self._on_timer)
        self.timer.start(33)

        self.engine.start()
        self._after_settings_changed()
        self.refresh_table()
        self._update_title()
        if open_devices:
            g = self.qs.value("geometry")
            if g is not None:
                self.restoreGeometry(g)

    # ================================================================ построение
    def _build_topbar(self) -> QWidget:
        bar = QFrame()
        bar.setObjectName("topbar")
        h = QHBoxLayout(bar)
        h.setContentsMargins(16, 10, 16, 10)
        h.setSpacing(18)

        left = QVBoxLayout()
        left.setSpacing(0)
        self.tc_label = QLabel("00:00:00:00")
        self.tc_label.setObjectName("tcDisplay")
        self.tc_sub = QLabel("")
        self.tc_sub.setObjectName("muted")
        left.addWidget(self.tc_label)
        left.addWidget(self.tc_sub)
        h.addLayout(left)

        tr = QVBoxLayout()
        tr.setSpacing(6)
        btns = QHBoxLayout()
        btns.setSpacing(6)
        self.btn_back = self._tbtn("⏮", "Стоп и в начало (F6)", self._stop)
        self.btn_rew = self._tbtn("−5с", "Назад на 5 секунд", lambda: self.engine.nudge(-5))
        self.btn_play = self._tbtn("▶", "Пуск / пауза (F5)", self._toggle_play)
        self.btn_play.setMinimumWidth(78)
        self.btn_ffw = self._tbtn("+5с", "Вперёд на 5 секунд", lambda: self.engine.nudge(5))
        for b in (self.btn_back, self.btn_rew, self.btn_play, self.btn_ffw):
            btns.addWidget(b)
        tr.addLayout(btns)
        loc = QHBoxLayout()
        loc.setSpacing(6)
        self.locate_edit = QLineEdit()
        self.locate_edit.setObjectName("tcEdit")
        self.locate_edit.setPlaceholderText("перейти к… 01:00:00:00")
        self.locate_edit.setMaximumWidth(200)
        self.locate_edit.returnPressed.connect(self._locate)
        go_to = QPushButton("Перейти")
        go_to.setFocusPolicy(Qt.NoFocus)
        go_to.clicked.connect(self._locate)
        loc.addWidget(self.locate_edit)
        loc.addWidget(go_to)
        tr.addLayout(loc)
        h.addLayout(tr)

        h.addStretch(1)

        right = QVBoxLayout()
        right.setSpacing(6)
        srow = QHBoxLayout()
        lab = QLabel("Время:")
        lab.setObjectName("muted")
        self.source_combo = QComboBox()
        for key, label in SOURCE_LABELS.items():
            self.source_combo.addItem(label, key)
        self.source_combo.setMinimumWidth(190)
        self.source_combo.setFocusPolicy(Qt.NoFocus)
        self.source_combo.activated.connect(self._on_source_chosen)
        btn_set = QPushButton("⚙ Настройки")
        btn_set.setFocusPolicy(Qt.NoFocus)
        btn_set.clicked.connect(self.open_settings)
        srow.addWidget(lab)
        srow.addWidget(self.source_combo)
        srow.addWidget(btn_set)
        right.addLayout(srow)
        grid = QGridLayout()
        grid.setSpacing(5)
        self.lamp_mtc_in = _lamp("MTC ВХ")
        self.lamp_ltc_in = _lamp("LTC ВХ")
        self.lamp_mtc_out = _lamp("MTC ВЫХ")
        self.lamp_ltc_out = _lamp("LTC ВЫХ")
        self.lamp_dmx = _lamp("DMX")
        self.lamp_osc = _lamp("OSC")
        grid.addWidget(self.lamp_mtc_in, 0, 0)
        grid.addWidget(self.lamp_ltc_in, 0, 1)
        grid.addWidget(self.lamp_dmx, 0, 2)
        grid.addWidget(self.lamp_mtc_out, 1, 0)
        grid.addWidget(self.lamp_ltc_out, 1, 1)
        grid.addWidget(self.lamp_osc, 1, 2)
        right.addLayout(grid)
        h.addLayout(right)
        return bar

    def _tbtn(self, text, tip, slot) -> QPushButton:
        b = QPushButton(text)
        b.setObjectName("transport")
        b.setToolTip(tip)
        b.setFocusPolicy(Qt.NoFocus)
        b.clicked.connect(slot)
        return b

    def _build_gobar(self) -> QWidget:
        bar = QFrame()
        bar.setObjectName("gobar")
        h = QHBoxLayout(bar)
        h.setContentsMargins(12, 10, 12, 10)
        h.setSpacing(14)
        self.btn_go = QPushButton("GO")
        self.btn_go.setObjectName("goButton")
        self.btn_go.setToolTip("Запустить следующую кью (Пробел)")
        self.btn_go.setFocusPolicy(Qt.NoFocus)
        self.btn_go.clicked.connect(self._go)
        h.addWidget(self.btn_go)
        nv = QVBoxLayout()
        nv.setSpacing(2)
        cap = QLabel("СЛЕДУЮЩАЯ ПО GO")
        cap.setObjectName("sectionTitle")
        self.next_label = QLabel("—")
        self.next_label.setObjectName("nextCue")
        self.next_info = QLabel("")
        self.next_info.setObjectName("muted")
        nv.addWidget(cap)
        nv.addWidget(self.next_label)
        nv.addWidget(self.next_info)
        h.addLayout(nv, 1)
        self.btn_panic = QPushButton("■  Стоп звука\nEsc")
        self.btn_panic.setObjectName("panicButton")
        self.btn_panic.setFocusPolicy(Qt.NoFocus)
        self.btn_panic.clicked.connect(lambda: self.engine.panic())
        h.addWidget(self.btn_panic)
        return bar

    def _build_cuelist(self) -> QWidget:
        panel = QFrame()
        panel.setObjectName("panel")
        v = QVBoxLayout(panel)
        v.setContentsMargins(10, 10, 10, 10)
        v.setSpacing(8)
        tb = QHBoxLayout()
        tb.setSpacing(6)
        for text, name, tip, slot in (
            ("+ Звук", "addAudio", "Добавить звуковую кью (можно перетащить файлы в окно)", lambda: self.add_cue("audio")),
            ("+ DMX", "addDmx", "Добавить кью, которая выставит каналы DMX", lambda: self.add_cue("dmx")),
            ("+ OSC", "addOsc", "Добавить кью, которая отправит OSC-сообщение", lambda: self.add_cue("osc")),
        ):
            b = QPushButton(text)
            b.setObjectName(name)
            b.setToolTip(tip)
            b.setFocusPolicy(Qt.NoFocus)
            b.clicked.connect(slot)
            tb.addWidget(b)
        tb.addSpacing(12)
        for text, tip, slot in (
            ("⧉", "Дублировать (Ctrl+D)", self.duplicate_cue),
            ("↑", "Переместить выше", lambda: self.move_cue(-1)),
            ("↓", "Переместить ниже", lambda: self.move_cue(1)),
            ("⏱", "Поставить выбранной кью текущий таймкод (Ctrl+T)", self.inspector_set_now),
            ("✕", "Удалить (Delete)", self.delete_cue),
        ):
            b = QPushButton(text)
            b.setToolTip(tip)
            b.setFixedWidth(40)
            b.setFocusPolicy(Qt.NoFocus)
            b.clicked.connect(slot)
            tb.addWidget(b)
        tb.addStretch(1)
        self.count_label = QLabel("")
        self.count_label.setObjectName("muted")
        tb.addWidget(self.count_label)
        v.addLayout(tb)

        self.table = QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels(["", "№", "Тип", "Название", "Таймкод", "Что делает"])
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        self.table.setShowGrid(False)
        self.table.setWordWrap(False)
        self.table.verticalHeader().setDefaultSectionSize(34)
        hh = self.table.horizontalHeader()
        hh.setSectionResizeMode(COL_MARK, QHeaderView.Fixed)
        hh.setSectionResizeMode(COL_NUM, QHeaderView.ResizeToContents)
        hh.setSectionResizeMode(COL_TYPE, QHeaderView.ResizeToContents)
        hh.setSectionResizeMode(COL_NAME, QHeaderView.Stretch)
        hh.setSectionResizeMode(COL_TC, QHeaderView.ResizeToContents)
        hh.setSectionResizeMode(COL_INFO, QHeaderView.Stretch)
        self.table.setColumnWidth(COL_MARK, 30)
        self.table.itemSelectionChanged.connect(self._on_selection)
        v.addWidget(self.table, 1)
        self.empty_hint = QLabel("Пока пусто. Нажмите «+ Звук», «+ DMX» или «+ OSC», "
                                 "или перетащите сюда звуковые файлы.")
        self.empty_hint.setObjectName("muted")
        self.empty_hint.setAlignment(Qt.AlignCenter)
        self.empty_hint.setWordWrap(True)
        v.addWidget(self.empty_hint)
        return panel

    def _build_log(self) -> QWidget:
        panel = QFrame()
        panel.setObjectName("panel")
        v = QVBoxLayout(panel)
        v.setContentsMargins(10, 6, 10, 8)
        v.setSpacing(4)
        cap = QLabel("ЖУРНАЛ")
        cap.setObjectName("sectionTitle")
        v.addWidget(cap)
        self.log_view = QPlainTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setMaximumBlockCount(1000)
        self.log_view.setStyleSheet("font-family: Consolas, 'DejaVu Sans Mono'; font-size: 9pt; border: none;")
        v.addWidget(self.log_view, 1)
        return panel

    def _build_menu(self) -> None:
        mb = self.menuBar()
        m = mb.addMenu("Файл")
        self._act(m, "Новое шоу", "Ctrl+N", self.new_show)
        self._act(m, "Открыть…", "Ctrl+O", self.open_show)
        self._act(m, "Сохранить", "Ctrl+S", self.save_show)
        self._act(m, "Сохранить как…", "Ctrl+Shift+S", self.save_show_as)
        m.addSeparator()
        self._act(m, "Выход", "Ctrl+Q", self.close)
        m = mb.addMenu("Кью")
        self._act(m, "Добавить звук", None, lambda: self.add_cue("audio"))
        self._act(m, "Добавить DMX", None, lambda: self.add_cue("dmx"))
        self._act(m, "Добавить OSC", None, lambda: self.add_cue("osc"))
        m.addSeparator()
        self._act(m, "Дублировать", "Ctrl+D", self.duplicate_cue)
        self._act(m, "Поставить текущий таймкод", "Ctrl+T", self.inspector_set_now)
        self._act(m, "Упорядочить по таймкоду", None, self.sort_by_time)
        self._act(m, "Перенумеровать 1, 2, 3…", None, self.renumber)
        m = mb.addMenu("Время")
        self._act(m, "Пуск / пауза", "F5", self._toggle_play)
        self._act(m, "Стоп и в начало", "F6", self._stop)
        self._act(m, "Перейти к…", "Ctrl+G", lambda: (self.locate_edit.setFocus(), self.locate_edit.selectAll()))
        m = mb.addMenu("Настройки")
        self._act(m, "Открыть настройки…", "Ctrl+,", self.open_settings)
        m = mb.addMenu("Справка")
        self._act(m, "Как пользоваться и горячие клавиши", "F1", self.show_help)
        self._act(m, "О программе", None, lambda: QMessageBox.about(
            self, APP_NAME, f"<b>{APP_NAME} {APP_VERSION}</b><br>Кью-плеер с таймкодом MTC/LTC, "
                            f"DMX (Art-Net, sACN) и OSC."))

    def _act(self, menu, text, shortcut, slot) -> QAction:
        a = QAction(text, self)
        if shortcut:
            a.setShortcut(QKeySequence(shortcut))
        a.triggered.connect(slot)
        menu.addAction(a)
        return a

    def _build_shortcuts(self) -> None:
        sc = QShortcut(QKeySequence("Space"), self)
        sc.activated.connect(self._go)
        sc = QShortcut(QKeySequence("Esc"), self)
        sc.activated.connect(lambda: self.engine.panic())
        sc = QShortcut(QKeySequence("Delete"), self.table)
        sc.setContext(Qt.WidgetShortcut)
        sc.activated.connect(self.delete_cue)

    # ================================================================ кью-лист
    def selected_index(self) -> int:
        rows = self.table.selectionModel().selectedRows() if self.table.selectionModel() else []
        return rows[0].row() if rows else -1

    def selected_cue(self) -> Cue | None:
        i = self.selected_index()
        return self.show_obj.cues[i] if 0 <= i < len(self.show_obj.cues) else None

    def refresh_table(self, select: int | None = None) -> None:
        cues = self.show_obj.cues
        if select is None:
            select = self.selected_index()
        self._selecting = True
        self.table.setRowCount(len(cues))
        self._row_cache.clear()
        for i in range(len(cues)):
            for c in range(6):
                if self.table.item(i, c) is None:
                    self.table.setItem(i, c, QTableWidgetItem(""))
            self.update_row(i)
        self._selecting = False
        self.empty_hint.setVisible(not cues)
        n_tc = sum(1 for c in cues if c.trigger.strip())
        self.count_label.setText(f"{len(cues)} кью · {n_tc} по таймкоду" if cues else "")
        if cues:
            sel = min(max(select, 0), len(cues) - 1)
            self.table.selectRow(sel)
            self.engine.standby = sel
        else:
            self.inspector.set_cue(None)
        self._update_next()

    def update_row(self, i: int) -> None:
        c = self.show_obj.cues[i]
        items = [self.table.item(i, k) for k in range(6)]
        if any(it is None for it in items):
            return
        items[COL_NUM].setText(c.number)
        items[COL_TYPE].setText(CUE_TYPES.get(c.type, c.type))
        items[COL_TYPE].setForeground(QBrush(QColor(TYPE_COLORS.get(c.type, MUTED))))
        items[COL_NAME].setText(c.name or "(без названия)")
        if c.trigger.strip():
            frames = parse_tc(c.trigger, self.engine.fr)
            items[COL_TC].setText(format_tc(frames, self.engine.fr) if frames is not None else f"⚠ {c.trigger}")
            items[COL_TC].setForeground(QBrush(QColor(TEXT if frames is not None else RED)))
        else:
            items[COL_TC].setText("по GO")
            items[COL_TC].setForeground(QBrush(QColor(MUTED)))
        items[COL_INFO].setText(c.summary())
        items[COL_INFO].setToolTip(c.summary() + (f"\n{c.notes}" if c.notes else ""))
        color = QColor(TEXT if c.enabled else "#5b6270")
        for k in (COL_NUM, COL_NAME, COL_INFO):
            items[k].setForeground(QBrush(color if c.enabled else QColor("#5b6270")))
        f = items[COL_TC].font()
        f.setFamily("Consolas")
        items[COL_TC].setFont(f)
        for it in items:
            it.setData(Qt.UserRole, c.id)
        self._row_cache.pop(i, None)

    def _on_selection(self) -> None:
        if self._selecting:
            return
        i = self.selected_index()
        cue = self.selected_cue()
        self.inspector.set_cue(cue)
        if i >= 0:
            self.engine.standby = i
        self._update_next()

    def _update_next(self) -> None:
        cues = self.show_obj.cues
        i = self.engine.standby
        if 0 <= i < len(cues):
            c = cues[i]
            self.next_label.setText(f"{c.number}  {c.name or '(без названия)'}")
            tc = f"по таймкоду {c.trigger}" if c.trigger.strip() else "только по GO"
            self.next_info.setText(f"{CUE_TYPES.get(c.type, c.type)} · {c.summary()} · {tc}")
        else:
            self.next_label.setText("—")
            self.next_info.setText("Добавьте кью")

    def _on_cue_edited(self, cue: Cue, structural: bool) -> None:
        self._mark_modified()
        try:
            i = self.show_obj.cues.index(cue)
        except ValueError:
            return
        self.update_row(i)
        if structural:
            self.engine.cues_changed()
            n_tc = sum(1 for c in self.show_obj.cues if c.trigger.strip())
            self.count_label.setText(f"{len(self.show_obj.cues)} кью · {n_tc} по таймкоду")
        self._update_next()

    def add_cue(self, kind: str) -> Cue | None:
        """Кнопки «+ Звук / DMX / OSC». Новая кью встаёт после выбранной."""
        pos = self.selected_index() + 1 if self.selected_index() >= 0 else len(self.show_obj.cues)
        if kind == "audio":
            files, _ = QFileDialog.getOpenFileNames(self, "Звуковые файлы", self.qs.value("audio_dir", ""), AUDIO_FILTER)
            if not files:
                return None
            self.qs.setValue("audio_dir", os.path.dirname(files[0]))
            return self.add_audio_files(files)
        cue = Cue(type=kind, number=self.show_obj.next_number())
        if kind == "dmx":
            cue.name = "Свет"
            cue.dmx_rows = [DmxRow(1, "1", 255)]
        else:
            targets = self.show_obj.settings["osc"].get("targets", [])
            cue.osc_target = targets[0]["name"] if targets else ""
            cue.name = "OSC"
            cue.osc_address = f"/cue/{cue.number}"
        self.show_obj.cues.insert(pos, cue)
        self._mark_modified()
        self.engine.cues_changed()
        self.refresh_table(pos)
        self.inspector.name.setFocus()
        self.inspector.name.selectAll()
        return cue

    def add_audio_files(self, files: list[str]) -> Cue | None:
        pos = self.selected_index() + 1 if self.selected_index() >= 0 else len(self.show_obj.cues)
        last = None
        for k, path in enumerate(files):
            cue = Cue(type="audio", number=self.show_obj.next_number(), file=path,
                      name=os.path.splitext(os.path.basename(path))[0])
            self.show_obj.cues.insert(pos + k, cue)
            last = cue
        if last is None:
            return None
        self._mark_modified()
        self.engine.cues_changed()
        self.refresh_table(self.show_obj.cues.index(last))
        return last

    def duplicate_cue(self) -> None:
        cue = self.selected_cue()
        if cue is None:
            return
        d = cue.to_dict()
        d.pop("id", None)
        c2 = Cue.from_dict(d)
        c2.number = self.show_obj.next_number()
        i = self.show_obj.cues.index(cue)
        self.show_obj.cues.insert(i + 1, c2)
        self._mark_modified()
        self.engine.cues_changed()
        self.refresh_table(i + 1)

    def delete_cue(self) -> None:
        i = self.selected_index()
        if i < 0:
            return
        c = self.show_obj.cues[i]
        r = QMessageBox.question(self, "Удалить кью", f"Удалить кью {c.number} «{c.name}»?",
                                 QMessageBox.Yes | QMessageBox.No, QMessageBox.Yes)
        if r != QMessageBox.Yes:
            return
        self.engine.stop_cue(c)
        del self.show_obj.cues[i]
        self._mark_modified()
        self.engine.cues_changed()
        self.refresh_table(min(i, len(self.show_obj.cues) - 1))

    def move_cue(self, d: int) -> None:
        i = self.selected_index()
        j = i + d
        cues = self.show_obj.cues
        if i < 0 or not (0 <= j < len(cues)):
            return
        cues[i], cues[j] = cues[j], cues[i]
        self._mark_modified()
        self.engine.cues_changed()
        self.refresh_table(j)

    def sort_by_time(self) -> None:
        fr = self.engine.fr

        def key(c):
            f = parse_tc(c.trigger, fr) if c.trigger.strip() else None
            return (f is None, f if f is not None else 0)

        cur = self.selected_cue()
        self.show_obj.cues.sort(key=key)
        self._mark_modified()
        self.engine.cues_changed()
        self.refresh_table(self.show_obj.cues.index(cur) if cur in self.show_obj.cues else 0)

    def renumber(self) -> None:
        for k, c in enumerate(self.show_obj.cues, 1):
            c.number = str(k)
        self._mark_modified()
        self.refresh_table()
        self.inspector.set_cue(self.selected_cue())

    def inspector_set_now(self) -> None:
        if self.inspector.cue is not None:
            self.inspector.set_trigger_now()

    # ================================================================ транспорт
    def _go(self) -> None:
        if not self.show_obj.cues:
            return
        self.engine.standby = max(0, self.selected_index())
        self.engine.go()

    def _toggle_play(self) -> None:
        if self.engine.source != "internal":
            self.statusBar().showMessage("Время идёт от внешнего источника — пуск и пауза на нём", 3000)
            return
        self.engine.toggle_play()

    def _stop(self) -> None:
        if self.engine.source != "internal":
            return
        self.engine.stop()

    def _locate(self) -> None:
        text = self.locate_edit.text().strip()
        frames = parse_tc(text, self.engine.fr)
        if frames is None:
            self.statusBar().showMessage("Не понимаю время. Пример: 01:00:00:00 или 2:30", 4000)
            return
        if self.engine.source != "internal":
            self.statusBar().showMessage("Переход работает только на внутренних часах", 4000)
            return
        self.engine.locate(frames)
        self.locate_edit.clear()
        self.table.setFocus()

    def _on_source_chosen(self, _index: int) -> None:
        key = self.source_combo.currentData()
        if key == self.show_obj.settings["timecode"].get("source"):
            return
        self.show_obj.settings["timecode"]["source"] = key
        self._mark_modified()
        self.engine.apply_settings()
        self._after_settings_changed()
        self.engine.log(f"Источник времени: {SOURCE_LABELS[key]}")

    # ================================================================ таймер интерфейса
    def _on_timer(self) -> None:
        self._drain_events()
        st = self.engine.status()
        running = st["running"]
        src = st["source"]
        self.tc_label.setText(st["tc"])
        if src == "internal":
            color = GREEN if running else TEXT
            state = "идёт" if running else "стоит"
        else:
            has = st["mtc_in"] if src == "mtc" else st["ltc_in"]
            if st["freewheel"]:
                color, state = AMBER, "удержание (сигнал пропал)"
            elif running:
                color, state = GREEN, "сигнал есть"
            elif has:
                color, state = TEXT, "сигнал есть, время стоит"
            else:
                color, state = MUTED, "нет сигнала"
            if src == "ltc" and st["ltc_in_fps"] and st["ltc_in_fps"] != st["fps"].nominal:
                state += f" · на входе {st['ltc_in_fps']} к/с!"
        self.tc_label.setStyleSheet(f"color:{color};")
        self.tc_sub.setText(f"{st['fps'].label.split(' (')[0]} · {SOURCE_LABELS[src]} · {state}")
        self.btn_play.setText("❚❚" if (running and src == "internal") else "▶")

        self._set_lamp(self.lamp_mtc_in, GREEN if st["mtc_in"] else None, src == "mtc")
        self._set_lamp(self.lamp_ltc_in, GREEN if st["ltc_in"] else None, src == "ltc")
        self._set_lamp(self.lamp_mtc_out, (GREEN if running else BLUE) if st["mtc_out"] else None, st["mtc_out"])
        self._set_lamp(self.lamp_ltc_out, (GREEN if st["ltc_out_active"] else BLUE) if st["ltc_out"] else None, st["ltc_out"])
        self._set_lamp(self.lamp_dmx, (AMBER if st["dmx_ok"] else RED) if st["dmx_outputs"] else None, st["dmx_outputs"] > 0)
        now = time.perf_counter()
        if st["osc_sent"] != self._last_osc_count:
            self._last_osc_count = st["osc_sent"]
            self._osc_flash_until = now + 0.3
        self._set_lamp(self.lamp_osc, VIOLET if now < self._osc_flash_until else None, True)
        self.lamp_mtc_out.setToolTip(f"MTC на порт: {st['mtc_out_name']}" if st["mtc_out"] else "MTC не выдаётся")

        self._paint_rows(st["playing"], now)
        self.inspector.tick()
        if st["audio_error"] and self.statusBar().currentMessage() == "":
            self.statusBar().showMessage(st["audio_error"])

    def _set_lamp(self, lamp: QLabel, color, relevant: bool) -> None:
        key = (color, relevant)
        if lamp.property("_k") == str(key):
            return
        lamp.setProperty("_k", str(key))
        if color:
            lamp.setStyleSheet(lamp_style(color))
        else:
            lamp.setStyleSheet("" if relevant else "color:#4a505c; border-color:#252a33;")

    def _paint_rows(self, playing: set, now: float) -> None:
        cues = self.show_obj.cues
        standby = self.engine.standby
        for i, c in enumerate(cues):
            fired = now - self.engine.last_fired.get(c.id, -10) < 0.6
            is_playing = c.id in playing
            key = (fired, is_playing, i == standby)
            if self._row_cache.get(i) == key:
                continue
            self._row_cache[i] = key
            mark = self.table.item(i, COL_MARK)
            if mark is None:
                continue
            if is_playing:
                mark.setText("♪")
                mark.setForeground(QBrush(QColor(GREEN)))
            elif i == standby:
                mark.setText("▶")
                mark.setForeground(QBrush(QColor(GREEN)))
            else:
                mark.setText("")
            bg = QBrush(QColor("#2a4a33")) if fired else QBrush()
            for k in range(6):
                it = self.table.item(i, k)
                if it is not None:
                    it.setBackground(bg)

    def _drain_events(self) -> None:
        for _ in range(200):
            try:
                ev = self.engine.events.get_nowait()
            except queue.Empty:
                break
            if ev[0] == "log":
                self.log_view.appendPlainText(f"{ev[1]}  {ev[2]}")
            elif ev[0] == "standby":
                i = ev[1]
                if 0 <= i < self.table.rowCount():
                    self.table.selectRow(i)
                    self.table.scrollToItem(self.table.item(i, COL_NAME))

    # ================================================================ настройки
    def open_settings(self) -> None:
        dlg = SettingsDialog(self.show_obj.settings, self)
        if dlg.exec():
            self.show_obj.settings = dlg.result_settings()
            self._mark_modified()
            self.engine.apply_settings()
            self._after_settings_changed()
            self.engine.log("Настройки применены")

    def _after_settings_changed(self) -> None:
        src = self.show_obj.settings["timecode"].get("source", "internal")
        idx = self.source_combo.findData(src)
        if idx >= 0:
            self.source_combo.setCurrentIndex(idx)
        internal = src == "internal"
        for b in (self.btn_back, self.btn_rew, self.btn_play, self.btn_ffw):
            b.setEnabled(internal)
        self.locate_edit.setEnabled(internal)
        self.inspector.refresh_choices()
        for i in range(len(self.show_obj.cues)):
            self.update_row(i)
        self.statusBar().clearMessage()
        if self.engine.audio_out.error:
            self.statusBar().showMessage(self.engine.audio_out.error)

    # ================================================================ файлы
    def _mark_modified(self) -> None:
        if not self.show_obj.modified:
            self.show_obj.modified = True
            self._update_title()

    def _update_title(self) -> None:
        name = os.path.basename(self.show_obj.path) if self.show_obj.path else "Новое шоу"
        self.setWindowTitle(f"{name}{' •' if self.show_obj.modified else ''} — {APP_NAME}")

    def _confirm_discard(self) -> bool:
        if not self.show_obj.modified:
            return True
        r = QMessageBox.question(self, APP_NAME, "Сохранить изменения в шоу?",
                                 QMessageBox.Save | QMessageBox.Discard | QMessageBox.Cancel, QMessageBox.Save)
        if r == QMessageBox.Save:
            return self.save_show()
        return r == QMessageBox.Discard

    def _replace_show(self, show: Show) -> None:
        self.engine.panic()
        self.engine.shutdown()
        self.show_obj = show
        self.engine = Engine(show)
        self.inspector.engine = self.engine
        self.engine.start()
        self.inspector.set_cue(None)
        self._after_settings_changed()
        self.refresh_table(0)
        self._update_title()

    def new_show(self) -> None:
        if not self._confirm_discard():
            return
        s = Show()
        s.settings = self.show_obj.settings_copy()  # оборудование то же — удобнее не настраивать заново
        self._replace_show(s)

    def open_show(self, path: str | None = None) -> None:
        if not path:
            if not self._confirm_discard():
                return
            path, _ = QFileDialog.getOpenFileName(self, "Открыть шоу", self.qs.value("show_dir", ""), SHOW_FILTER)
            if not path:
                return
        try:
            show = Show.load(path)
        except Exception as e:
            QMessageBox.warning(self, APP_NAME, f"Не удалось открыть файл:\n{e}")
            return
        self.qs.setValue("show_dir", os.path.dirname(path))
        self.qs.setValue("last_show", path)
        self._replace_show(show)
        self.engine.log(f"Открыто шоу: {path}")

    def save_show(self) -> bool:
        if not self.show_obj.path:
            return self.save_show_as()
        try:
            self.show_obj.save(self.show_obj.path)
        except Exception as e:
            QMessageBox.warning(self, APP_NAME, f"Не удалось сохранить:\n{e}")
            return False
        self.qs.setValue("last_show", self.show_obj.path)
        self._update_title()
        self.statusBar().showMessage("Сохранено", 2000)
        return True

    def save_show_as(self) -> bool:
        start = self.show_obj.path or os.path.join(self.qs.value("show_dir", ""), "Шоу.tcshow")
        path, _ = QFileDialog.getSaveFileName(self, "Сохранить шоу", start, SHOW_FILTER)
        if not path:
            return False
        if not path.lower().endswith(".tcshow"):
            path += ".tcshow"
        self.show_obj.path = path
        self.qs.setValue("show_dir", os.path.dirname(path))
        return self.save_show()

    def show_help(self) -> None:
        box = QMessageBox(self)
        box.setWindowTitle("Как пользоваться")
        box.setTextFormat(Qt.RichText)
        box.setText(HELP_TEXT)
        box.exec()

    # ================================================================ перетаскивание файлов
    def dragEnterEvent(self, e) -> None:
        if e.mimeData().hasUrls():
            e.acceptProposedAction()

    def dropEvent(self, e) -> None:
        paths = [u.toLocalFile() for u in e.mimeData().urls()]
        shows = [p for p in paths if p.lower().endswith(".tcshow")]
        if shows:
            if self._confirm_discard():
                self.open_show(shows[0])
            return
        audio = [p for p in paths if p.lower().endswith(AUDIO_EXT)]
        if audio:
            self.add_audio_files(audio)

    def closeEvent(self, e) -> None:
        if not self._confirm_discard():
            e.ignore()
            return
        self.qs.setValue("geometry", self.saveGeometry())
        self.timer.stop()
        self.engine.panic()
        self.engine.shutdown()
        e.accept()
