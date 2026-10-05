"""Draw the app icon (pixel-style briefcase on the Command Center green). Run once at install."""
from pathlib import Path

from PIL import Image, ImageDraw

STATIC = Path(__file__).parent / 'static'


def draw(size=64):
    img = Image.new('RGBA', (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    s = size / 64
    box = lambda x0, y0, x1, y1, fill: d.rectangle([x0 * s, y0 * s, x1 * s - 1, y1 * s - 1], fill=fill)
    d.rounded_rectangle([0, 0, size - 1, size - 1], radius=int(12 * s), fill=(31, 46, 42, 255))
    box(23, 14, 41, 18, (215, 235, 146, 255))   # handle
    box(23, 14, 27, 22, (215, 235, 146, 255))
    box(37, 14, 41, 22, (215, 235, 146, 255))
    box(10, 22, 54, 50, (215, 235, 146, 255))   # case
    box(10, 33, 54, 36, (53, 93, 70, 255))      # strap
    box(29, 31, 35, 39, (240, 210, 122, 255))   # clasp
    return img


def main():
    STATIC.mkdir(parents=True, exist_ok=True)
    big = draw(256)
    big.save(STATIC / 'icon.png')
    big.save(STATIC / 'icon.ico', sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
    return STATIC / 'icon.ico'


if __name__ == '__main__':
    print(main())
