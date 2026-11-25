"""Draws the app icon (purple rounded square with a knight) -> icon.ico"""
from PIL import Image, ImageDraw, ImageFont

S = 256
img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
d = ImageDraw.Draw(img)
d.rounded_rectangle([8, 8, S - 8, S - 8], radius=56, fill=(124, 108, 255, 255))
font = None
for name in ("seguisym.ttf", "DejaVuSans.ttf", "arial.ttf"):
    try:
        font = ImageFont.truetype(name, 190)
        break
    except OSError:
        pass
d.text((S / 2, S / 2 + 6), "♞", font=font, fill="white", anchor="mm")
img.save("icon.ico", sizes=[(16, 16), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
