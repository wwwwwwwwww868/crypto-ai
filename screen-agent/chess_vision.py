"""
chess_vision.py - find a chess board on screen, read the position, ask Stockfish.

How piece reading works
  Every site and theme draws pieces differently, so instead of shipping
  pictures of pieces we *learn* yours: put a board in the starting position
  on screen and press Calibrate. Each square is turned into a small
  "silhouette" (the piece with its square colour removed), and those are
  remembered per piece type. Later, each square is matched to the closest
  remembered silhouette.
"""
from __future__ import annotations

import glob
import io
import os
import platform
import shutil
import subprocess
import urllib.error
import urllib.request
import zipfile
from dataclasses import dataclass

import chess
import chess.engine
import cv2
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
CALIBRATION_FILE = os.path.join(HERE, "chess_calibration.npz")
ENGINE_DIR = os.path.join(HERE, "engine")

DESC = 40            # descriptor size (DESC x DESC pixels)
MARGIN = 0.07        # trim this fraction off each square edge
EMPTY_FRACTION = 0.03


@dataclass
class BoardRect:
    x: int          # screen coordinates of the top-left corner
    y: int
    size: int       # width == height

    def square_px(self) -> float:
        return self.size / 8


# ------------------------------------------------------------ board finding
def detect_board(img: np.ndarray, offset=(0, 0)) -> BoardRect | None:
    """Find an 8x8 board in an RGB screenshot. Returns its screen rectangle.

    Looks for many equally-sized square regions laid out on a grid - the
    empty squares of the board - so it works with any colours or theme.
    """
    gray = cv2.cvtColor(img, cv2.COLOR_RGB2GRAY)
    edges = cv2.Canny(gray, 15, 45)
    edges = cv2.dilate(edges, np.ones((3, 3), np.uint8))
    contours, _ = cv2.findContours(255 - edges, cv2.RETR_LIST,
                                   cv2.CHAIN_APPROX_SIMPLE)
    boxes = []
    for c in contours:
        x, y, w, h = cv2.boundingRect(c)
        if 14 <= w <= 300 and abs(w - h) <= 0.12 * w:
            if cv2.contourArea(c) >= 0.8 * w * h:
                boxes.append((x, y, w, h))
    if len(boxes) < 12:
        return None

    # biggest group of same-sized squares
    sizes = np.array([b[2] for b in boxes], float)
    counts = [(np.abs(sizes - s) <= 0.1 * s).sum() for s in sizes]
    ref = sizes[int(np.argmax(counts))]
    group = [b for b in boxes if abs(b[2] - ref) <= 0.1 * ref]
    if len(group) < 12:
        return None

    cx = np.array([b[0] + b[2] / 2 for b in group])
    cy = np.array([b[1] + b[3] / 2 for b in group])
    pitch = _grid_pitch(cx, ref) or _grid_pitch(cy, ref)
    if not pitch:
        return None

    # keep only squares on one common grid, near each other (one board)
    best = None
    for i in range(len(group)):
        ox, oy = cx[i], cy[i]
        col = np.round((cx - ox) / pitch)
        row = np.round((cy - oy) / pitch)
        on_grid = ((np.abs(cx - ox - col * pitch) < 0.2 * pitch) &
                   (np.abs(cy - oy - row * pitch) < 0.2 * pitch) &
                   (np.abs(col) <= 7) & (np.abs(row) <= 7))
        n = int(on_grid.sum())
        if best is None or n > best[0]:
            best = (n, on_grid, col, row, ox, oy)
    n, on_grid, col, row, ox, oy = best
    cols, rows = col[on_grid], row[on_grid]
    if cols.max() - cols.min() != 7 or rows.max() - rows.min() != 7:
        return None                       # couldn't see all 8 files/ranks

    # refine pitch with the extreme squares, then derive the outer corner
    left_c = cx[on_grid][cols == cols.min()].mean()
    right_c = cx[on_grid][cols == cols.max()].mean()
    top_c = cy[on_grid][rows == rows.min()].mean()
    bot_c = cy[on_grid][rows == rows.max()].mean()
    pitch = ((right_c - left_c) + (bot_c - top_c)) / 14
    x0 = left_c - pitch / 2
    y0 = top_c - pitch / 2
    return BoardRect(int(round(x0 + offset[0])), int(round(y0 + offset[1])),
                     int(round(pitch * 8)))


def _grid_pitch(centres: np.ndarray, ref: float) -> float | None:
    """Typical spacing between neighbouring square centres."""
    v = np.sort(centres)
    d = np.diff(v)
    d = d[(d > 0.8 * ref) & (d < 1.4 * ref)]
    return float(np.median(d)) if len(d) else None


# --------------------------------------------------------- square features
def _squares(img: np.ndarray, rect: BoardRect, offset=(0, 0)):
    """Yield (row, col, rgb crop) for the 64 squares, row 0 = top of screen."""
    sp = rect.square_px()
    bx, by = rect.x - offset[0], rect.y - offset[1]
    m = sp * MARGIN
    for r in range(8):
        for c in range(8):
            x1, y1 = int(bx + c * sp + m), int(by + r * sp + m)
            x2, y2 = int(bx + (c + 1) * sp - m), int(by + (r + 1) * sp - m)
            yield r, c, img[max(y1, 0):y2, max(x1, 0):x2]


def describe(square: np.ndarray) -> tuple[np.ndarray, float]:
    """Piece silhouette with the square's own colour neutralised.

    Returns (descriptor, fraction of the square that is 'piece')."""
    sq = cv2.resize(square, (DESC, DESC), interpolation=cv2.INTER_AREA)
    sq = sq.astype(np.float32)
    # square colour = median of the 4 corners (pieces rarely reach corners;
    # a coordinate label in one corner is outvoted by the other three)
    k = 5
    corners = np.concatenate([sq[:k, :k].reshape(-1, 3), sq[:k, -k:].reshape(-1, 3),
                              sq[-k:, :k].reshape(-1, 3), sq[-k:, -k:].reshape(-1, 3)])
    bg = np.median(corners, axis=0)
    mask = np.linalg.norm(sq - bg, axis=2) > 38
    gray = sq.mean(axis=2) / 255.0
    desc = np.where(mask, gray, 0.5).astype(np.float32)
    desc = cv2.GaussianBlur(desc, (0, 0), 1.2)   # tolerate tiny misalignment
    return desc.ravel(), float(mask.mean())


def _shifted(desc: np.ndarray, r=2) -> np.ndarray:
    """The descriptor nudged up to r pixels each way (crops are never exact)."""
    img = desc.reshape(DESC, DESC)
    pad = np.pad(img, r, constant_values=0.5)
    return np.stack([pad[r + dy:r + dy + DESC, r + dx:r + dx + DESC].ravel()
                     for dy in range(-r, r + 1) for dx in range(-r, r + 1)])


# ------------------------------------------------------------- calibration
START_ROWS = ["rnbqkbnr", "pppppppp", "........", "........",
              "........", "........", "PPPPPPPP", "RNBQKBNR"]


def calibrate(img: np.ndarray, rect: BoardRect, offset=(0, 0)) -> str:
    """Learn the piece set from a board showing the starting position."""
    feats = {(r, c): describe(sq) for r, c, sq in _squares(img, rect, offset)}

    # which side is at the bottom? White pieces are brighter.
    def brightness(rows):
        vals = [feats[(r, c)][0] for r in rows for c in range(8)]
        v = np.concatenate(vals)
        return v[v != 0.5].mean()
    white_bottom = brightness([6, 7]) > brightness([0, 1])

    X, y = [], []
    for (r, c), (d, frac) in feats.items():
        if white_bottom:
            sym = START_ROWS[r][c]
        else:                         # board flipped: mirror both ways
            sym = START_ROWS[7 - r][7 - c]
        if sym != "." and frac < EMPTY_FRACTION:
            raise ValueError("That doesn't look like the starting position "
                             f"(square row {r + 1}, col {c + 1} looks empty).")
        X.append(d)
        y.append(sym)
    np.savez(CALIBRATION_FILE, X=np.array(X), y=np.array(y))
    side = "White" if white_bottom else "Black"
    return f"Learned your piece set ({side} at the bottom)."


def is_calibrated() -> bool:
    return os.path.exists(CALIBRATION_FILE)


def _load_calibration():
    data = np.load(CALIBRATION_FILE)
    return data["X"], data["y"]


# ----------------------------------------------------------- reading boards
@dataclass
class Reading:
    board: chess.Board
    white_bottom: bool
    grid: list[list[str]]      # what's on each screen square, row 0 = top
    notes: list[str]


def read_position(img: np.ndarray, rect: BoardRect, offset=(0, 0),
                  orientation="auto", to_move="auto") -> Reading:
    """Turn the board on screen into a python-chess Board.

    orientation: 'auto' | 'white' (white at bottom) | 'black'
    to_move:     'auto' (the side at the bottom) | 'white' | 'black'
    """
    if not is_calibrated():
        raise RuntimeError("Calibrate first (starting position on screen).")
    X, labels = _load_calibration()
    keep = labels != "."                  # emptiness is decided by EMPTY_FRACTION
    X, labels = X[keep], labels[keep]
    grid = [["."] * 8 for _ in range(8)]
    for r, c, sq in _squares(img, rect, offset):
        d, frac = describe(sq)
        if frac < EMPTY_FRACTION:
            continue
        dist = np.abs(X[None] - _shifted(d)[:, None]).mean(axis=2).min(axis=0)
        grid[r][c] = str(labels[int(np.argmin(dist))])

    notes = []
    if orientation == "auto":
        white_bottom = _guess_white_bottom(grid)
    else:
        white_bottom = orientation == "white"

    board = chess.Board(None)
    for r in range(8):
        for c in range(8):
            if grid[r][c] != ".":
                sq = (chess.square(c, 7 - r) if white_bottom
                      else chess.square(7 - c, r))
                board.set_piece_at(sq, chess.Piece.from_symbol(grid[r][c]))

    if to_move == "auto":
        board.turn = chess.WHITE if white_bottom else chess.BLACK
    else:
        board.turn = chess.WHITE if to_move == "white" else chess.BLACK

    board.castling_rights = _castling_from_pieces(board)
    board.clean_castling_rights()

    # The side that just moved can't be in check: if it is, flip the turn.
    if board.was_into_check():
        board.turn = not board.turn
        notes.append("Switched side to move (the other king was in check).")
    return Reading(board, white_bottom, grid, notes)


def _guess_white_bottom(grid) -> bool:
    for r in range(8):
        for c in range(8):
            if grid[r][c] == "K":
                return r >= 4
            if grid[r][c] == "k":
                return r < 4
    return True


def _castling_from_pieces(b: chess.Board) -> int:
    rights = 0
    P = chess.Piece
    if b.piece_at(chess.E1) == P(chess.KING, chess.WHITE):
        if b.piece_at(chess.H1) == P(chess.ROOK, chess.WHITE): rights |= chess.BB_H1
        if b.piece_at(chess.A1) == P(chess.ROOK, chess.WHITE): rights |= chess.BB_A1
    if b.piece_at(chess.E8) == P(chess.KING, chess.BLACK):
        if b.piece_at(chess.H8) == P(chess.ROOK, chess.BLACK): rights |= chess.BB_H8
        if b.piece_at(chess.A8) == P(chess.ROOK, chess.BLACK): rights |= chess.BB_A8
    return rights


def problems(board: chess.Board) -> str | None:
    """Explain why a position can't be analysed, or None if it's fine."""
    st = board.status()
    if st == chess.STATUS_VALID:
        return None
    msgs = []
    if st & chess.STATUS_NO_WHITE_KING: msgs.append("no white king found")
    if st & chess.STATUS_NO_BLACK_KING: msgs.append("no black king found")
    if st & chess.STATUS_TOO_MANY_KINGS: msgs.append("too many kings")
    if st & chess.STATUS_PAWNS_ON_BACKRANK: msgs.append("pawn on first/last rank")
    if st & chess.STATUS_TOO_MANY_WHITE_PIECES: msgs.append("too many white pieces")
    if st & chess.STATUS_TOO_MANY_BLACK_PIECES: msgs.append("too many black pieces")
    if st & chess.STATUS_OPPOSITE_CHECK: msgs.append("both kings in check")
    if not msgs:
        return None        # minor issues (e.g. en passant) don't stop analysis
    return ("Couldn't read the board correctly: " + ", ".join(msgs) +
            ". Try Calibrate again with the starting position.")


# ------------------------------------------------------------------ engine
SETTINGS_FILE = os.path.join(HERE, "settings.json")
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
_checked: dict[str, bool] = {}


def engine_works(path: str) -> bool:
    """Really run a short search: a build made for a newer CPU can still say
    hello ('uciok') and only crash once it starts thinking."""
    if path in _checked:
        return _checked[path]
    ok = False
    try:
        out = subprocess.run([path], input="uci\nisready\nposition startpos\n"
                             "go depth 10\n", capture_output=True, text=True,
                             timeout=30, creationflags=_NO_WINDOW)
        ok = "bestmove" in out.stdout
    except subprocess.TimeoutExpired as e:          # still thinking = fine
        ok = "bestmove" in (e.stdout or b"").decode(errors="ignore")
    except Exception:
        ok = False
    _checked[path] = ok
    return ok


def _saved_engine() -> str | None:
    try:
        import json
        with open(SETTINGS_FILE) as f:
            return json.load(f).get("engine_path")
    except Exception:
        return None


def candidate_engines() -> list[str]:
    found = []
    p = _saved_engine()
    if p and os.path.isfile(p):
        found.append(p)
    pats = [os.path.join(ENGINE_DIR, "**", "stockfish*.exe"),
            os.path.join(ENGINE_DIR, "**", "stockfish*")]
    for pat in pats:
        for f in sorted(glob.glob(pat, recursive=True)):
            if os.path.isfile(f) and (f.lower().endswith(".exe") or os.access(f, os.X_OK)):
                found.append(f)
    for name in ("stockfish", "/usr/games/stockfish"):
        w = shutil.which(name)
        if w:
            found.append(w)
    return list(dict.fromkeys(found))


def find_engine() -> str | None:
    """First Stockfish on this computer that actually runs."""
    for f in candidate_engines():
        if engine_works(f):
            return f
    return None


# Fastest builds first; the plain x86-64 build runs on any 64-bit PC.
WINDOWS_BUILDS = ["stockfish-windows-x86-64-avx2.zip",
                  "stockfish-windows-x86-64-sse41-popcnt.zip",
                  "stockfish-windows-x86-64.zip"]
RELEASES = ["latest/download", "download/sf_17.1", "download/sf_17", "download/sf_16.1"]


def _fetch(url: str) -> bytes:
    import ssl
    req = urllib.request.Request(url, headers={"User-Agent": "screen-agent"})
    try:
        ctx = ssl.create_default_context()
        try:
            import certifi                      # better certificates if present
            ctx.load_verify_locations(certifi.where())
        except Exception:
            pass
        with urllib.request.urlopen(req, timeout=180, context=ctx) as r:
            return r.read()
    except urllib.error.URLError as e:
        if "CERTIFICATE" in str(e).upper():
            # Windows sometimes lacks the right root certificate for Python;
            # PowerShell uses Windows' own certificate store instead.
            tmp = os.path.join(ENGINE_DIR, "_download.zip")
            subprocess.run(["powershell", "-NoProfile", "-Command",
                            f"Invoke-WebRequest -UseBasicParsing -Uri '{url}' "
                            f"-OutFile '{tmp}'"], check=True, timeout=300,
                           creationflags=_NO_WINDOW)
            with open(tmp, "rb") as f:
                data = f.read()
            os.remove(tmp)
            return data
        raise


def download_engine(progress=print) -> str:
    """Download a Stockfish that runs on this PC into ./engine."""
    if platform.system() != "Windows":
        raise RuntimeError("Auto-download is for Windows. Install stockfish "
                           "with your package manager (e.g. apt install stockfish).")
    os.makedirs(ENGINE_DIR, exist_ok=True)
    errors = []
    for name in WINDOWS_BUILDS:
        for rel in RELEASES:
            url = f"https://github.com/official-stockfish/Stockfish/releases/{rel}/{name}"
            target = os.path.join(ENGINE_DIR, name[:-4])
            try:
                progress(f"Downloading {name} ...")
                data = _fetch(url)
                shutil.rmtree(target, ignore_errors=True)
                zipfile.ZipFile(io.BytesIO(data)).extractall(target)
                exes = glob.glob(os.path.join(target, "**", "stockfish*.exe"),
                                 recursive=True)
                if not exes:
                    raise RuntimeError("no stockfish .exe inside the download")
                progress("Testing Stockfish on this PC ...")
                if engine_works(exes[0]):
                    progress("Stockfish ready.")
                    return exes[0]
                shutil.rmtree(target, ignore_errors=True)
                errors.append(f"{name}: downloaded but doesn't run on this CPU")
                break                       # same build from older release won't help
            except urllib.error.HTTPError as e:
                errors.append(f"{name} ({rel}): HTTP {e.code}")
                continue                    # not in this release, try the next
            except Exception as e:
                shutil.rmtree(target, ignore_errors=True)
                errors.append(f"{name}: {e}")
                break
    raise RuntimeError("Couldn't set up Stockfish automatically. Download it from "
                       "stockfishchess.org/download, unzip it, then press "
                       "'Choose engine file…' and pick the stockfish .exe.\n\n"
                       "Details:\n" + "\n".join(errors[-6:]))


@dataclass
class Line:
    move: chess.Move
    san: str
    score: str          # '+0.35', '-1.20', '#3', '#-2' (side to move's view)
    pv: str             # first few moves of the line, in SAN


class Engine:
    def __init__(self, path: str):
        kw = {}
        if platform.system() == "Windows":
            kw["creationflags"] = subprocess.CREATE_NO_WINDOW
        self.engine = chess.engine.SimpleEngine.popen_uci(path, **kw)

    def best_lines(self, board: chess.Board, seconds=1.5, lines=3) -> list[Line]:
        infos = self.engine.analyse(board, chess.engine.Limit(time=seconds),
                                    multipv=lines)
        if isinstance(infos, dict):
            infos = [infos]
        out = []
        for info in infos:
            pv = info.get("pv")
            if not pv:
                continue
            sc = info["score"].pov(board.turn)
            if sc.is_mate():
                score = f"#{sc.mate()}"
            else:
                score = f"{sc.score() / 100:+.2f}"
            out.append(Line(pv[0], board.san(pv[0]), score,
                            board.variation_san(pv[:6])))
        return out

    def close(self):
        try:
            self.engine.quit()
        except Exception:
            pass
