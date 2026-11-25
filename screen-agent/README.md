# screen-agent

Scans your screen (screenshots + OCR text reading + image matching) and
interacts with it (click, type, press keys, scroll). Separate from the
trading bot in the rest of this repo.

## Windows 11 quick setup

1. Install Python from https://www.python.org/downloads/ — tick **"Add python.exe to PATH"** in the installer.
2. Download this folder, then double-click **`setup.bat`** (installs the Python packages and Tesseract OCR).
3. Double-click **`start.bat`** to open the `>` prompt.

## Setup (other systems / manual)

1. Python 3.10+
2. Install Tesseract OCR (needed for reading text):
   - Windows: installer from https://github.com/UB-Mannheim/tesseract/wiki
     (the default install location is found automatically)
   - Mac: `brew install tesseract`
   - Linux: `sudo apt install tesseract-ocr`
3. `pip install -r requirements.txt`

Mac: allow your terminal under System Settings → Privacy & Security →
**Screen Recording** and **Accessibility**, or it can't see or click.

## Emergency stop

Slam the mouse into the **top-left corner** of the screen. It stops at once.

## Use it

Interactive mode:

    python screen_agent.py
    > read                 # list every word on screen and where it is
    > clicktext Sign in    # find the words "Sign in" and click them
    > type hello
    > press enter
    > press ctrl c

One-off command:

    python screen_agent.py clicktext OK

Script (one command per line, stops if a find fails):

    python screen_agent.py --script example_script.txt

## Commands

| command | what it does |
|---|---|
| `screenshot [file]` | save the screen to a PNG |
| `read` | OCR every word with its x,y position |
| `find <text>` / `findimg <png>` | locate text / a picture |
| `clicktext <text>` / `clickimg <png>` | find it and click it |
| `waittext <text>` / `waitimg <png>` | wait up to 30 s for it to appear |
| `click X Y`, `rightclick X Y`, `doubleclick X Y` | click a position |
| `move X Y`, `scroll N` | mouse movement / wheel |
| `type <text>`, `press <key> [key…]` | keyboard (`press ctrl shift t`) |
| `sleep S`, `where` | pause / show mouse position & screen size |

For `clickimg`, take a screenshot, crop just the button/icon, and save it as a
PNG. Image matching is usually more reliable than OCR for icons and buttons.

## Use it from your own Python code

```python
from screen_agent import find_text, find_image, click, type_text, wait_for

w = wait_for(lambda: find_text("Login"), timeout=20)
if w:
    click(w.x, w.y)
    type_text("myname")
```

## Limits

- OCR can misread small, stylised or low-contrast text, and text inside some
  input boxes. Use `read` to see what it actually sees; use `clickimg` when
  text fails.
- On high-DPI / scaled displays (e.g. Windows 150 %), positions can be off;
  set display scaling to 100 % if clicks miss.
