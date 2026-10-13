"""Запуск TimeCue.

    python main.py                 — обычный запуск
    python main.py шоу.tcshow      — открыть файл шоу
    python main.py --selftest DIR  — самопроверка без устройств: строит окна, сохраняет скриншоты в DIR
"""

from __future__ import annotations

import os
import sys
import traceback


def _resource(name: str) -> str:
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, name)


def selftest(out_dir: str) -> int:
    """Проверка, что интерфейс собирается и основные действия работают (для сборки в GitHub Actions)."""
    from PySide6.QtWidgets import QApplication

    from timecue.show import Cue, DmxRow, Show
    from timecue.ui.main_window import MainWindow
    from timecue.ui.settings_dialog import SettingsDialog
    from timecue.ui.style import build_qss

    os.makedirs(out_dir, exist_ok=True)
    app = QApplication.instance() or QApplication(sys.argv)
    app.setStyleSheet(build_qss())
    show = Show()
    show.cues = [
        Cue(type="audio", number="1", name="Интро", file="intro.wav", trigger="00:00:00:00"),
        Cue(type="dmx", number="2", name="Сцена в синий", trigger="00:00:05:00",
            dmx_rows=[DmxRow(1, "1-12", 255), DmxRow(1, "13", 128)]),
        Cue(type="osc", number="3", name="Видео: ролик 2", trigger="00:00:12:10",
            osc_address="/composition/layers/1/clips/2/connect", osc_args="1"),
        Cue(type="dmx", number="4", name="Блэкаут", dmx_clear=True),
        Cue(type="osc", number="5", name="Финал", osc_address="/cue/final", osc_args='1 0.5 "go"'),
    ]
    win = MainWindow(show, open_devices=False)
    win.resize(1360, 820)
    win.show()
    for i in range(len(show.cues)):
        win.table.selectRow(i)
        app.processEvents()
        win.inspector.grab().save(os.path.join(out_dir, f"inspector_{i + 1}_{show.cues[i].type}.png"))
    win.table.selectRow(1)
    win.engine.play()
    for _ in range(40):
        app.processEvents()
        win._on_timer()
    win.engine.pause()
    win.renumber()
    win.sort_by_time()
    win.duplicate_cue()
    win.inspector.set_trigger_now()
    win._go()
    app.processEvents()
    win._on_timer()
    win.grab().save(os.path.join(out_dir, "main_window.png"))
    dlg = SettingsDialog(show.settings, win)
    dlg.resize(820, 560)
    for t in range(dlg.tabs.count()):
        dlg.tabs.setCurrentIndex(t)
        app.processEvents()
        dlg.grab().save(os.path.join(out_dir, f"settings_{t + 1}.png"))
    dlg._add_dmx_output(None)
    dlg._add_osc_target(None)
    s = dlg.result_settings()
    assert s["dmx"]["outputs"] and s["osc"]["targets"]
    show.settings = s
    win.engine.apply_settings()
    win._after_settings_changed()
    show.modified = False
    win.close()
    print("SELFTEST OK")
    return 0


def main() -> int:
    # в собранном .exe без консоли stdout/stderr отсутствуют
    if sys.stdout is None:
        sys.stdout = open(os.devnull, "w", encoding="utf-8")
    if sys.stderr is None:
        sys.stderr = open(os.devnull, "w", encoding="utf-8")
    if len(sys.argv) >= 2 and sys.argv[1] == "--selftest":
        out = sys.argv[2] if len(sys.argv) > 2 else "selftest_out"
        try:
            return selftest(out)
        except Exception:
            traceback.print_exc()
            return 1

    from PySide6.QtGui import QIcon
    from PySide6.QtWidgets import QApplication, QMessageBox

    from timecue import APP_NAME
    from timecue.show import Show
    from timecue.ui.main_window import MainWindow
    from timecue.ui.style import build_qss

    if sys.platform.startswith("win"):
        try:  # своя иконка в панели задач Windows
            import ctypes
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("TimeCue.Player")
        except Exception:
            pass

    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setStyleSheet(build_qss())
    icon = _resource("icon.ico")
    if os.path.exists(icon):
        app.setWindowIcon(QIcon(icon))

    def excepthook(exc_type, exc, tb):
        text = "".join(traceback.format_exception(exc_type, exc, tb))
        sys.stderr.write(text)
        try:
            QMessageBox.critical(None, APP_NAME, "Произошла ошибка:\n\n" + text[-1500:])
        except Exception:
            pass

    sys.excepthook = excepthook

    win = MainWindow(Show())
    path = next((a for a in sys.argv[1:] if a.lower().endswith(".tcshow")), None)
    if path is None:
        last = win.qs.value("last_show", "")
        if last and os.path.exists(str(last)):
            path = str(last)
    if path:
        win.open_show(path)
    win.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
