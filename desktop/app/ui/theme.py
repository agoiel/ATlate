"""Тема оформления: те же цвета, что в Android-версии (взяты с иконки приложения).

Светлая или тёмная тема — по настройке Windows или вручную.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import QByteArray, QRectF, Qt
from PySide6.QtGui import QColor, QGuiApplication, QIcon, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer

# ---------------------------------------------------------------- бренд

INK = "#3D2B28"      # тёмные волосы
COCOA = "#6B4A33"    # тёплый коричневый
SAND = "#AC8653"     # фон иконки
CREAM = "#F3E3CC"    # кожа и страницы книги
BOW = "#8D2A32"      # бант
HERO_END = "#7A5638"


@dataclass(frozen=True)
class Palette:
    dark: bool
    bg: str
    surface: str
    surface_alt: str
    border: str
    text: str
    text2: str
    primary: str
    on_primary: str
    selected: str
    hover: str


LIGHT = Palette(False, "#F7EFE4", "#FFFFFF", "#FBF6EF", "#EBDCC8", INK, "#7C6455",
                COCOA, "#FFFFFF", CREAM, "#F4E9DA")
DARK = Palette(True, "#211816", "#33241F", "#3F2D27", "#574036", "#F5E9DA", "#C9B29A",
               "#D9B27C", INK, "#574036", "#4A362F")

_current = LIGHT


def palette() -> Palette:
    return _current


def system_is_dark() -> bool:
    try:
        return QGuiApplication.styleHints().colorScheme() == Qt.ColorScheme.Dark
    except AttributeError:   # Qt старше 6.5
        return False


def apply(app, mode: str = "system") -> Palette:
    """Включить тему для всего приложения."""
    global _current
    dark = system_is_dark() if mode == "system" else mode == "dark"
    _current = DARK if dark else LIGHT
    app.setStyle("Fusion")
    app.setStyleSheet(stylesheet(_current))
    return _current


def font_family() -> str:
    return "Segoe UI Variable Text" if sys.platform == "win32" else "Segoe UI"


# ---------------------------------------------------------------- стили

def stylesheet(c: Palette) -> str:
    f = font_family()
    return f"""
* {{ font-family: "{f}", "Segoe UI", sans-serif; font-size: 10pt; color: {c.text}; }}
QWidget#root, QScrollArea, QScrollArea > QWidget > QWidget#content {{ background: {c.bg}; }}
QToolTip {{ background: {INK}; color: {CREAM}; border: none; padding: 6px 8px; border-radius: 6px; }}

QFrame#card {{ background: {c.surface}; border: 1px solid {c.border}; border-radius: 18px; }}
QLabel#cardTitle {{ font-size: 12pt; font-weight: 600; }}
QLabel#caption, QLabel#cardSubtitle {{ color: {c.text2}; font-size: 9pt; }}
QLabel#fieldLabel {{ color: {c.text2}; font-size: 8pt; font-weight: 600; letter-spacing: 0.5px; }}
QLabel#badge {{ background: {c.selected}; border-radius: 12px; }}
QLabel#sectionLabel {{ color: {c.primary}; font-size: 9pt; font-weight: 600; padding-top: 6px; }}

QPushButton {{ border-radius: 20px; padding: 8px 18px; font-weight: 600; min-height: 22px; }}
QPushButton[kind="primary"] {{ background: {c.primary}; color: {c.on_primary}; border: 2px solid {c.primary}; }}
QPushButton[kind="primary"]:hover {{ background: {_mix(c.primary, c.surface, 0.12)}; }}
QPushButton[kind="hero"] {{ background: {CREAM}; color: {INK}; border: 2px solid {CREAM}; }}
QPushButton[kind="hero"]:hover {{ background: #FFF4E4; }}
QPushButton[kind="heroOutline"] {{ background: rgba(255,255,255,0.08); color: {CREAM}; border: 2px solid {CREAM}; }}
QPushButton[kind="heroOutline"]:hover {{ background: rgba(255,255,255,0.16); }}
QPushButton[kind="outline"] {{ background: transparent; color: {c.primary}; border: 2px solid {c.primary}; }}
QPushButton[kind="outline"]:hover {{ background: {c.hover}; }}
QPushButton[kind="tonal"] {{ background: {c.selected}; color: {c.primary}; border: 2px solid {c.selected}; }}
QPushButton[kind="tonal"]:hover {{ background: {_mix(c.selected, c.primary, 0.12)}; }}
QPushButton:pressed {{ padding-top: 9px; padding-bottom: 7px; }}
QPushButton:disabled {{ opacity: 0.5; }}

QToolButton#tile {{ background: {c.surface}; border: 1px solid {c.border}; border-radius: 16px;
                   padding: 12px 6px 10px 6px; font-weight: 600; font-size: 9pt; }}
QToolButton#tile:hover {{ background: {c.hover}; border-color: {c.primary}; }}

QComboBox, QLineEdit, QPlainTextEdit {{ background: {c.surface_alt}; border: 1px solid {c.border};
    border-radius: 12px; padding: 8px 12px; selection-background-color: {c.primary};
    selection-color: {c.on_primary}; }}
QComboBox:hover, QLineEdit:hover {{ border-color: {c.primary}; }}
QLineEdit:focus, QPlainTextEdit:focus, QComboBox:focus {{ border: 2px solid {c.primary}; padding: 7px 11px; }}
QComboBox::drop-down {{ border: none; width: 26px; }}
QComboBox QAbstractItemView {{ background: {c.surface}; border: 1px solid {c.border}; border-radius: 10px;
    padding: 4px; outline: none; selection-background-color: {c.selected}; selection-color: {c.text}; }}

QFrame#select {{ background: {c.surface_alt}; border: 1px solid {c.border}; border-radius: 12px; }}
QFrame#select:hover {{ border-color: {c.primary}; }}
QFrame#select[open="true"] {{ background: {c.surface}; border: 2px solid {c.primary}; }}
QFrame#selectHeader {{ background: transparent; border: none; border-radius: 11px; }}
QFrame#selectItem {{ background: transparent; border: none; border-radius: 8px; }}
QFrame#selectItem:hover {{ background: {c.hover}; }}
QFrame#selectItem[checked="true"] {{ background: {c.selected}; }}
QFrame#selectItem[checked="true"] QLabel {{ font-weight: 600; }}
QFrame#dropdown {{ background: {c.surface_alt}; border: 1px solid {c.border}; border-radius: 14px; }}
QFrame#dropdown[open="true"] {{ background: {c.surface}; border: 2px solid {c.primary}; }}
QFrame#dropHeader {{ background: transparent; border: none; border-radius: 12px; }}
QFrame#dropHeader:hover {{ background: {c.hover}; }}
QScrollArea#dropListArea, QWidget#dropListContent {{ background: transparent; }}
QFrame#choice {{ background: {c.surface_alt}; border: 1px solid {c.border}; border-radius: 12px; }}
QFrame#choice:hover {{ border-color: {c.primary}; }}
QFrame#choice[checked="true"] {{ background: {c.selected}; border: 2px solid {c.primary}; }}

QFrame#segmented {{ background: rgba(255,255,255,0.12); border-radius: 21px; }}
QPushButton[kind="segment"] {{ background: transparent; color: {CREAM}; border: none; border-radius: 17px;
    padding: 7px 14px; }}
QPushButton[kind="segment"]:checked {{ background: {CREAM}; color: {INK}; }}

QPushButton[kind="round"] {{ background: {c.selected}; color: {c.primary}; border: none; border-radius: 16px;
    min-width: 32px; max-width: 32px; min-height: 32px; max-height: 32px; padding: 0; font-size: 13pt; }}
QPushButton[kind="round"]:hover {{ background: {_mix(c.selected, c.primary, 0.15)}; }}

QPushButton[kind="link"] {{ background: transparent; border: none; color: {c.primary}; padding: 4px 2px;
    text-align: left; font-weight: 600; }}
QPushButton[kind="link"]:hover {{ text-decoration: underline; }}

QFrame#collapseHeader {{ background: transparent; border: none; }}
QWidget#transparent {{ background: transparent; }}
QFrame#chip {{ background: rgba(255,255,255,0.14); border-radius: 13px; }}

QScrollBar:vertical {{ background: transparent; width: 10px; margin: 4px 2px; }}
QScrollBar::handle:vertical {{ background: {c.border}; border-radius: 3px; min-height: 40px; }}
QScrollBar::handle:vertical:hover {{ background: {c.text2}; }}
QScrollBar::add-line, QScrollBar::sub-line, QScrollBar::add-page, QScrollBar::sub-page {{ height: 0; background: none; }}

QDialog, QMessageBox {{ background: {c.surface}; }}
QFrame#segmentedPlain {{ background: {c.surface_alt}; border: 1px solid {c.border}; border-radius: 21px; }}
QFrame#segmentedPlain QPushButton[kind="segment"] {{ color: {c.text2}; }}
QFrame#segmentedPlain QPushButton[kind="segment"]:checked {{ background: {c.primary}; color: {c.on_primary}; }}
QFrame#studioPromo {{ background: {c.surface}; border: 2px solid {c.primary}; border-radius: 18px; }}
QFrame#studioBar {{ background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 {INK}, stop:1 {HERO_END}); }}
QLabel#studioTitle {{ color: {CREAM}; font-size: 13pt; font-weight: 700; }}
QLabel#studioSub {{ color: rgba(243,227,204,0.75); font-size: 9pt; }}
QFrame#studioFooter {{ background: {c.surface}; border-top: 1px solid {c.border}; }}
QWidget#studioEmpty, QSplitter#studioSplit, QSplitter#studioSplit > QWidget {{ background: {c.bg}; }}
QFrame#dropZone {{ background: {c.surface}; border: 2px dashed {c.border}; border-radius: 22px; }}
QListWidget#pageList {{ background: {c.surface}; border: none; border-right: 1px solid {c.border}; padding: 6px; }}
QListWidget#pageList::item {{ border-radius: 10px; padding: 4px; color: {c.text2}; }}
QListWidget#pageList::item:selected {{ background: {c.selected}; color: {c.text}; }}
QListWidget#pageList::item:hover {{ background: {c.hover}; }}
QGraphicsView#pageView {{ background: {_mix(c.bg, "#000000", 0.06)}; border-radius: 14px; }}
QScrollArea#studioSide {{ background: {c.bg}; }}
QFrame#blockCard {{ background: {c.surface}; border: 1px solid {c.border}; border-radius: 14px; }}
QFrame#blockCard[selected="true"] {{ border: 2px solid {c.primary}; }}
QLabel#blockNumber {{ background: {c.primary}; color: {c.on_primary}; border-radius: 12px; font-weight: 700;
    font-size: 9pt; }}
QPlainTextEdit#sourceEdit {{ color: {c.text2}; font-size: 9pt; }}
QProgressBar {{ background: {c.surface_alt}; border: none; border-radius: 5px; max-height: 10px; }}
QProgressBar::chunk {{ background: {c.primary}; border-radius: 5px; }}
QSplitter::handle {{ background: transparent; width: 6px; }}
QPushButton[kind="tonal"]:checked {{ background: {c.primary}; color: {c.on_primary}; }}
QMenu {{ background: {c.surface}; border: 1px solid {c.border}; border-radius: 10px; padding: 6px; }}
QMenu::item {{ padding: 8px 18px; border-radius: 8px; }}
QMenu::item:selected {{ background: {c.selected}; }}
QTextBrowser {{ background: {c.surface}; border: none; }}
QCheckBox {{ spacing: 8px; }}
QTableWidget {{ background: {c.surface}; border: 1px solid {c.border}; border-radius: 10px;
    gridline-color: {c.border}; }}
QHeaderView::section {{ background: {c.surface_alt}; border: none; padding: 6px; font-weight: 600; }}
"""


def _mix(a: str, b: str, t: float) -> str:
    ca, cb = QColor(a), QColor(b)
    return QColor(round(ca.red() + (cb.red() - ca.red()) * t), round(ca.green() + (cb.green() - ca.green()) * t),
                  round(ca.blue() + (cb.blue() - ca.blue()) * t)).name()


# ---------------------------------------------------------------- иконки (те же, что в Android)

_ICONS: dict[str, list[tuple[str, str]]] = {
    "play": [("fill", "M8,5.5v13l10.5,-6.5z")],
    "stop": [("fill", "M7,7h10v10H7z")],
    "pause": [("fill", "M7,5.5h3.6v13H7zM13.4,5.5H17v13h-3.6z")],
    "image": [("stroke", "M4,5h16v14H4z"), ("stroke", "M4,16l5,-5 4,4 2,-2 5,5"),
              ("fill", "M15.5,9.5m-1.5,0a1.5,1.5 0 1,0 3,0a1.5,1.5 0 1,0 -3,0")],
    "refresh": [("stroke", "M18.5,12a6.5,6.5 0 1,1 -2,-4.7"), ("stroke", "M17.5,3.8v4h-4")],
    "help": [("stroke", "M12,12m-8.5,0a8.5,8.5 0 1,0 17,0a8.5,8.5 0 1,0 -17,0"),
             ("stroke", "M9.6,9.4a2.5,2.5 0 1,1 3.6,2.3c-0.8,0.4 -1.2,1 -1.2,1.9v0.5"),
             ("fill", "M12,17m-1.1,0a1.1,1.1 0 1,0 2.2,0a1.1,1.1 0 1,0 -2.2,0")],
    "book": [("stroke", "M6.5,4h11a1,1 0 0,1 1,1v15h-11.5a2,2 0 0,1 -2,-2v-13a1,1 0 0,1 1.5,-1z"),
             ("stroke", "M5,18a2,2 0 0,1 2,-2h11.5"), ("stroke", "M9,8h6")],
    "chevron": [("stroke", "M7,10l5,5 5,-5")],
    "check": [("stroke", "M5,12.5l4.5,4.5 9.5,-10")],
    "arrow": [("stroke", "M5,12h14"), ("stroke", "M14,7l5,5 -5,5")],
    "lang": [("stroke", "M4,5h9"), ("stroke", "M8.5,3v2"), ("stroke", "M11,5c-0.8,3.5 -3,6.5 -6,8"),
             ("stroke", "M6.5,8.5c1,2 2.6,3.4 4.5,4.3"), ("stroke", "M12,20l4,-9 4,9"), ("stroke", "M13.4,17h5.2")],
    "bolt": [("stroke", "M13,3L5,13.5h6l-1,7.5 8,-10.5h-6z")],
    "tune": [("stroke", "M4,7h9"), ("stroke", "M17,7h3"), ("stroke", "M15,7m-2,0a2,2 0 1,0 4,0a2,2 0 1,0 -4,0"),
             ("stroke", "M4,17h3"), ("stroke", "M11,17h9"), ("stroke", "M9,17m-2,0a2,2 0 1,0 4,0a2,2 0 1,0 -4,0")],
    "plus": [("stroke", "M6,12h12"), ("stroke", "M12,6v12")],
    "link": [("stroke", "M10,6H6.5A1.5,1.5 0 0,0 5,7.5v10A1.5,1.5 0 0,0 6.5,19h10a1.5,1.5 0 0,0 1.5,-1.5V14"),
             ("stroke", "M13,5h6v6"), ("stroke", "M19,5l-8,8")],
    "select": [("stroke", "M4,8V5a1,1 0 0,1 1,-1h3"), ("stroke", "M16,4h3a1,1 0 0,1 1,1v3"),
               ("stroke", "M20,16v3a1,1 0 0,1 -1,1h-3"), ("stroke", "M8,20H5a1,1 0 0,1 -1,-1v-3")],
    "eye": [("stroke", "M2.5,12c2.2,-4 5.6,-6 9.5,-6s7.3,2 9.5,6c-2.2,4 -5.6,6 -9.5,6s-7.3,-2 -9.5,-6z"),
            ("stroke", "M12,12m-2.8,0a2.8,2.8 0 1,0 5.6,0a2.8,2.8 0 1,0 -5.6,0")],
    "hide": [("stroke", "M4,4l16,16"), ("stroke", "M9.9,6.3C10.6,6.1 11.3,6 12,6c3.9,0 7.3,2 9.5,6c-0.7,1.3 -1.6,2.4 -2.6,3.3"),
             ("stroke", "M6.4,7.6C4.8,8.6 3.5,10.1 2.5,12c2.2,4 5.6,6 9.5,6c1.6,0 3,-0.3 4.3,-1")],
    "list": [("stroke", "M9,6h11"), ("stroke", "M9,12h11"), ("stroke", "M9,18h11"),
             ("fill", "M5,6m-1.3,0a1.3,1.3 0 1,0 2.6,0a1.3,1.3 0 1,0 -2.6,0"),
             ("fill", "M5,12m-1.3,0a1.3,1.3 0 1,0 2.6,0a1.3,1.3 0 1,0 -2.6,0"),
             ("fill", "M5,18m-1.3,0a1.3,1.3 0 1,0 2.6,0a1.3,1.3 0 1,0 -2.6,0")],
    "keyboard": [("stroke", "M3,7h18v11H3z"), ("stroke", "M7,11h0.01"), ("stroke", "M11,11h0.01"),
                 ("stroke", "M15,11h0.01"), ("stroke", "M8,15h8")],
    "folder": [("stroke", "M3,7a1,1 0 0,1 1,-1h5l2,2h8a1,1 0 0,1 1,1v9a1,1 0 0,1 -1,1H4a1,1 0 0,1 -1,-1z")],
    "code": [("stroke", "M8,7l-5,5 5,5"), ("stroke", "M16,7l5,5 -5,5"), ("stroke", "M13.5,4.5l-3,15")],
    "brush": [("stroke", "M14.5,4.5l5,5L9,20H4v-5z"), ("stroke", "M12.5,6.5l5,5")],
    "trash": [("stroke", "M5,7h14"), ("stroke", "M9.5,7V4.5h5V7"), ("stroke", "M7,7l1,13h8l1,-13"),
              ("stroke", "M10.5,11v5"), ("stroke", "M13.5,11v5")],
    "download": [("stroke", "M12,4v11"), ("stroke", "M7,10l5,5 5,-5"), ("stroke", "M5,19.5h14")],
    "pin": [("stroke", "M9,4h6l-1,6 3,3H7l3,-3z"), ("stroke", "M12,13v7")],
}


def icon_svg(name: str, color: str) -> str:
    parts = []
    for kind, d in _ICONS.get(name, []):
        if kind == "fill":
            parts.append(f'<path fill="{color}" d="{d}"/>')
        else:
            parts.append(f'<path fill="none" stroke="{color}" stroke-width="1.8" stroke-linecap="round" '
                         f'stroke-linejoin="round" d="{d}"/>')
    return f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24">{"".join(parts)}</svg>'


def icon(name: str, color: str, size: int = 20) -> QIcon:
    return QIcon(pixmap(name, color, size))


def pixmap(name: str, color: str, size: int = 20) -> QPixmap:
    ratio = 2.0
    pm = QPixmap(int(size * ratio), int(size * ratio))
    pm.fill(Qt.GlobalColor.transparent)
    r = QSvgRenderer(QByteArray(icon_svg(name, color).encode("utf-8")))
    p = QPainter(pm)
    r.render(p, QRectF(0, 0, size * ratio, size * ratio))
    p.end()
    pm.setDevicePixelRatio(ratio)
    return pm


def resource(name: str) -> Path:
    """Файл рядом с программой (или внутри собранного exe)."""
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[2]))
    return base / name
