"""Тёмная тема и цвета интерфейса."""

BG = "#14161b"
PANEL = "#1c1f26"
PANEL_2 = "#232731"
BORDER = "#2e3440"
TEXT = "#e7eaf0"
MUTED = "#8a93a5"
GREEN = "#2fbf71"
GREEN_DARK = "#1f8a51"
AMBER = "#f2b33d"
RED = "#e5484d"
BLUE = "#4c8dff"
VIOLET = "#a78bfa"

TYPE_COLORS = {"audio": BLUE, "dmx": AMBER, "osc": VIOLET}

QSS = f"""
* {{
    font-family: "Segoe UI", "Inter", "Arial";
    font-size: 10pt;
    color: {TEXT};
}}
QMainWindow, QDialog, QWidget#root {{ background: {BG}; }}
QFrame#panel, QFrame#topbar, QFrame#gobar {{
    background: {PANEL};
    border: 1px solid {BORDER};
    border-radius: 10px;
}}
QLabel#muted {{ color: {MUTED}; }}
QLabel#hint {{ color: {MUTED}; font-size: 9pt; }}
QLabel#sectionTitle {{ color: {MUTED}; font-size: 9pt; font-weight: 600; letter-spacing: 1px; }}
QLabel#tcDisplay {{
    font-family: "Consolas", "DejaVu Sans Mono", "Courier New";
    font-size: 44pt;
    font-weight: 600;
    color: {TEXT};
    padding: 0 6px;
}}
QLabel#nextCue {{ font-size: 14pt; font-weight: 600; }}
QLabel#lamp {{
    border-radius: 6px;
    padding: 3px 8px;
    font-size: 8pt;
    font-weight: 700;
    background: {PANEL_2};
    color: {MUTED};
    border: 1px solid {BORDER};
}}
QPushButton {{
    background: {PANEL_2};
    border: 1px solid {BORDER};
    border-radius: 7px;
    padding: 6px 12px;
}}
QPushButton:hover {{ border-color: {BLUE}; }}
QPushButton:pressed {{ background: {BORDER}; }}
QPushButton:disabled {{ color: #555c69; border-color: #262a33; }}
QPushButton#goButton {{
    background: {GREEN_DARK};
    border: 2px solid {GREEN};
    border-radius: 12px;
    font-size: 26pt;
    font-weight: 800;
    color: white;
    min-width: 170px;
    min-height: 74px;
}}
QPushButton#goButton:hover {{ background: {GREEN}; }}
QPushButton#panicButton {{
    background: #3a1d20;
    border: 2px solid {RED};
    border-radius: 12px;
    font-size: 12pt;
    font-weight: 700;
    min-height: 74px;
    min-width: 140px;
}}
QPushButton#panicButton:hover {{ background: #5a2328; }}
QPushButton#transport {{
    font-size: 15pt;
    min-width: 54px;
    min-height: 44px;
    padding: 2px 8px;
}}
QPushButton#addAudio {{ border-color: {BLUE}; }}
QPushButton#addDmx {{ border-color: {AMBER}; }}
QPushButton#addOsc {{ border-color: {VIOLET}; }}
QPushButton#primary {{ background: {GREEN_DARK}; border-color: {GREEN}; color: white; font-weight: 600; }}
QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox, QPlainTextEdit {{
    background: {BG};
    border: 1px solid {BORDER};
    border-radius: 6px;
    padding: 5px 7px;
    selection-background-color: {BLUE};
}}
QLineEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus, QComboBox:focus, QPlainTextEdit:focus {{ border-color: {BLUE}; }}
QLineEdit#tcEdit {{ font-family: "Consolas", "DejaVu Sans Mono"; font-size: 11pt; }}
QComboBox QAbstractItemView {{ background: {PANEL}; border: 1px solid {BORDER}; selection-background-color: {BLUE}; }}
QCheckBox {{ spacing: 8px; }}
QTableWidget {{
    background: {PANEL};
    alternate-background-color: #20242c;
    border: none;
    gridline-color: transparent;
    selection-background-color: #2b4a80;
    selection-color: white;
}}
QTableWidget::item {{ padding: 4px; border-bottom: 1px solid #252a33; }}
QHeaderView::section {{
    background: {PANEL};
    color: {MUTED};
    border: none;
    border-bottom: 1px solid {BORDER};
    padding: 6px 4px;
    font-weight: 600;
}}
QTabWidget::pane {{ border: 1px solid {BORDER}; border-radius: 8px; background: {PANEL}; top: -1px; }}
QTabBar::tab {{
    background: transparent;
    color: {MUTED};
    padding: 8px 14px;
    border: none;
    border-bottom: 2px solid transparent;
}}
QTabBar::tab:selected {{ color: {TEXT}; border-bottom: 2px solid {BLUE}; }}
QGroupBox {{
    border: 1px solid {BORDER};
    border-radius: 8px;
    margin-top: 14px;
    padding: 10px 8px 8px 8px;
    font-weight: 600;
}}
QGroupBox::title {{ subcontrol-origin: margin; left: 10px; padding: 0 4px; color: {MUTED}; }}
QSplitter::handle {{ background: transparent; }}
QScrollBar:vertical {{ background: transparent; width: 10px; margin: 0; }}
QScrollBar::handle:vertical {{ background: {BORDER}; border-radius: 5px; min-height: 30px; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; }}
QSlider::groove:horizontal {{ height: 6px; background: {BORDER}; border-radius: 3px; }}
QSlider::handle:horizontal {{ background: {AMBER}; width: 14px; margin: -5px 0; border-radius: 7px; }}
QSlider::sub-page:horizontal {{ background: {AMBER}; border-radius: 3px; }}
QMenuBar {{ background: {BG}; }}
QMenuBar::item:selected {{ background: {PANEL_2}; }}
QMenu {{ background: {PANEL}; border: 1px solid {BORDER}; }}
QMenu::item:selected {{ background: {BLUE}; }}
QStatusBar {{ color: {MUTED}; }}
QToolTip {{ background: {PANEL_2}; color: {TEXT}; border: 1px solid {BORDER}; padding: 6px; }}
"""


def lamp_style(color: str | None) -> str:
    if color is None:
        return ""
    return (f"background: {color}; color: #10131a; border: 1px solid {color};")
