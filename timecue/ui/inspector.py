"""Панель свойств выбранной кью."""

from __future__ import annotations

import os

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDoubleSpinBox, QFileDialog, QFormLayout, QFrame, QHBoxLayout, QLabel,
    QLineEdit, QPushButton, QScrollArea, QSlider, QSpinBox, QStackedWidget, QVBoxLayout, QWidget,
)

from ..osc import parse_args
from ..show import CUE_TYPES, Cue, DmxRow
from ..tc import format_tc, parse_tc
from .style import MUTED, RED, TYPE_COLORS

AUDIO_FILTER = "Звук (*.wav *.aif *.aiff *.flac *.ogg *.mp3);;Все файлы (*.*)"


def _hint(text: str) -> QLabel:
    lab = QLabel(text)
    lab.setObjectName("hint")
    lab.setWordWrap(True)
    return lab


class DmxRowWidget(QFrame):
    changed = Signal()
    remove = Signal(object)

    def __init__(self, row: DmxRow, parent=None):
        super().__init__(parent)
        self.row = row
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(6)
        self.uni = QSpinBox()
        self.uni.setRange(1, 999)
        self.uni.setPrefix("U ")
        self.uni.setValue(row.universe)
        self.uni.setToolTip("Вселенная (как в настройках DMX → «Вселенная в кью»)")
        self.chans = QLineEdit(row.channels)
        self.chans.setPlaceholderText("1-10, 15")
        self.chans.setToolTip("Каналы: один (5), диапазон (1-10) или список (1,3,5-8)")
        self.chans.setMaximumWidth(110)
        self.slider = QSlider(Qt.Horizontal)
        self.slider.setRange(0, 255)
        self.slider.setValue(row.value)
        self.val = QSpinBox()
        self.val.setRange(0, 255)
        self.val.setValue(row.value)
        self.pct = QLabel()
        self.pct.setObjectName("hint")
        self.pct.setMinimumWidth(38)
        rm = QPushButton("✕")
        rm.setToolTip("Удалить строку")
        rm.setFixedWidth(32)
        lay.addWidget(self.uni)
        lay.addWidget(self.chans)
        lay.addWidget(self.slider, 1)
        lay.addWidget(self.val)
        lay.addWidget(self.pct)
        lay.addWidget(rm)
        self._update_pct()
        self.uni.valueChanged.connect(self._on_uni)
        self.chans.textEdited.connect(self._on_chans)
        self.slider.valueChanged.connect(self._on_slider)
        self.val.valueChanged.connect(self._on_val)
        rm.clicked.connect(lambda: self.remove.emit(self))

    def _update_pct(self):
        self.pct.setText(f"{round(self.row.value * 100 / 255)}%")

    def _on_uni(self, v):
        self.row.universe = v
        self.changed.emit()

    def _on_chans(self, t):
        self.row.channels = t
        self.changed.emit()

    def _on_slider(self, v):
        if self.val.value() != v:
            self.val.setValue(v)

    def _on_val(self, v):
        if self.slider.value() != v:
            self.slider.setValue(v)
        self.row.value = v
        self._update_pct()
        self.changed.emit()


class Inspector(QFrame):
    """Сигнал changed(cue, structural): structural=True — изменилось время/тип/вкл, нужно пересобрать таймлайн."""

    changed = Signal(object, bool)
    fire_requested = Signal(object)
    stop_requested = Signal(object)

    def __init__(self, engine, parent=None):
        super().__init__(parent)
        self.setObjectName("panel")
        self.engine = engine
        self.cue: Cue | None = None
        self._loading = False

        outer = QVBoxLayout(self)
        outer.setContentsMargins(14, 12, 14, 12)
        outer.setSpacing(10)

        head = QHBoxLayout()
        self.type_badge = QLabel("")
        self.type_badge.setStyleSheet("font-weight:700; padding:3px 10px; border-radius:6px;")
        self.title = QLabel("Выберите кью")
        self.title.setStyleSheet("font-size:13pt; font-weight:600;")
        head.addWidget(self.type_badge)
        head.addWidget(self.title, 1)
        outer.addLayout(head)

        self.empty = _hint("Выберите кью в списке слева или добавьте новую кнопками «+ Звук», «+ DMX», «+ OSC».")
        outer.addWidget(self.empty)

        self.body = QWidget()
        body = QVBoxLayout(self.body)
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(10)

        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignRight | Qt.AlignVCenter)
        form.setHorizontalSpacing(12)
        form.setVerticalSpacing(8)
        self.number = QLineEdit()
        self.number.setMaximumWidth(90)
        self.name = QLineEdit()
        self.name.setPlaceholderText("Например: Выход ведущего")
        trig = QHBoxLayout()
        self.trigger = QLineEdit()
        self.trigger.setObjectName("tcEdit")
        self.trigger.setPlaceholderText("пусто — только по GO")
        self.trigger.setToolTip("Таймкод, на котором кью сработает сама.\n"
                                "Форматы: 01:00:05:12 · 1:05:12 (ч:м:с) · 2:30 (м:с) · 90 (секунды)")
        self.btn_now = QPushButton("⏱ Текущий")
        self.btn_now.setToolTip("Подставить текущее время транспорта (Ctrl+T)")
        self.btn_clear_tc = QPushButton("✕")
        self.btn_clear_tc.setToolTip("Убрать таймкод — кью будет только по GO")
        self.btn_clear_tc.setFixedWidth(32)
        trig.addWidget(self.trigger, 1)
        trig.addWidget(self.btn_now)
        trig.addWidget(self.btn_clear_tc)
        self.trigger_state = QLabel("")
        self.trigger_state.setObjectName("hint")
        self.enabled = QCheckBox("Кью включена")
        form.addRow("Номер", self.number)
        form.addRow("Название", self.name)
        form.addRow("Таймкод", trig)
        form.addRow("", self.trigger_state)
        form.addRow("", self.enabled)
        body.addLayout(form)

        line = QFrame()
        line.setFrameShape(QFrame.HLine)
        line.setStyleSheet("color:#2e3440;")
        body.addWidget(line)

        self.pages = QStackedWidget()
        self.pages.addWidget(self._build_audio())
        self.pages.addWidget(self._build_dmx())
        self.pages.addWidget(self._build_osc())
        body.addWidget(self.pages)

        notes_lab = QLabel("ЗАМЕТКИ")
        notes_lab.setObjectName("sectionTitle")
        self.notes = QLineEdit()
        self.notes.setPlaceholderText("Для себя: что происходит в этот момент")
        body.addWidget(notes_lab)
        body.addWidget(self.notes)

        actions = QHBoxLayout()
        self.btn_fire = QPushButton("▶  Запустить эту кью")
        self.btn_fire.setObjectName("primary")
        self.btn_stop = QPushButton("■ Стоп")
        actions.addWidget(self.btn_fire, 1)
        actions.addWidget(self.btn_stop)
        body.addLayout(actions)
        body.addStretch(1)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setWidget(self.body)
        scroll.setStyleSheet("QScrollArea { background: transparent; } QScrollArea > QWidget > QWidget { background: transparent; }")
        outer.addWidget(scroll, 1)

        self.number.textEdited.connect(lambda t: self._set("number", t, False))
        self.name.textEdited.connect(lambda t: self._set("name", t, False))
        self.trigger.editingFinished.connect(self._on_trigger)
        self.trigger.textEdited.connect(self._validate_trigger)
        self.btn_now.clicked.connect(self.set_trigger_now)
        self.btn_clear_tc.clicked.connect(self._clear_trigger)
        self.enabled.toggled.connect(lambda v: self._set("enabled", v, True))
        self.notes.textEdited.connect(lambda t: self._set("notes", t, False))
        self.btn_fire.clicked.connect(lambda: self.cue and self.fire_requested.emit(self.cue))
        self.btn_stop.clicked.connect(lambda: self.cue and self.stop_requested.emit(self.cue))
        self.set_cue(None)

    # ---------------------------------------------------------------- страницы
    def _build_audio(self) -> QWidget:
        w = QWidget()
        form = QFormLayout(w)
        form.setContentsMargins(0, 0, 0, 0)
        form.setLabelAlignment(Qt.AlignRight | Qt.AlignVCenter)
        form.setHorizontalSpacing(12)
        row = QHBoxLayout()
        self.a_file = QLineEdit()
        self.a_file.setReadOnly(True)
        self.a_file.setPlaceholderText("Файл не выбран")
        browse = QPushButton("Выбрать…")
        browse.clicked.connect(self._browse_audio)
        row.addWidget(self.a_file, 1)
        row.addWidget(browse)
        self.a_volume = QDoubleSpinBox()
        self.a_volume.setRange(-60.0, 12.0)
        self.a_volume.setSingleStep(1.0)
        self.a_volume.setDecimals(1)
        self.a_volume.setSuffix(" дБ")
        self.a_volume.setMaximumWidth(120)
        self.a_pair = QComboBox()
        self.a_info = QLabel("")
        self.a_info.setObjectName("hint")
        form.addRow("Файл", row)
        form.addRow("Громкость", self.a_volume)
        form.addRow("Выход", self.a_pair)
        form.addRow("", self.a_info)
        form.addRow("", _hint("Если кью стоит на таймкоде, звук сам подхватится с нужного места "
                              "при перемотке и остановится вместе с транспортом."))
        self.a_volume.valueChanged.connect(lambda v: self._set("volume_db", float(v), False))
        self.a_pair.currentIndexChanged.connect(lambda i: self._set("out_pair", i + 1, False) if i >= 0 else None)
        return w

    def _build_dmx(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(8)
        self.d_clear = QCheckBox("Сначала обнулить все каналы (blackout)")
        lay.addWidget(self.d_clear)
        hdr = QHBoxLayout()
        for text, stretch in (("Вселенная", 0), ("Каналы", 0), ("Значение 0–255", 1)):
            lab = QLabel(text)
            lab.setObjectName("sectionTitle")
            hdr.addWidget(lab, stretch)
        lay.addLayout(hdr)
        self.d_rows_box = QVBoxLayout()
        self.d_rows_box.setSpacing(6)
        lay.addLayout(self.d_rows_box)
        add = QPushButton("+ Добавить каналы")
        add.clicked.connect(self._add_dmx_row)
        lay.addWidget(add, 0, Qt.AlignLeft)
        lay.addWidget(_hint("Значения держатся, пока другая кью их не изменит. "
                            "Куда уходит каждая вселенная — в Настройках → DMX."))
        self.d_clear.toggled.connect(lambda v: self._set("dmx_clear", v, False))
        return w

    def _build_osc(self) -> QWidget:
        w = QWidget()
        form = QFormLayout(w)
        form.setContentsMargins(0, 0, 0, 0)
        form.setLabelAlignment(Qt.AlignRight | Qt.AlignVCenter)
        form.setHorizontalSpacing(12)
        self.o_target = QComboBox()
        self.o_address = QLineEdit()
        self.o_address.setPlaceholderText("/cue/1/go")
        self.o_args = QLineEdit()
        self.o_args.setPlaceholderText('например: 1 0.5 "текст"')
        self.o_args.setToolTip("Аргументы через пробел:\n"
                               "  5 → целое,  0.5 → дробное,  true/false → логическое,\n"
                               '  "текст с пробелами" → строка.\n'
                               "Принудительно: i:5  f:5  s:5")
        self.o_preview = QLabel("")
        self.o_preview.setObjectName("hint")
        self.o_preview.setWordWrap(True)
        form.addRow("Куда", self.o_target)
        form.addRow("Адрес", self.o_address)
        form.addRow("Аргументы", self.o_args)
        form.addRow("", self.o_preview)
        form.addRow("", _hint("Получателей (IP и порт) настраивают в Настройках → OSC."))
        self.o_target.currentTextChanged.connect(lambda t: self._set("osc_target", t, False) if t else None)
        self.o_address.textEdited.connect(lambda t: self._set("osc_address", t, False))
        self.o_args.textEdited.connect(self._on_osc_args)
        return w

    # ---------------------------------------------------------------- данные
    def refresh_choices(self) -> None:
        """Обновляет списки выходов звука и получателей OSC из настроек."""
        st = self.engine.settings
        self._loading = True
        ch = max(2, int(st["audio"].get("channels", 2)))
        self.a_pair.clear()
        for p in range(1, ch // 2 + 1):
            self.a_pair.addItem(f"Каналы {2 * p - 1}–{2 * p}")
        self.o_target.clear()
        for t in st["osc"].get("targets", []):
            self.o_target.addItem(t.get("name", ""))
        self._loading = False
        if self.cue is not None:
            self.set_cue(self.cue)

    def set_cue(self, cue: Cue | None) -> None:
        self.cue = cue
        self._loading = True
        try:
            has = cue is not None
            self.body.setVisible(has)
            self.empty.setVisible(not has)
            if not has:
                self.type_badge.setText("")
                self.type_badge.setVisible(False)
                self.title.setText("Свойства кью")
                return
            color = TYPE_COLORS.get(cue.type, MUTED)
            self.type_badge.setVisible(True)
            self.type_badge.setText(CUE_TYPES.get(cue.type, cue.type))
            self.type_badge.setStyleSheet(f"font-weight:700; padding:3px 10px; border-radius:6px;"
                                          f"background:{color}; color:#10131a;")
            self.title.setText(f"Кью {cue.number}")
            self.number.setText(cue.number)
            self.name.setText(cue.name)
            self.trigger.setText(cue.trigger)
            self.enabled.setChecked(cue.enabled)
            self.notes.setText(cue.notes)
            self._validate_trigger(cue.trigger)
            self.pages.setCurrentIndex({"audio": 0, "dmx": 1, "osc": 2}.get(cue.type, 2))
            if cue.type == "audio":
                self.a_file.setText(cue.file)
                self.a_file.setToolTip(cue.file)
                self.a_volume.setValue(cue.volume_db)
                idx = max(0, cue.out_pair - 1)
                if idx >= self.a_pair.count():
                    self.a_pair.addItem(f"Каналы {2 * idx + 1}–{2 * idx + 2} (нет в настройках)")
                    idx = self.a_pair.count() - 1
                self.a_pair.setCurrentIndex(idx)
                self._update_audio_info()
            elif cue.type == "dmx":
                self.d_clear.setChecked(cue.dmx_clear)
                self._rebuild_dmx_rows()
            else:
                if self.o_target.findText(cue.osc_target) < 0 and cue.osc_target:
                    self.o_target.addItem(cue.osc_target)
                self.o_target.setCurrentText(cue.osc_target)
                self.o_address.setText(cue.osc_address)
                self.o_args.setText(cue.osc_args)
                self._update_osc_preview()
        finally:
            self._loading = False

    def _set(self, attr: str, value, structural: bool) -> None:
        if self._loading or self.cue is None:
            return
        setattr(self.cue, attr, value)
        if attr == "number":
            self.title.setText(f"Кью {value}")
        self.changed.emit(self.cue, structural)

    # ---------------------------------------------------------------- таймкод
    def _validate_trigger(self, text: str) -> bool:
        t = text.strip()
        if not t:
            self.trigger_state.setText("Срабатывает только по кнопке GO")
            self.trigger_state.setStyleSheet("")
            return True
        frames = parse_tc(t, self.engine.fr)
        if frames is None:
            self.trigger_state.setText("Не понимаю время. Пример: 00:01:30:00 или 1:30")
            self.trigger_state.setStyleSheet(f"color:{RED};")
            return False
        self.trigger_state.setText(f"Сработает на {format_tc(frames, self.engine.fr)} и по GO")
        self.trigger_state.setStyleSheet("")
        return True

    def _on_trigger(self) -> None:
        if self._loading or self.cue is None:
            return
        text = self.trigger.text().strip()
        if not self._validate_trigger(text):
            return
        if text:
            text = format_tc(parse_tc(text, self.engine.fr), self.engine.fr)
            self.trigger.setText(text)
        if text != self.cue.trigger:
            self._set("trigger", text, True)

    def set_trigger_now(self) -> None:
        if self.cue is None:
            return
        text = format_tc(self.engine.position(), self.engine.fr)
        self.trigger.setText(text)
        self._validate_trigger(text)
        self._set("trigger", text, True)

    def _clear_trigger(self) -> None:
        self.trigger.setText("")
        self._validate_trigger("")
        self._set("trigger", "", True)

    # ---------------------------------------------------------------- звук
    def _browse_audio(self) -> None:
        if self.cue is None:
            return
        start = os.path.dirname(self.cue.file) if self.cue.file else ""
        path, _ = QFileDialog.getOpenFileName(self, "Звуковой файл", start, AUDIO_FILTER)
        if not path:
            return
        self.a_file.setText(path)
        self.a_file.setToolTip(path)
        if not self.cue.name:
            self.cue.name = os.path.splitext(os.path.basename(path))[0]
            self.name.setText(self.cue.name)
        self._set("file", path, True)
        self._update_audio_info()

    def _update_audio_info(self) -> None:
        if self.cue is None or self.cue.type != "audio":
            return
        if not self.cue.file:
            self.a_info.setText("")
            return
        if not os.path.exists(self.cue.file):
            self.a_info.setText("Файл не найден на диске")
            self.a_info.setStyleSheet(f"color:{RED};")
            return
        self.a_info.setStyleSheet("")
        dur = self.engine.audio_duration(self.cue)
        if dur is None:
            self.a_info.setText("Загружается…")
        else:
            m, s = divmod(dur, 60)
            self.a_info.setText(f"Длительность {int(m)}:{s:04.1f}")

    def tick(self) -> None:
        """Вызывается таймером главного окна — обновляет «Загружается…» на длительность."""
        if self.cue is not None and self.cue.type == "audio" and self.a_info.text() == "Загружается…":
            self._update_audio_info()

    # ---------------------------------------------------------------- DMX
    def _rebuild_dmx_rows(self) -> None:
        while self.d_rows_box.count():
            item = self.d_rows_box.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()
        if self.cue is None:
            return
        for r in self.cue.dmx_rows:
            rw = DmxRowWidget(r)
            rw.changed.connect(lambda: self._set("dmx_rows", self.cue.dmx_rows, False))
            rw.remove.connect(self._remove_dmx_row)
            self.d_rows_box.addWidget(rw)

    def _add_dmx_row(self) -> None:
        if self.cue is None:
            return
        last = self.cue.dmx_rows[-1] if self.cue.dmx_rows else None
        self.cue.dmx_rows.append(DmxRow(last.universe if last else 1, "1", 255))
        self._rebuild_dmx_rows()
        self._set("dmx_rows", self.cue.dmx_rows, False)

    def _remove_dmx_row(self, rw: DmxRowWidget) -> None:
        if self.cue is None:
            return
        try:
            self.cue.dmx_rows.remove(rw.row)
        except ValueError:
            pass
        self._rebuild_dmx_rows()
        self._set("dmx_rows", self.cue.dmx_rows, False)

    # ---------------------------------------------------------------- OSC
    def _on_osc_args(self, text: str) -> None:
        self._set("osc_args", text, False)
        self._update_osc_preview()

    def _update_osc_preview(self) -> None:
        if self.cue is None:
            return
        try:
            args = parse_args(self.cue.osc_args)
        except Exception as e:
            self.o_preview.setText(f"Ошибка в аргументах: {e}")
            return
        if not args:
            self.o_preview.setText("Без аргументов")
            return
        names = {bool: "логическое", int: "целое", float: "дробное", str: "строка"}
        self.o_preview.setText("Отправится: " + ",  ".join(f"{a!r} ({names.get(type(a), '?')})" for a in args))
