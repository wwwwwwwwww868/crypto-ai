#!/usr/bin/env python3
"""
screen_agent.py - scan your screen and interact with it.

What it can do
  - Capture the whole screen (or a region) to a PNG
  - Read text on screen with OCR and tell you where each word is
  - Find a small picture (button/icon) on screen
  - Click, double-click, move, scroll, type text and press keys
  - "Click the word X" / "click the image Y" in one step
  - Watch the screen and react when text or an image appears
  - Run a list of steps from a simple script file

SAFETY: slam the mouse into the TOP-LEFT corner of the screen to abort
instantly (pyautogui fail-safe). Every action also has a short pause.
"""
import argparse
import shlex
import sys
import time
from dataclasses import dataclass

import types

try:                        # pyautogui exits if tkinter is missing, but only
    import tkinter          # needs it for its unused mouseInfo() helper
except ImportError:
    _stub = types.ModuleType("mouseinfo")
    _stub.mouseInfo = lambda: None
    sys.modules["mouseinfo"] = _stub

import mss
import numpy as np
import pyautogui
from PIL import Image

pyautogui.FAILSAFE = True   # mouse to top-left corner = emergency stop
pyautogui.PAUSE = 0.15      # small delay after every action


# ---------------------------------------------------------------- scanning
def capture(region=None) -> Image.Image:
    """Screenshot. region = (left, top, width, height) or None for all screens."""
    with getattr(mss, "MSS", mss.mss)() as sct:
        mon = sct.monitors[0] if region is None else {
            "left": region[0], "top": region[1],
            "width": region[2], "height": region[3]}
        shot = sct.grab(mon)
        img = Image.frombytes("RGB", shot.size, shot.bgra, "raw", "BGRX")
        img.info["offset"] = (mon["left"], mon["top"])
        return img


@dataclass
class Word:
    text: str
    x: int      # centre, screen coordinates
    y: int
    conf: float


def read_text(region=None, min_conf=50.0) -> list[Word]:
    """OCR the screen. Returns every word with its centre position."""
    import os, shutil, pytesseract
    if not shutil.which("tesseract"):           # Windows: no PATH edit needed
        for p in (r"C:\Program Files\Tesseract-OCR\tesseract.exe",
                  os.path.expandvars(r"%LOCALAPPDATA%\Programs\Tesseract-OCR\tesseract.exe")):
            if os.path.exists(p):
                pytesseract.pytesseract.tesseract_cmd = p
                break
    img = capture(region)
    ox, oy = img.info["offset"]
    # Screens have small, scattered text: grayscale + 2x upscale + "sparse
    # text" mode (psm 11) reads far more of it than the defaults do.
    S = 2
    prepared = img.convert("L").resize((img.width * S, img.height * S),
                                       Image.LANCZOS)
    d = pytesseract.image_to_data(prepared, config="--psm 11",
                                  output_type=pytesseract.Output.DICT)
    words = []
    for i, t in enumerate(d["text"]):
        t = t.strip()
        conf = float(d["conf"][i])
        if t and conf >= min_conf:
            words.append(Word(t, ox + (d["left"][i] + d["width"][i] // 2) // S,
                              oy + (d["top"][i] + d["height"][i] // 2) // S, conf))
    words.sort(key=lambda w: (w.y // 10, w.x))   # reading order
    return words


def find_text(target: str, region=None) -> Word | None:
    """Find a word or phrase on screen (case-insensitive)."""
    parts = target.lower().split()
    words = read_text(region)
    low = [w.text.lower() for w in words]
    for i in range(len(words) - len(parts) + 1):
        if all(parts[j] in low[i + j] for j in range(len(parts))):
            first, last = words[i], words[i + len(parts) - 1]
            return Word(target, (first.x + last.x) // 2,
                        (first.y + last.y) // 2, first.conf)
    return None


def find_image(template_path: str, threshold=0.85, region=None):
    """Find a picture on screen. Returns (x, y, score) of its centre or None."""
    import cv2
    screen = cv2.cvtColor(np.array(capture(region)), cv2.COLOR_RGB2BGR)
    tpl = cv2.imread(template_path)
    if tpl is None:
        raise FileNotFoundError(template_path)
    res = cv2.matchTemplate(screen, tpl, cv2.TM_CCOEFF_NORMED)
    _, score, _, loc = cv2.minMaxLoc(res)
    if score < threshold:
        return None
    ox, oy = (region[0], region[1]) if region else (
        capture().info["offset"])
    h, w = tpl.shape[:2]
    return ox + loc[0] + w // 2, oy + loc[1] + h // 2, score


def wait_for(check, timeout=30.0, interval=0.5):
    """Re-scan until check() returns something truthy, or time out."""
    end = time.time() + timeout
    while time.time() < end:
        r = check()
        if r:
            return r
        time.sleep(interval)
    return None


# --------------------------------------------------------------- actions
def click(x, y, button="left", clicks=1):
    pyautogui.click(x, y, clicks=clicks, button=button)

def move(x, y):            pyautogui.moveTo(x, y, duration=0.2)
def scroll(amount):        pyautogui.scroll(amount)
def type_text(text):       pyautogui.write(text, interval=0.02)
def press(*keys):          pyautogui.hotkey(*keys) if len(keys) > 1 else pyautogui.press(keys[0])


# --------------------------------------------------------------- commands
def run_command(argv: list[str]) -> bool:
    """Run one command. Returns False if a find/wait failed."""
    cmd, *a = argv
    cmd = cmd.lower()
    if cmd == "screenshot":
        out = a[0] if a else "screen.png"
        capture().save(out); print(f"saved {out}")
    elif cmd == "read":
        for w in read_text():
            print(f"{w.x:5d},{w.y:<5d} {w.conf:4.0f}%  {w.text}")
    elif cmd == "find":
        w = find_text(" ".join(a))
        print(f"found at {w.x},{w.y}" if w else "not found"); return bool(w)
    elif cmd == "findimg":
        r = find_image(a[0])
        print(f"found at {r[0]},{r[1]} (score {r[2]:.2f})" if r else "not found")
        return bool(r)
    elif cmd == "click":
        click(int(a[0]), int(a[1]))
    elif cmd == "rightclick":
        click(int(a[0]), int(a[1]), button="right")
    elif cmd == "doubleclick":
        click(int(a[0]), int(a[1]), clicks=2)
    elif cmd == "clicktext":
        w = find_text(" ".join(a))
        if not w: print("text not found"); return False
        click(w.x, w.y); print(f"clicked '{w.text}' at {w.x},{w.y}")
    elif cmd == "clickimg":
        r = find_image(a[0])
        if not r: print("image not found"); return False
        click(r[0], r[1]); print(f"clicked image at {r[0]},{r[1]}")
    elif cmd == "waittext":
        w = wait_for(lambda: find_text(" ".join(a)))
        print(f"appeared at {w.x},{w.y}" if w else "timed out"); return bool(w)
    elif cmd == "waitimg":
        r = wait_for(lambda: find_image(a[0]))
        print(f"appeared at {r[0]},{r[1]}" if r else "timed out"); return bool(r)
    elif cmd == "move":
        move(int(a[0]), int(a[1]))
    elif cmd == "scroll":
        scroll(int(a[0]))
    elif cmd == "type":
        type_text(" ".join(a))
    elif cmd == "press":
        press(*a)            # e.g. press ctrl c   /   press enter
    elif cmd == "sleep":
        time.sleep(float(a[0]))
    elif cmd == "where":
        print("mouse at %d,%d   screen %dx%d" % (*pyautogui.position(), *pyautogui.size()))
    else:
        print(f"unknown command: {cmd}  (type 'help')"); return False
    return True


HELP = """commands:
  screenshot [file.png]     save the screen
  read                      OCR: list every word + position
  find <text>               where is this text?
  findimg <file.png>        where is this picture?
  click X Y | rightclick X Y | doubleclick X Y
  clicktext <text>          find text and click it
  clickimg <file.png>       find picture and click it
  waittext <text>           wait (30s) until text appears
  waitimg <file.png>        wait (30s) until picture appears
  move X Y | scroll N | type <text> | press <key> [key...]
  sleep SECONDS | where | help | quit
Emergency stop: move the mouse to the top-left corner."""


def repl():
    print("screen-agent ready. 'help' for commands.\n")
    while True:
        try:
            line = input("> ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if not line or line.startswith("#"):
            continue
        if line in ("quit", "exit"):
            break
        if line == "help":
            print(HELP); continue
        try:
            run_command(shlex.split(line))
        except pyautogui.FailSafeException:
            print("fail-safe triggered - stopped.")
        except Exception as e:
            print(f"error: {e}")


def run_script(path, stop_on_fail=True):
    with open(path) as f:
        for n, line in enumerate(f, 1):
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            print(f"[{n}] {line}")
            if not run_command(shlex.split(line)) and stop_on_fail:
                print(f"stopped at line {n}"); return 1
    return 0


def main():
    p = argparse.ArgumentParser(description="Scan the screen and interact with it.")
    p.add_argument("--script", help="run commands from a file, one per line")
    p.add_argument("--keep-going", action="store_true", help="don't stop when a step fails")
    p.add_argument("command", nargs="*", help="a single command, e.g. clicktext OK")
    args = p.parse_args()
    try:
        if args.script:
            sys.exit(run_script(args.script, not args.keep_going))
        if args.command:
            sys.exit(0 if run_command(args.command) else 1)
        repl()
    except pyautogui.FailSafeException:
        print("fail-safe triggered - stopped."); sys.exit(2)


if __name__ == "__main__":
    main()
