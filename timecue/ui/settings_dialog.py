"""Окно настроек: таймкод, MTC, LTC, звук, DMX, OSC."""

from __future__ import annotations

import copy

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView, QCheckBox, QComboBox, QDialog, QDialogButtonBox, QDoubleSpinBox, QFormLayout,
    QGroupBox, QHBoxLayout, QHeaderView, QLabel, QLineEdit, QPushButton, QSpinBox, QTableWidget,
    QTabWidget, QVBoxLayout, QWidget,
)

from .. import audio as audio_mod
from .. import midi as midi_mod
from ..dmx import local_ipv4_addresses
from ..engine import SOURCE_LABELS
from ..tc import FRAME_RATES, format_tc, parse_tc, rate_from_key

NONE_ITEM = "— нет —"
DEFAULT_ITEM = "По умолчанию (системное)"
AUTO_IP = "Авто"


def _hint(text: str) -> QLabel:
    lab = QLabel(text)
    lab.setObjectName("hint")
    lab.setWordWrap(True)
    return lab


def _form(parent: QWidget) -> QFormLayout:
    f = QFormLayout(parent)
    f.setLabelAlignment(Qt.AlignRight | Qt.AlignVCenter)
    f.setHorizontalSpacing(14)
    f.setVerticalSpacing(10)
    return f


def _fill_combo(combo: QComboBox, items: list[str], current: str, empty_label: str) -> None:
    combo.clear()
    combo.addItem(empty_label, "")
    for it in items:
        combo.addItem(it, it)
    if current and current not in items:
        combo.addItem(f"{current} (не подключено)", current)
    idx = combo.findData(current)
    combo.setCurrentIndex(idx if idx >= 0 else 0)


class SettingsDialog(QDialog):
    def __init__(self, settings: dict, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Настройки")
        self.resize(820, 560)
        self.s = copy.deepcopy(settings)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(14, 14, 14, 14)
        self.tabs = QTabWidget()
        lay.addWidget(self.tabs, 1)
        self.tabs.addTab(self._tab_timecode(), "Таймкод")
        self.tabs.addTab(self._tab_mtc(), "MTC (MIDI)")
        self.tabs.addTab(self._tab_ltc(), "LTC (аудио)")
        self.tabs.addTab(self._tab_audio(), "Звук")
        self.tabs.addTab(self._tab_dmx(), "DMX")
        self.tabs.addTab(self._tab_osc(), "OSC")

        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.button(QDialogButtonBox.Ok).setText("Применить")
        bb.button(QDialogButtonBox.Ok).setObjectName("primary")
        bb.button(QDialogButtonBox.Cancel).setText("Отмена")
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)

    # ---------------------------------------------------------------- вкладки
    def _tab_timecode(self) -> QWidget:
        w = QWidget()
        f = _form(w)
        t = self.s["timecode"]
        self.fps = QComboBox()
        for fr in FRAME_RATES:
            self.fps.addItem(fr.label, fr.key)
        self.fps.setCurrentIndex(max(0, self.fps.findData(t.get("fps", "25"))))
        self.source = QComboBox()
        for key, label in SOURCE_LABELS.items():
            self.source.addItem(label, key)
        self.source.setCurrentIndex(max(0, self.source.findData(t.get("source", "internal"))))
        self.start_tc = QLineEdit(t.get("start_tc", "00:00:00:00"))
        self.start_tc.setObjectName("tcEdit")
        self.start_tc.setMaximumWidth(160)
        self.freewheel = QSpinBox()
        self.freewheel.setRange(3, 250)
        self.freewheel.setSuffix(" кадров")
        self.freewheel.setValue(int(t.get("freewheel_frames", 10)))
        self.chase_offset = QSpinBox()
        self.chase_offset.setRange(-250, 250)
        self.chase_offset.setSuffix(" кадров")
        self.chase_offset.setValue(int(t.get("chase_offset_frames", 0)))
        self.dmx_tracking = QCheckBox("При перемотке восстанавливать DMX по кью до этой точки")
        self.dmx_tracking.setChecked(bool(t.get("dmx_tracking", True)))
        f.addRow("Частота кадров", self.fps)
        f.addRow("Источник времени", self.source)
        f.addRow("", _hint("«Внутренние часы» — программа сама ведёт время и может выдавать MTC/LTC (мастер).\n"
                           "«Следовать за MTC/LTC» — время приходит извне, программа запускает кью по нему. "
                           "Выходы MTC/LTC при этом продолжают работать — получится конвертер LTC ↔ MTC."))
        f.addRow("Старт (кнопка «Стоп»)", self.start_tc)
        f.addRow("Удержание при пропадании", self.freewheel)
        f.addRow("", _hint("Сколько кадров продолжать идти, если входной таймкод на мгновение пропал."))
        f.addRow("Сдвиг входного времени", self.chase_offset)
        f.addRow("", self.dmx_tracking)
        return w

    def _tab_mtc(self) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)
        m = self.s["mtc"]
        if not midi_mod.midi_available():
            v.addWidget(_hint("⚠ MIDI недоступен на этом компьютере (не найдена библиотека rtmidi)."))
        out = QGroupBox("Выход MTC")
        fo = _form(out)
        self.mtc_out_en = QCheckBox("Выдавать MTC")
        self.mtc_out_en.setChecked(bool(m.get("out_enabled")))
        self.mtc_out_port = QComboBox()
        fo.addRow("", self.mtc_out_en)
        fo.addRow("MIDI-выход", self.mtc_out_port)
        inn = QGroupBox("Вход MTC (для режима «Следовать за MTC»)")
        fi = _form(inn)
        self.mtc_in_port = QComboBox()
        fi.addRow("MIDI-вход", self.mtc_in_port)
        refresh = QPushButton("↻ Обновить список портов")
        refresh.clicked.connect(self._refresh_midi)
        v.addWidget(out)
        v.addWidget(inn)
        v.addWidget(refresh, 0, Qt.AlignLeft)
        v.addWidget(_hint("Чтобы отдать MTC другой программе на этом же компьютере (пульт, Resolume, grandMA onPC), "
                          "установите бесплатный loopMIDI и создайте виртуальный порт — он появится в этом списке."))
        v.addStretch(1)
        self._refresh_midi()
        return w

    def _refresh_midi(self) -> None:
        m = self.s["mtc"]
        cur_out = self.mtc_out_port.currentData() if self.mtc_out_port.count() else m.get("out_port", "")
        cur_in = self.mtc_in_port.currentData() if self.mtc_in_port.count() else m.get("in_port", "")
        _fill_combo(self.mtc_out_port, midi_mod.list_outputs(), cur_out or "", NONE_ITEM)
        _fill_combo(self.mtc_in_port, midi_mod.list_inputs(), cur_in or "", NONE_ITEM)

    def _tab_ltc(self) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)
        lt = self.s["ltc"]
        if not audio_mod.audio_available():
            v.addWidget(_hint("⚠ Звук недоступен: " + audio_mod.AUDIO_IMPORT_ERROR))
        outs = [d for d, _ in audio_mod.list_devices("output")]
        ins = [d for d, _ in audio_mod.list_devices("input")]
        out = QGroupBox("Выход LTC")
        fo = _form(out)
        self.ltc_out_en = QCheckBox("Выдавать LTC")
        self.ltc_out_en.setChecked(bool(lt.get("out_enabled")))
        self.ltc_out_dev = QComboBox()
        _fill_combo(self.ltc_out_dev, outs, lt.get("out_device", ""), "Та же карта, что и для звука")
        self.ltc_out_ch = QSpinBox()
        self.ltc_out_ch.setRange(1, 64)
        self.ltc_out_ch.setPrefix("канал ")
        self.ltc_out_ch.setValue(int(lt.get("out_channel", 2)))
        self.ltc_level = QDoubleSpinBox()
        self.ltc_level.setRange(-40.0, 0.0)
        self.ltc_level.setSuffix(" дБFS")
        self.ltc_level.setValue(float(lt.get("out_level_db", -10.0)))
        self.ltc_offset = QDoubleSpinBox()
        self.ltc_offset.setRange(-500.0, 500.0)
        self.ltc_offset.setSuffix(" мс")
        self.ltc_offset.setValue(float(lt.get("out_offset_ms", 0.0)))
        fo.addRow("", self.ltc_out_en)
        fo.addRow("Звуковая карта", self.ltc_out_dev)
        fo.addRow("Канал", self.ltc_out_ch)
        fo.addRow("Уровень", self.ltc_level)
        fo.addRow("Опережение", self.ltc_offset)
        fo.addRow("", _hint("LTC — это звук: выделите под него отдельный канал и не смешивайте с музыкой. "
                            "Если приёмник отстаёт на пару кадров, увеличьте опережение."))
        inn = QGroupBox("Вход LTC (для режима «Следовать за LTC»)")
        fi = _form(inn)
        self.ltc_in_dev = QComboBox()
        _fill_combo(self.ltc_in_dev, ins, lt.get("in_device", ""), DEFAULT_ITEM)
        self.ltc_in_ch = QSpinBox()
        self.ltc_in_ch.setRange(1, 64)
        self.ltc_in_ch.setPrefix("канал ")
        self.ltc_in_ch.setValue(int(lt.get("in_channel", 1)))
        fi.addRow("Звуковая карта", self.ltc_in_dev)
        fi.addRow("Канал", self.ltc_in_ch)
        v.addWidget(out)
        v.addWidget(inn)
        v.addStretch(1)
        return w

    def _tab_audio(self) -> QWidget:
        w = QWidget()
        f = _form(w)
        a = self.s["audio"]
        if not audio_mod.audio_available():
            f.addRow("", _hint("⚠ Звук недоступен: " + audio_mod.AUDIO_IMPORT_ERROR))
        self.audio_dev = QComboBox()
        _fill_combo(self.audio_dev, [d for d, _ in audio_mod.list_devices("output")], a.get("device", ""), DEFAULT_ITEM)
        self.audio_ch = QSpinBox()
        self.audio_ch.setRange(2, 64)
        self.audio_ch.setSingleStep(2)
        self.audio_ch.setValue(int(a.get("channels", 2)))
        self.audio_sr = QComboBox()
        for sr in (44100, 48000, 96000):
            self.audio_sr.addItem(f"{sr} Гц", sr)
        self.audio_sr.setCurrentIndex(max(0, self.audio_sr.findData(int(a.get("sample_rate", 48000)))))
        self.audio_bs = QComboBox()
        for bs in (128, 256, 512, 1024):
            self.audio_bs.addItem(f"{bs} сэмплов", bs)
        self.audio_bs.setCurrentIndex(max(0, self.audio_bs.findData(int(a.get("blocksize", 256)))))
        f.addRow("Звуковая карта", self.audio_dev)
        f.addRow("Каналов выхода", self.audio_ch)
        f.addRow("Частота", self.audio_sr)
        f.addRow("Буфер", self.audio_bs)
        f.addRow("", _hint("Для многоканальных карт лучше выбирать вариант с пометкой [WASAPI]. "
                           "Меньший буфер — меньше задержка, но выше риск щелчков."))
        return w

    def _tab_dmx(self) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)
        d = self.s["dmx"]
        v.addWidget(_hint("Каждая строка — одна вселенная. «Вселенная в кью» — номер, который вы пишете в DMX-кью. "
                          "Её можно отправить по Art-Net или sACN на любую ноду. Пустой IP: Art-Net уйдёт "
                          "широковещательно, sACN — мультикастом. Если нода принимает только с определённого адреса, "
                          "выберите нужную сетевую карту."))
        self.dmx_table = QTableWidget(0, 6)
        self.dmx_table.setHorizontalHeaderLabels(["Вкл", "Вселенная в кью", "Протокол", "Номер в сети", "IP получателя", "Сетевая карта"])
        self.dmx_table.verticalHeader().setVisible(False)
        self.dmx_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.dmx_table.setSelectionMode(QAbstractItemView.SingleSelection)
        hh = self.dmx_table.horizontalHeader()
        hh.setSectionResizeMode(QHeaderView.ResizeToContents)
        hh.setSectionResizeMode(4, QHeaderView.Stretch)
        self._local_ips = local_ipv4_addresses()
        for o in d.get("outputs", []):
            self._add_dmx_output(o)
        v.addWidget(self.dmx_table, 1)
        row = QHBoxLayout()
        add = QPushButton("+ Вселенная")
        add.clicked.connect(lambda: self._add_dmx_output(None))
        rm = QPushButton("− Удалить выбранную")
        rm.clicked.connect(lambda: self._remove_selected(self.dmx_table))
        row.addWidget(add)
        row.addWidget(rm)
        row.addStretch(1)
        v.addLayout(row)
        g = QGroupBox("Общие")
        f = _form(g)
        self.dmx_hz = QSpinBox()
        self.dmx_hz.setRange(1, 44)
        self.dmx_hz.setSuffix(" Гц")
        self.dmx_hz.setValue(int(round(float(d.get("refresh_hz", 30)))))
        self.sacn_prio = QSpinBox()
        self.sacn_prio.setRange(0, 200)
        self.sacn_prio.setValue(int(d.get("sacn_priority", 100)))
        self.dmx_name = QLineEdit(str(d.get("source_name", "TimeCue")))
        self.artnet_bcast = QLineEdit(str(d.get("artnet_broadcast", "255.255.255.255")))
        f.addRow("Частота отправки", self.dmx_hz)
        f.addRow("Приоритет sACN", self.sacn_prio)
        f.addRow("Имя источника", self.dmx_name)
        f.addRow("Широковещательный адрес Art-Net", self.artnet_bcast)
        v.addWidget(g)
        return w

    def _add_dmx_output(self, o: dict | None) -> None:
        if o is None:
            n = self.dmx_table.rowCount() + 1
            o = {"enabled": True, "universe": n, "protocol": "artnet", "net_universe": n - 1, "dest_ip": "", "local_ip": ""}
        r = self.dmx_table.rowCount()
        self.dmx_table.insertRow(r)
        en = QCheckBox()
        en.setChecked(bool(o.get("enabled", True)))
        en.setStyleSheet("margin-left:12px;")
        uni = QSpinBox()
        uni.setRange(1, 999)
        uni.setValue(int(o.get("universe", 1)))
        proto = QComboBox()
        proto.addItem("Art-Net", "artnet")
        proto.addItem("sACN (E1.31)", "sacn")
        proto.setCurrentIndex(1 if o.get("protocol") == "sacn" else 0)
        net = QSpinBox()
        net.setRange(0, 63999)
        net.setValue(int(o.get("net_universe", 0)))
        ip = QLineEdit(str(o.get("dest_ip", "")))
        ip.setPlaceholderText("широковещательно / мультикаст")
        nic = QComboBox()
        nic.setEditable(True)
        nic.addItem(AUTO_IP)
        for a in self._local_ips:
            nic.addItem(a)
        cur = str(o.get("local_ip", ""))
        nic.setCurrentText(cur if cur else AUTO_IP)

        def on_proto(_i, proto=proto, net=net):
            # подсказка: Art-Net считает вселенные с 0, sACN — с 1
            if proto.currentData() == "sacn" and net.value() == 0:
                net.setValue(1)

        proto.currentIndexChanged.connect(on_proto)
        for c, wdg in enumerate((en, uni, proto, net, ip, nic)):
            self.dmx_table.setCellWidget(r, c, wdg)
        self.dmx_table.setRowHeight(r, 40)

    def _tab_osc(self) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)
        v.addWidget(_hint("Получатели OSC-сообщений. В OSC-кью выбирается получатель по имени."))
        self.osc_table = QTableWidget(0, 3)
        self.osc_table.setHorizontalHeaderLabels(["Имя", "IP-адрес", "Порт"])
        self.osc_table.verticalHeader().setVisible(False)
        self.osc_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.osc_table.setSelectionMode(QAbstractItemView.SingleSelection)
        hh = self.osc_table.horizontalHeader()
        hh.setSectionResizeMode(QHeaderView.Stretch)
        for t in self.s["osc"].get("targets", []):
            self._add_osc_target(t)
        v.addWidget(self.osc_table, 1)
        row = QHBoxLayout()
        add = QPushButton("+ Получатель")
        add.clicked.connect(lambda: self._add_osc_target(None))
        rm = QPushButton("− Удалить выбранного")
        rm.clicked.connect(lambda: self._remove_selected(self.osc_table))
        row.addWidget(add)
        row.addWidget(rm)
        row.addStretch(1)
        v.addLayout(row)
        return w

    def _add_osc_target(self, t: dict | None) -> None:
        if t is None:
            t = {"name": f"Получатель {self.osc_table.rowCount() + 1}", "host": "127.0.0.1", "port": 8000}
        r = self.osc_table.rowCount()
        self.osc_table.insertRow(r)
        name = QLineEdit(str(t.get("name", "")))
        host = QLineEdit(str(t.get("host", "127.0.0.1")))
        port = QSpinBox()
        port.setRange(1, 65535)
        port.setValue(int(t.get("port", 8000)))
        for c, wdg in enumerate((name, host, port)):
            self.osc_table.setCellWidget(r, c, wdg)
        self.osc_table.setRowHeight(r, 40)

    def _remove_selected(self, table: QTableWidget) -> None:
        r = table.currentRow()
        if r < 0 and table.rowCount():
            r = table.rowCount() - 1
        if r >= 0:
            table.removeRow(r)

    # ---------------------------------------------------------------- результат
    def result_settings(self) -> dict:
        s = self.s
        t = s["timecode"]
        t["fps"] = self.fps.currentData()
        t["source"] = self.source.currentData()
        fr = rate_from_key(t["fps"])
        st = parse_tc(self.start_tc.text(), fr)
        t["start_tc"] = format_tc(st if st is not None else 0, fr)
        t["freewheel_frames"] = self.freewheel.value()
        t["chase_offset_frames"] = self.chase_offset.value()
        t["dmx_tracking"] = self.dmx_tracking.isChecked()

        m = s["mtc"]
        m["out_enabled"] = self.mtc_out_en.isChecked()
        m["out_port"] = self.mtc_out_port.currentData() or ""
        m["in_port"] = self.mtc_in_port.currentData() or ""

        lt = s["ltc"]
        lt["out_enabled"] = self.ltc_out_en.isChecked()
        lt["out_device"] = self.ltc_out_dev.currentData() or ""
        lt["out_channel"] = self.ltc_out_ch.value()
        lt["out_level_db"] = self.ltc_level.value()
        lt["out_offset_ms"] = self.ltc_offset.value()
        lt["in_device"] = self.ltc_in_dev.currentData() or ""
        lt["in_channel"] = self.ltc_in_ch.value()

        a = s["audio"]
        a["device"] = self.audio_dev.currentData() or ""
        a["channels"] = self.audio_ch.value()
        a["sample_rate"] = int(self.audio_sr.currentData())
        a["blocksize"] = int(self.audio_bs.currentData())

        outs = []
        for r in range(self.dmx_table.rowCount()):
            en, uni, proto, net, ip, nic = (self.dmx_table.cellWidget(r, c) for c in range(6))
            local = nic.currentText().strip()
            outs.append({"enabled": en.isChecked(), "universe": uni.value(), "protocol": proto.currentData(),
                         "net_universe": net.value(), "dest_ip": ip.text().strip(),
                         "local_ip": "" if local in ("", AUTO_IP) else local})
        d = s["dmx"]
        d["outputs"] = outs
        d["refresh_hz"] = float(self.dmx_hz.value())
        d["sacn_priority"] = self.sacn_prio.value()
        d["source_name"] = self.dmx_name.text().strip() or "TimeCue"
        d["artnet_broadcast"] = self.artnet_bcast.text().strip() or "255.255.255.255"

        targets = []
        for r in range(self.osc_table.rowCount()):
            name, host, port = (self.osc_table.cellWidget(r, c) for c in range(3))
            if name.text().strip():
                targets.append({"name": name.text().strip(), "host": host.text().strip() or "127.0.0.1",
                                "port": port.value()})
        s["osc"]["targets"] = targets
        return s
