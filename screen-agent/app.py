#!/usr/bin/env python3
"""Screen Agent - desktop app: chess board analysis + screen tools."""
from __future__ import annotations

import contextlib
import io
import json
import os
import sys
import time
import webbrowser

import numpy as np
import chess
import chess.svg
from PySide6.QtCore import (QByteArray, QObject, QPointF, QRect, QRectF,
                            QRunnable, Qt, QThreadPool, QTimer, Signal)
from PySide6.QtGui import (QColor, QFont, QGuiApplication, QImage,
                           QKeySequence, QPainter, QPalette, QPen, QPixmap,
                           QPolygonF, QShortcut)
from PySide6.QtSvgWidgets import QSvgWidget
from PySide6.QtWidgets import (QAbstractItemView, QApplication, QCheckBox,
                               QComboBox, QFileDialog, QFrame, QGridLayout,
                               QHBoxLayout, QHeaderView, QLabel, QLineEdit,
                               QListWidget, QListWidgetItem, QMainWindow,
                               QPlainTextEdit, QPushButton, QScrollArea, QSizePolicy,
                               QSlider, QSpinBox, QStackedWidget,
                               QTableWidget, QTableWidgetItem, QVBoxLayout,
                               QWidget)

import chess_vision as cv
import screen_agent as sa

HERE = os.path.dirname(os.path.abspath(__file__))
SETTINGS_FILE = os.path.join(HERE, "settings.json")

ACCENT = "#7c6cff"
GOOD, WARN, BAD = "#3ddc97", "#ffb547", "#ff6b6b"

STYLE = f"""
* {{ color: #e7e9f0; font-family: "Segoe UI Variable", "Segoe UI", "Inter", sans-serif; font-size: 10.5pt; }}
#Root {{ background: #0e1016; }}
#Sidebar {{ background: #13161e; border-right: 1px solid #1f2330; }}
#Brand {{ font-size: 15pt; font-weight: 700; padding: 6px 8px 18px 8px; }}
#BrandSub {{ color: #7d849a; font-size: 9pt; padding: 0 8px 14px 8px; }}
QPushButton#Nav {{ text-align: left; padding: 11px 14px; border: none; border-radius: 10px;
                   background: transparent; color: #a3a9bb; font-size: 11pt; }}
QPushButton#Nav:hover {{ background: #1a1e29; color: #e7e9f0; }}
QPushButton#Nav:checked {{ background: #232838; color: white; font-weight: 600; }}
#Title {{ font-size: 19pt; font-weight: 700; }}
#Subtitle {{ color: #8a90a6; }}
#Card {{ background: #161922; border: 1px solid #222736; border-radius: 16px; }}
#CardTitle {{ font-size: 11.5pt; font-weight: 650; }}
#Muted {{ color: #8a90a6; font-size: 9.5pt; }}
QPushButton {{ background: #212534; border: 1px solid #2c3145; border-radius: 10px; padding: 8px 14px; min-height: 20px; }}
QPushButton:hover {{ background: #2a2f42; border-color: #3a4060; }}
QPushButton:pressed {{ background: #1c2030; }}
QPushButton:disabled {{ color: #5b6175; background: #1a1d28; border-color: #23273a; }}
QPushButton#Primary {{ background: {ACCENT}; border: none; color: white; font-weight: 650;
                       font-size: 12pt; padding: 12px 18px; }}
QPushButton#Primary:hover {{ background: #8d7fff; }}
QPushButton#Primary:disabled {{ background: #3b3570; color: #b9b3e6; }}
QLineEdit, QComboBox, QSpinBox, QPlainTextEdit, QListWidget, QTableWidget {{
    background: #10131a; border: 1px solid #262b3c; border-radius: 9px; padding: 6px 8px;
    selection-background-color: {ACCENT}; }}
QLineEdit:focus, QComboBox:focus, QSpinBox:focus, QPlainTextEdit:focus {{ border-color: {ACCENT}; }}
QComboBox::drop-down {{ border: none; width: 22px; }}
QComboBox QAbstractItemView {{ background: #161922; border: 1px solid #262b3c; selection-background-color: #2a2f42; }}
QPlainTextEdit {{ font-family: "Cascadia Mono", "Consolas", monospace; font-size: 10pt; }}
QListWidget::item {{ padding: 8px; border-radius: 8px; margin: 2px; }}
QListWidget::item:selected {{ background: #232838; }}
QHeaderView::section {{ background: #161922; border: none; color: #8a90a6; padding: 6px; }}
QTableWidget {{ gridline-color: #1f2330; }}
QSlider::groove:horizontal {{ height: 6px; background: #262b3c; border-radius: 3px; }}
QSlider::sub-page:horizontal {{ background: {ACCENT}; border-radius: 3px; }}
QSlider::handle:horizontal {{ background: white; width: 16px; margin: -6px 0; border-radius: 8px; }}
QCheckBox::indicator {{ width: 18px; height: 18px; border-radius: 5px; border: 1px solid #3a4060; background: #10131a; }}
QCheckBox::indicator:checked {{ background: {ACCENT}; border-color: {ACCENT}; }}
QScrollBar:vertical {{ background: transparent; width: 10px; }}
QScrollBar::handle:vertical {{ background: #2a2f42; border-radius: 5px; min-height: 30px; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; }}
#StatusBar {{ background: #13161e; border-top: 1px solid #1f2330; }}
"""

BOARD_COLORS = {"square light": "#dfe3ee", "square dark": "#8a93b8",
                "square light lastmove": "#c9c2ff", "square dark lastmove": "#8f84e8",
                "margin": "#161922", "coord": "#8a90a6"}


# ------------------------------------------------------------------ helpers
class _Signals(QObject):
    done = Signal(object)
    fail = Signal(str)
    progress = Signal(str)


class Task(QRunnable):
    """Run fn in a background thread so the window never freezes."""
    def __init__(self, fn, *args):
        super().__init__()
        self.fn, self.args, self.sig = fn, args, _Signals()

    def run(self):
        try:
            result = self.fn(*self.args)
        except Exception as e:                      # shown in the status bar
            self.sig.fail.emit(str(e) or type(e).__name__)
            return
        self.sig.done.emit(result)


def card(title: str | None = None) -> tuple[QFrame, QVBoxLayout]:
    f = QFrame(objectName="Card")
    lay = QVBoxLayout(f)
    lay.setContentsMargins(18, 16, 18, 18)
    lay.setSpacing(10)
    if title:
        lay.addWidget(QLabel(title, objectName="CardTitle"))
    return f, lay


def muted(text: str) -> QLabel:
    l = QLabel(text, objectName="Muted")
    l.setWordWrap(True)
    return l


def dot(color: str, text: str) -> str:
    return f"<span style='color:{color}; font-size:13pt'>●</span>&nbsp; {text}"


def virtual_screen_px():
    """(left, top, width, height) of all monitors in real pixels."""
    import mss
    with getattr(mss, "MSS", mss.mss)() as s:
        m = s.monitors[0]
        return m["left"], m["top"], m["width"], m["height"]


def to_logical(px: float, py: float) -> QPointF:
    """Real screen pixels -> Qt window coordinates (handles display scaling)."""
    left, top, w, h = virtual_screen_px()
    vg = QGuiApplication.primaryScreen().virtualGeometry()
    return QPointF(vg.x() + (px - left) * vg.width() / w,
                   vg.y() + (py - top) * vg.height() / h)


def to_physical(r: QRect):
    """Qt window rectangle -> (x, y, w, h) in real screen pixels."""
    left, top, w, h = virtual_screen_px()
    vg = QGuiApplication.primaryScreen().virtualGeometry()
    kx, ky = w / vg.width(), h / vg.height()
    return (int(left + (r.x() - vg.x()) * kx), int(top + (r.y() - vg.y()) * ky),
            int(r.width() * kx), int(r.height() * ky))


# ------------------------------------------------------------ screen overlay
class Overlay(QWidget):
    """Invisible, click-through layer over the screen for arrows/highlights."""
    def __init__(self):
        super().__init__(None, Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint |
                         Qt.Tool | Qt.WindowTransparentForInput)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.items = []
        self.timer = QTimer(self, singleShot=True, timeout=self.clear)

    def show_items(self, items, ms=None):
        self.items = items
        self.setGeometry(QGuiApplication.primaryScreen().virtualGeometry())
        self.show()
        self.raise_()
        self.update()
        self.timer.stop()
        if ms:
            self.timer.start(ms)

    def clear(self):
        self.items = []
        self.hide()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        off = self.geometry().topLeft()
        for kind, *a in self.items:
            if kind == "rect":
                x, y, w, h, color = a
                tl = to_logical(x, y) - QPointF(off)
                br = to_logical(x + w, y + h) - QPointF(off)
                p.setPen(QPen(QColor(color), 4))
                p.setBrush(Qt.NoBrush)
                p.drawRoundedRect(QRectF(tl, br), 6, 6)
            elif kind == "dot":
                x, y, color = a
                c = to_logical(x, y) - QPointF(off)
                p.setPen(QPen(QColor(color), 4))
                p.drawEllipse(c, 18, 18)
            elif kind == "arrow":
                x1, y1, x2, y2, color, width = a
                self._arrow(p, to_logical(x1, y1) - QPointF(off),
                            to_logical(x2, y2) - QPointF(off), color, width)

    @staticmethod
    def _arrow(p, a: QPointF, b: QPointF, color: str, width: float):
        col = QColor(color)
        col.setAlpha(210)
        d = b - a
        length = max((d.x() ** 2 + d.y() ** 2) ** 0.5, 1)
        u = QPointF(d.x() / length, d.y() / length)
        n = QPointF(-u.y(), u.x())
        head = width * 2.4
        base = b - u * head
        p.setPen(QPen(col, width, Qt.SolidLine, Qt.RoundCap))
        p.drawLine(a, base)
        p.setPen(Qt.NoPen)
        p.setBrush(col)
        p.drawPolygon(QPolygonF([b, base + n * head * 0.75, base - n * head * 0.75]))
        p.drawEllipse(a, width * 0.9, width * 0.9)


class RegionPicker(QWidget):
    """Full-screen frozen screenshot; drag a square over the chess board."""
    def __init__(self, shot, on_pick):
        super().__init__(None, Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
        self.on_pick = on_pick
        self.offset = shot.info["offset"]
        self.real_size = shot.size
        data = shot.tobytes("raw", "RGB")
        self.pix = QPixmap.fromImage(QImage(data, shot.width, shot.height,
                                            shot.width * 3, QImage.Format_RGB888).copy())
        self.start = self.end = None
        self.setCursor(Qt.CrossCursor)
        self.setGeometry(QGuiApplication.primaryScreen().virtualGeometry())
        self.show()
        self.activateWindow()

    def _square(self) -> QRect:
        s, e = self.start, self.end
        size = max(abs(e.x() - s.x()), abs(e.y() - s.y()))
        x = s.x() if e.x() >= s.x() else s.x() - size
        y = s.y() if e.y() >= s.y() else s.y() - size
        return QRect(x, y, size, size)

    def paintEvent(self, _):
        p = QPainter(self)
        p.drawPixmap(self.rect(), self.pix)
        p.fillRect(self.rect(), QColor(0, 0, 0, 120))
        p.setPen(QColor("white"))
        p.setFont(QFont("Segoe UI", 16, QFont.Bold))
        p.drawText(self.rect().adjusted(0, 40, 0, 0), Qt.AlignHCenter | Qt.AlignTop,
                   "Drag a square around the chess board  ·  Esc to cancel")
        if self.start and self.end:
            r = self._square()
            sx = self.pix.width() / self.width()
            sy = self.pix.height() / self.height()
            p.drawPixmap(r, self.pix, QRectF(r.x() * sx, r.y() * sy,
                                             r.width() * sx, r.height() * sy))
            p.setPen(QPen(QColor(ACCENT), 3))
            p.drawRect(r)
            p.setPen(QPen(QColor(255, 255, 255, 90), 1))
            for i in range(1, 8):
                f = r.width() * i / 8
                p.drawLine(int(r.x() + f), r.y(), int(r.x() + f), r.bottom())
                p.drawLine(r.x(), int(r.y() + f), r.right(), int(r.y() + f))

    def mousePressEvent(self, e):
        self.start = self.end = e.position().toPoint()

    def mouseMoveEvent(self, e):
        self.end = e.position().toPoint()
        self.update()

    def mouseReleaseEvent(self, e):
        self.end = e.position().toPoint()
        r = self._square()
        self.close()
        if r.width() > 40:
            k = self.real_size[0] / self.width()
            self.on_pick(cv.BoardRect(int(self.offset[0] + r.x() * k),
                                      int(self.offset[1] + r.y() * k),
                                      int(r.width() * k)))

    def keyPressEvent(self, e):
        if e.key() == Qt.Key_Escape:
            self.close()


# --------------------------------------------------------------- main window
class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Screen Agent")
        self.resize(1240, 860)
        self.setMinimumSize(1080, 760)
        self.pool = QThreadPool.globalInstance()
        self.tasks = set()
        self.engine = None
        self.overlay = Overlay()
        self.settings = self._load_settings()
        self.board_mode = self.settings.get("board_mode", "auto")
        r = self.settings.get("manual_rect")
        self.manual_rect = cv.BoardRect(*r) if r else None
        self.last = None              # (reading, rect, lines)
        self.picked_image = None
        self.words = []

        root = QWidget(objectName="Root")
        self.setCentralWidget(root)
        outer = QHBoxLayout(root)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        # sidebar
        side = QFrame(objectName="Sidebar")
        side.setFixedWidth(220)
        sl = QVBoxLayout(side)
        sl.setContentsMargins(14, 22, 14, 16)
        sl.setSpacing(4)
        sl.addWidget(QLabel("◆  Screen Agent", objectName="Brand"))
        sl.addWidget(QLabel("see the screen · act on it", objectName="BrandSub"))
        self.stack = QStackedWidget()
        self.nav = []
        for i, (icon, name, page) in enumerate([
                ("♞", "Chess", self._chess_page()),
                ("◎", "Screen tools", self._screen_page()),
                ("›_", "Console", self._console_page())]):
            b = QPushButton(f"{icon}   {name}", objectName="Nav", checkable=True)
            b.clicked.connect(lambda _=False, i=i: self._go(i))
            sl.addWidget(b)
            self.nav.append(b)
            self.stack.addWidget(page)
        sl.addStretch()
        sl.addWidget(muted("Emergency stop for clicks/typing: move the mouse "
                           "to the top-left corner of the screen."))
        outer.addWidget(side)

        right = QVBoxLayout()
        right.setContentsMargins(0, 0, 0, 0)
        right.setSpacing(0)
        right.addWidget(self.stack, 1)
        bar = QFrame(objectName="StatusBar")
        bl = QHBoxLayout(bar)
        bl.setContentsMargins(18, 8, 18, 8)
        self.status = QLabel()
        self.mouse_lbl = muted("")
        bl.addWidget(self.status, 1)
        bl.addWidget(self.mouse_lbl)
        right.addWidget(bar)
        outer.addLayout(right, 1)

        self._go(0)
        self.say("Ready.", GOOD)
        QShortcut(QKeySequence("F8"), self, self.analyze)
        self.mouse_timer = QTimer(self, interval=250, timeout=self._show_mouse)
        self.mouse_timer.start()
        self._refresh_chess_status()

    # ---------------------------------------------------------- plumbing
    def _go(self, i):
        self.stack.setCurrentIndex(i)
        for j, b in enumerate(self.nav):
            b.setChecked(i == j)

    def say(self, text, color=ACCENT):
        self.status.setText(dot(color, text))

    def run(self, fn, *args, done=None, busy="Working…", buttons=()):
        for b in buttons:
            b.setEnabled(False)
        self.say(busy, WARN)
        t = Task(fn, *args)
        self.tasks.add(t)

        def finish():
            self.tasks.discard(t)
            for b in buttons:
                b.setEnabled(True)

        def ok(r):
            finish()
            if done:
                done(r)

        def fail(msg):
            finish()
            self.say(msg, BAD)
        t.sig.done.connect(ok)
        t.sig.fail.connect(fail)
        t.sig.progress.connect(lambda m: self.say(m, WARN))
        self.pool.start(t)
        return t

    def grab(self):
        """Screenshot without this window or the overlay in it."""
        hide = self.hide_chk.isChecked()
        self.overlay.clear()
        if hide:
            self.setWindowOpacity(0.0)
        QApplication.processEvents()
        time.sleep(0.18 if hide else 0.05)
        try:
            shot = sa.capture()
        finally:
            self.setWindowOpacity(1.0)
        if not hide:
            # Our window is in the screenshot: blank it out so its own board
            # preview is never mistaken for the real board.
            x, y, w, h = to_physical(self.frameGeometry())
            ox, oy = shot.info["offset"]
            from PIL import ImageDraw
            ImageDraw.Draw(shot).rectangle([x - ox, y - oy, x - ox + w, y - oy + h],
                                           fill=(0, 0, 0))
        return shot

    def act(self, fn, *args, done=None, busy="Working…"):
        """Run something that clicks/types: get this window out of the way."""
        self.showMinimized()

        def back(r):
            self.showNormal()
            self.activateWindow()
            if done:
                done(r)
        QTimer.singleShot(350, lambda: self.run(fn, *args, done=back, busy=busy))

    def _show_mouse(self):
        try:
            x, y = sa.pyautogui.position()
            self.mouse_lbl.setText(f"mouse  {x}, {y}")
        except Exception:
            pass

    def _load_settings(self):
        try:
            with open(SETTINGS_FILE) as f:
                return json.load(f)
        except Exception:
            return {}

    def _save_settings(self):
        s = {"board_mode": self.board_mode,
             "manual_rect": [self.manual_rect.x, self.manual_rect.y,
                             self.manual_rect.size] if self.manual_rect else None,
             "orientation": self.orient.currentIndex(),
             "to_move": self.tomove.currentIndex(),
             "think": self.think.value(), "lines": self.nlines.value(),
             "arrow": self.arrow_chk.isChecked(), "hide": self.hide_chk.isChecked()}
        try:
            with open(SETTINGS_FILE, "w") as f:
                json.dump(s, f)
        except Exception:
            pass

    def closeEvent(self, e):
        self._save_settings()
        self.overlay.close()
        if self.engine:
            self.engine.close()
        super().closeEvent(e)

    # ------------------------------------------------------------ chess page
    def _chess_page(self):
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.setContentsMargins(30, 26, 30, 20)
        lay.setSpacing(16)
        lay.addWidget(QLabel("Chess analysis", objectName="Title"))
        lay.addWidget(QLabel("Finds a chess board anywhere on your screen, reads "
                             "the position and asks Stockfish for the best move.",
                             objectName="Subtitle"))
        row = QHBoxLayout()
        row.setSpacing(16)
        lay.addLayout(row, 1)

        # left: board picture
        bc, bl = card()
        self.board_svg = QSvgWidget()
        self.board_svg.setMinimumSize(360, 360)
        bl.addWidget(self.board_svg, 1, Qt.AlignCenter)
        fen_row = QHBoxLayout()
        self.fen = QLineEdit(readOnly=True, placeholderText="Position (FEN) appears here")
        copy = QPushButton("Copy")
        copy.clicked.connect(lambda: (QGuiApplication.clipboard().setText(self.fen.text()),
                                      self.say("FEN copied.", GOOD)))
        lich = QPushButton("Open in Lichess")
        lich.clicked.connect(lambda: self.fen.text() and webbrowser.open(
            "https://lichess.org/analysis/" + self.fen.text().replace(" ", "_")))
        fen_row.addWidget(self.fen, 1)
        fen_row.addWidget(copy)
        fen_row.addWidget(lich)
        bl.addLayout(fen_row)
        row.addWidget(bc, 5)
        bc.resizeEvent = lambda e: self._fit_board(bc)

        # right column (scrolls instead of squashing on small screens)
        holder = QWidget()
        col = QVBoxLayout(holder)
        col.setContentsMargins(0, 0, 6, 0)
        col.setSpacing(16)
        scroll = QScrollArea(widgetResizable=True, frameShape=QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setStyleSheet("QScrollArea, QScrollArea > QWidget > QWidget { background: transparent; }")
        scroll.setWidget(holder)
        scroll.setMinimumWidth(400)
        row.addWidget(scroll, 4)

        c1, l1 = card()
        self.chip_board = QLabel()
        self.chip_pieces = QLabel()
        self.chip_engine = QLabel()
        for w in (self.chip_board, self.chip_pieces, self.chip_engine):
            l1.addWidget(w)
        self.analyze_btn = QPushButton("Find best move   (F8)", objectName="Primary")
        self.analyze_btn.clicked.connect(self.analyze)
        l1.addWidget(self.analyze_btn)
        g = QGridLayout()
        g.setSpacing(8)
        self.find_btn = QPushButton("Find board")
        self.find_btn.clicked.connect(self.find_board)
        self.pick_btn = QPushButton("Select board area…")
        self.pick_btn.clicked.connect(self.pick_board)
        self.calib_btn = QPushButton("Learn pieces")
        self.calib_btn.clicked.connect(self.calibrate)
        self.engine_btn = QPushButton("Get Stockfish")
        self.engine_btn.clicked.connect(self.get_engine)
        g.addWidget(self.find_btn, 0, 0)
        g.addWidget(self.pick_btn, 0, 1)
        g.addWidget(self.calib_btn, 1, 0)
        g.addWidget(self.engine_btn, 1, 1)
        l1.addLayout(g)
        self.calib_btn.setToolTip("Show a board in the starting position, then "
                                  "press this. Redo it if you change piece style or site.")
        l1.addWidget(muted("First time: put a board in the starting position and "
                           "press Learn pieces."))
        c1.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Minimum)
        col.addWidget(c1)

        c2, l2 = card("Options")
        og = QGridLayout()
        og.setHorizontalSpacing(12)
        og.setVerticalSpacing(10)
        self.orient = QComboBox()
        self.orient.addItems(["Auto-detect", "White at bottom", "Black at bottom"])
        self.tomove = QComboBox()
        self.tomove.addItems(["Side at the bottom", "White", "Black"])
        self.think = QSlider(Qt.Horizontal, minimum=1, maximum=20, value=4)
        self.think_lbl = QLabel()
        self.think.valueChanged.connect(
            lambda v: self.think_lbl.setText(f"{v * 0.5:.1f} s"))
        self.nlines = QSpinBox(minimum=1, maximum=5, value=3)
        self.arrow_chk = QCheckBox("Draw the arrow on the real board")
        self.hide_chk = QCheckBox("Hide this window while scanning")
        og.addWidget(QLabel("Board"), 0, 0)
        og.addWidget(self.orient, 0, 1, 1, 2)
        og.addWidget(QLabel("To move"), 1, 0)
        og.addWidget(self.tomove, 1, 1, 1, 2)
        og.addWidget(QLabel("Think"), 2, 0)
        og.addWidget(self.think, 2, 1)
        og.addWidget(self.think_lbl, 2, 2)
        og.addWidget(QLabel("Lines"), 3, 0)
        og.addWidget(self.nlines, 3, 1, 1, 2)
        og.setColumnStretch(1, 1)
        l2.addLayout(og)
        l2.addWidget(self.arrow_chk)
        l2.addWidget(self.hide_chk)
        c2.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Minimum)
        col.addWidget(c2)

        c3, l3 = card("Best moves")
        self.moves = QListWidget()
        self.moves.setMinimumHeight(150)
        self.moves.currentRowChanged.connect(self._show_line)
        l3.addWidget(self.moves, 1)
        col.addWidget(c3, 1)

        s = self.settings
        self.orient.setCurrentIndex(s.get("orientation", 0))
        self.tomove.setCurrentIndex(s.get("to_move", 0))
        self.think.setValue(s.get("think", 4))
        self.think_lbl.setText(f"{self.think.value() * 0.5:.1f} s")
        self.nlines.setValue(s.get("lines", 3))
        self.arrow_chk.setChecked(s.get("arrow", True))
        self.hide_chk.setChecked(s.get("hide", True))
        lay.addWidget(muted("Use it for studying, puzzles, reviewing your games and "
                            "playing bots. Engine help in live games against people "
                            "is against chess.com / lichess rules and gets accounts banned."))
        self._draw_board(chess.Board())
        return page

    def _fit_board(self, c):
        side = max(min(c.width() - 40, c.height() - 90), 200)
        self.board_svg.setFixedSize(side, side)

    def _draw_board(self, board, white_bottom=True, lines=(), highlight=0):
        arrows = []
        for i, ln in enumerate(lines):
            col = ACCENT if i == highlight else "#3ddc97"
            arrows.append(chess.svg.Arrow(ln.move.from_square, ln.move.to_square, color=col))
        svg = chess.svg.board(board, size=600, arrows=arrows, colors=BOARD_COLORS,
                              orientation=chess.WHITE if white_bottom else chess.BLACK)
        self.board_svg.load(QByteArray(svg.encode()))

    def _refresh_chess_status(self):
        if self.board_mode == "manual" and self.manual_rect:
            self.chip_board.setText(dot(GOOD, "Board: using the area you selected"))
        else:
            self.chip_board.setText(dot(GOOD, "Board: found automatically each scan"))
        if cv.is_calibrated():
            self.chip_pieces.setText(dot(GOOD, "Pieces: learned"))
        else:
            self.chip_pieces.setText(dot(WARN, "Pieces: not learned yet — press Learn pieces"))
        path = cv.find_engine()
        if path:
            self.chip_engine.setText(dot(GOOD, "Engine: Stockfish ready"))
            self.engine_btn.setText("Stockfish ✓")
        else:
            self.chip_engine.setText(dot(WARN, "Engine: not found — press Get Stockfish"))

    def _board_rect(self, img_np, offset):
        if self.board_mode == "manual" and self.manual_rect:
            return self.manual_rect
        r = cv.detect_board(img_np, offset)
        if r is None:
            raise RuntimeError("Couldn't find a chess board. Make it fully visible, "
                               "or use 'Select board area…'.")
        return r

    def find_board(self):
        self.board_mode = "auto"
        shot = self.grab()
        img = np.array(shot)

        def done(r):
            if r is None:
                self.say("No board found. Make the whole board visible, or use "
                         "'Select board area…'.", BAD)
                return
            self.overlay.show_items([("rect", r.x, r.y, r.size, r.size, GOOD)], ms=1800)
            self.say(f"Board found ({r.size}px). It'll be found again each scan.", GOOD)
            self._refresh_chess_status()
            self._save_settings()
        self.run(cv.detect_board, img, shot.info["offset"], done=done,
                 busy="Looking for a board…", buttons=[self.find_btn])

    def pick_board(self):
        shot = self.grab()

        def picked(rect):
            self.manual_rect = rect
            self.board_mode = "manual"
            self.overlay.show_items([("rect", rect.x, rect.y, rect.size, rect.size,
                                      GOOD)], ms=1500)
            self.say("Board area saved. Press 'Find board' to go back to auto.", GOOD)
            self._refresh_chess_status()
            self._save_settings()
        self._picker = RegionPicker(shot, picked)

    def calibrate(self):
        shot = self.grab()
        img, off = np.array(shot), shot.info["offset"]

        def work():
            rect = self._board_rect(img, off)
            return cv.calibrate(img, rect, off), rect

        def done(res):
            msg, rect = res
            self.overlay.show_items([("rect", rect.x, rect.y, rect.size, rect.size,
                                      GOOD)], ms=1500)
            self.say(msg, GOOD)
            self._refresh_chess_status()
        self.run(work, done=done, busy="Learning your pieces…", buttons=[self.calib_btn])

    def get_engine(self):
        if cv.find_engine():
            self.say("Stockfish is already installed.", GOOD)
            return
        self._dl = _Signals()
        self._dl.progress.connect(lambda m: self.say(m, WARN))

        def work():
            return cv.download_engine(progress=self._dl.progress.emit)

        def done(path):
            self.say("Stockfish downloaded.", GOOD)
            self._refresh_chess_status()
        self.run(work, done=done, busy="Downloading Stockfish…",
                 buttons=[self.engine_btn])

    def analyze(self):
        if not self.analyze_btn.isEnabled():
            return
        shot = self.grab()
        img, off = np.array(shot), shot.info["offset"]
        orient = ["auto", "white", "black"][self.orient.currentIndex()]
        to_move = ["auto", "white", "black"][self.tomove.currentIndex()]
        seconds, nlines = self.think.value() * 0.5, self.nlines.value()

        def work():
            rect = self._board_rect(img, off)
            reading = cv.read_position(img, rect, off, orient, to_move)
            bad = cv.problems(reading.board)
            if bad:
                return reading, rect, [], bad
            if reading.board.is_game_over():
                return reading, rect, [], "Game over in this position: " + \
                    reading.board.result()
            if self.engine is None:
                path = cv.find_engine()
                if not path:
                    raise RuntimeError("Stockfish not found — press 'Get Stockfish'.")
                self.engine = cv.Engine(path)
            return reading, rect, self.engine.best_lines(reading.board, seconds, nlines), None

        self.run(work, done=self._analysis_done, busy="Reading board and thinking…",
                 buttons=[self.analyze_btn])

    def _analysis_done(self, res):
        reading, rect, lines, problem = res
        self.last = (reading, rect, lines)
        b = reading.board
        self.fen.setText(b.fen())
        self.moves.blockSignals(True)
        self.moves.clear()
        for i, ln in enumerate(lines):
            it = QListWidgetItem(f"{i + 1}.  {ln.san}      {ln.score}\n      {ln.pv}")
            f = it.font()
            f.setPointSizeF(11 if i == 0 else 10)
            f.setBold(i == 0)
            it.setFont(f)
            self.moves.addItem(it)
        self.moves.blockSignals(False)
        self._draw_board(b, reading.white_bottom, lines)
        if problem:
            self.say(problem, BAD)
            return
        side = "White" if b.turn == chess.WHITE else "Black"
        note = ("  " + " ".join(reading.notes)) if reading.notes else ""
        self.say(f"{side} to move — best: {lines[0].san} ({lines[0].score}){note}", GOOD)
        if lines:
            self.moves.setCurrentRow(0)

    def _show_line(self, i):
        if not self.last or i < 0:
            return
        reading, rect, lines = self.last
        if i >= len(lines):
            return
        self._draw_board(reading.board, reading.white_bottom, lines, highlight=i)
        if self.arrow_chk.isChecked():
            mv = lines[i].move
            sp = rect.size / 8

            def centre(sq):
                f, r = chess.square_file(sq), chess.square_rank(sq)
                c, row = (f, 7 - r) if reading.white_bottom else (7 - f, r)
                return rect.x + (c + 0.5) * sp, rect.y + (row + 0.5) * sp
            (x1, y1), (x2, y2) = centre(mv.from_square), centre(mv.to_square)
            self.overlay.show_items([("arrow", x1, y1, x2, y2, ACCENT, sp * 0.22)],
                                    ms=20000)

    # ----------------------------------------------------------- screen page
    def _screen_page(self):
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.setContentsMargins(30, 26, 30, 20)
        lay.setSpacing(16)
        lay.addWidget(QLabel("Screen tools", objectName="Title"))
        lay.addWidget(QLabel("Capture the screen, read its text, find things and "
                             "click them.", objectName="Subtitle"))
        row = QHBoxLayout()
        row.setSpacing(16)
        lay.addLayout(row, 1)

        pc, pl = card("Screenshot")
        self.preview = QLabel("Press Capture to take a screenshot.", alignment=Qt.AlignCenter)
        self.preview.setMinimumSize(420, 260)
        self.preview.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Ignored)
        pl.addWidget(self.preview, 1)
        br = QHBoxLayout()
        cap = QPushButton("Capture")
        cap.clicked.connect(self.capture_preview)
        save = QPushButton("Save as PNG…")
        save.clicked.connect(self.save_shot)
        read = QPushButton("Read all text")
        read.clicked.connect(self.read_all)
        for b in (cap, save, read):
            br.addWidget(b)
        pl.addLayout(br)
        row.addWidget(pc, 3)

        col = QVBoxLayout()
        col.setSpacing(16)
        row.addLayout(col, 2)

        tc, tl = card("Find text")
        self.text_in = QLineEdit(placeholderText="e.g.  Sign in")
        tl.addWidget(self.text_in)
        tr = QHBoxLayout()
        ft = QPushButton("Find")
        ft.clicked.connect(lambda: self.find_text(False))
        ct = QPushButton("Find && click")
        ct.clicked.connect(lambda: self.find_text(True))
        tr.addWidget(ft)
        tr.addWidget(ct)
        tl.addLayout(tr)
        col.addWidget(tc)

        ic, il = card("Find a picture")
        self.img_lbl = muted("Crop a button/icon from a screenshot and pick it here.")
        il.addWidget(self.img_lbl)
        ir = QHBoxLayout()
        choose = QPushButton("Choose image…")
        choose.clicked.connect(self.choose_image)
        fi = QPushButton("Find")
        fi.clicked.connect(lambda: self.find_img(False))
        ci = QPushButton("Find && click")
        ci.clicked.connect(lambda: self.find_img(True))
        for b in (choose, fi, ci):
            ir.addWidget(b)
        il.addLayout(ir)
        col.addWidget(ic)

        wc, wl = card("Text on screen")
        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(["Text", "X", "Y"])
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.table.verticalHeader().hide()
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.cellDoubleClicked.connect(self._click_word)
        wl.addWidget(self.table, 1)
        wl.addWidget(muted("Double-click a row to click that spot."))
        col.addWidget(wc, 1)
        self.shot = None
        return page

    def _set_preview(self, shot):
        self.shot = shot
        data = shot.tobytes("raw", "RGB")
        img = QImage(data, shot.width, shot.height, shot.width * 3,
                     QImage.Format_RGB888).copy()
        pix = QPixmap.fromImage(img).scaled(self.preview.size(), Qt.KeepAspectRatio,
                                            Qt.SmoothTransformation)
        self.preview.setPixmap(pix)

    def capture_preview(self):
        self._set_preview(self.grab())
        self.say(f"Captured {self.shot.width}×{self.shot.height}.", GOOD)

    def save_shot(self):
        if not self.shot:
            self.capture_preview()
        path, _ = QFileDialog.getSaveFileName(self, "Save screenshot", "screen.png",
                                              "PNG (*.png)")
        if path:
            self.shot.save(path)
            self.say(f"Saved {path}", GOOD)

    def read_all(self):
        shot = self.grab()
        self._set_preview(shot)

        def done(words):
            self.words = words
            self.table.setRowCount(len(words))
            for i, w in enumerate(words):
                for j, v in enumerate((w.text, w.x, w.y)):
                    self.table.setItem(i, j, QTableWidgetItem(str(v)))
            self.say(f"Read {len(words)} words.", GOOD)
        self.run(sa.read_text, None, 50.0, shot, done=done, busy="Reading text (OCR)…")

    def _click_word(self, row, _col):
        w = self.words[row]
        self.act(sa.click, w.x, w.y, busy=f"Clicking '{w.text}'…",
                 done=lambda _: self.say(f"Clicked '{w.text}' at {w.x}, {w.y}.", GOOD))

    def find_text(self, click):
        target = self.text_in.text().strip()
        if not target:
            return
        shot = self.grab()

        def done(w):
            if not w:
                self.say(f"'{target}' not found on screen.", BAD)
                return
            if click:
                self.act(sa.click, w.x, w.y, busy="Clicking…",
                         done=lambda _: self.say(f"Clicked '{target}' at {w.x}, {w.y}.", GOOD))
            else:
                self.overlay.show_items([("dot", w.x, w.y, GOOD)], ms=2000)
                self.say(f"Found '{target}' at {w.x}, {w.y}.", GOOD)
        self.run(sa.find_text, target, None, shot, done=done, busy="Searching…")

    def choose_image(self):
        path, _ = QFileDialog.getOpenFileName(self, "Choose image", "",
                                              "Images (*.png *.jpg *.bmp)")
        if path:
            self.picked_image = path
            self.img_lbl.setText(os.path.basename(path))

    def find_img(self, click):
        if not self.picked_image:
            self.choose_image()
            if not self.picked_image:
                return
        shot = self.grab()

        def done(r):
            if not r:
                self.say("Picture not found on screen.", BAD)
                return
            x, y, score = r
            if click:
                self.act(sa.click, x, y, busy="Clicking…",
                         done=lambda _: self.say(f"Clicked picture at {x}, {y}.", GOOD))
            else:
                self.overlay.show_items([("dot", x, y, GOOD)], ms=2000)
                self.say(f"Found at {x}, {y} (match {score:.0%}).", GOOD)
        self.run(sa.find_image, self.picked_image, 0.85, None, shot, done=done,
                 busy="Searching…")

    # ---------------------------------------------------------- console page
    def _console_page(self):
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.setContentsMargins(30, 26, 30, 20)
        lay.setSpacing(16)
        lay.addWidget(QLabel("Console", objectName="Title"))
        lay.addWidget(QLabel("Type commands or run a script file. Type 'help' for "
                             "the list.", objectName="Subtitle"))
        c, l = card()
        self.log = QPlainTextEdit(readOnly=True)
        l.addWidget(self.log, 1)
        r = QHBoxLayout()
        self.cmd = QLineEdit(placeholderText="e.g.  clicktext OK    ·    type hello    ·    press enter")
        self.cmd.returnPressed.connect(self.run_cmd)
        go = QPushButton("Run", objectName="Primary")
        go.clicked.connect(self.run_cmd)
        scr = QPushButton("Run script…")
        scr.clicked.connect(self.run_script_file)
        r.addWidget(self.cmd, 1)
        r.addWidget(go)
        r.addWidget(scr)
        l.addLayout(r)
        lay.addWidget(c, 1)
        self.log.appendPlainText(sa.HELP)
        return page

    @staticmethod
    def _captured(fn, *a):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            try:
                fn(*a)
            except Exception as e:
                print(f"error: {e}")
        return buf.getvalue()

    def run_cmd(self):
        import shlex
        line = self.cmd.text().strip()
        if not line:
            return
        self.cmd.clear()
        self.log.appendPlainText(f"\n> {line}")
        if line == "help":
            self.log.appendPlainText(sa.HELP)
            return
        try:
            argv = shlex.split(line)
        except ValueError as e:
            self.log.appendPlainText(f"error: {e}")
            return
        self.run(self._captured, sa.run_command, argv,
                 done=lambda out: (self.log.appendPlainText(out.rstrip()),
                                   self.say("Done.", GOOD)), busy=f"Running: {line}")

    def run_script_file(self):
        path, _ = QFileDialog.getOpenFileName(self, "Run script", HERE,
                                              "Scripts (*.txt);;All files (*)")
        if not path:
            return
        self.log.appendPlainText(f"\n> script {path}")
        self.act(self._captured, sa.run_script, path,
                 done=lambda out: (self.log.appendPlainText(out.rstrip()),
                                   self.say("Script finished.", GOOD)),
                 busy="Running script…")


def dark_palette(app):
    app.setStyle("Fusion")
    p = QPalette()
    for role, c in [(QPalette.Window, "#0e1016"), (QPalette.WindowText, "#e7e9f0"),
                    (QPalette.Base, "#10131a"), (QPalette.AlternateBase, "#161922"),
                    (QPalette.Text, "#e7e9f0"), (QPalette.Button, "#212534"),
                    (QPalette.ButtonText, "#e7e9f0"), (QPalette.Highlight, ACCENT),
                    (QPalette.HighlightedText, "#ffffff"), (QPalette.ToolTipBase, "#161922"),
                    (QPalette.ToolTipText, "#e7e9f0"), (QPalette.PlaceholderText, "#5f6680")]:
        p.setColor(role, QColor(c))
    app.setPalette(p)
    app.setStyleSheet(STYLE)


def main():
    app = QApplication(sys.argv)
    app.setApplicationName("Screen Agent")
    dark_palette(app)
    w = MainWindow()
    w.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
