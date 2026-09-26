#!/usr/bin/env python3
"""Render the Live Wire logo to PNG + a macOS .iconset (pure Pillow, no SVG deps).

Draws the same mark as assets/logo.svg: a dark rounded tile, a baseline 'wire',
and an impact-spike pulse stroked along a green->amber->red heat ramp. Supersamples
at 2x then downscales for clean edges. Writes only into assets/.
Needs Pillow (`pip install pillow`); the app itself does not.
"""
import math
import os

from PIL import Image, ImageDraw

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ASSETS = os.path.join(ROOT, "assets")

GREEN, AMBER, RED = (29, 158, 117), (239, 159, 39), (226, 75, 74)
BG, BORD, WIRE = (14, 17, 23), (30, 36, 46), (42, 48, 57)

PULSE = [(140, 600), (300, 600), (352, 600), (392, 644), (428, 236), (492, 796),
         (548, 600), (624, 600), (668, 540), (712, 644), (756, 600), (884, 600)]
X0, X1 = 140, 884


def lerp(a, b, t):
    return tuple(int(round(a[i] + (b[i] - a[i]) * t)) for i in range(3))


def grad(t):
    t = max(0.0, min(1.0, t))
    return lerp(GREEN, AMBER, t * 2) if t < 0.5 else lerp(AMBER, RED, (t - 0.5) * 2)


def render(px):
    s = px / 1024.0
    img = Image.new("RGBA", (px, px), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    def S(v):
        return v * s
    d.rounded_rectangle([S(32), S(32), S(992), S(992)], radius=S(208), fill=BG + (255,))
    d.rounded_rectangle([S(32), S(32), S(992), S(992)], radius=S(208), outline=BORD + (255,), width=max(1, int(S(4))))
    d.line([S(120), S(600), S(904), S(600)], fill=WIRE + (255,), width=max(1, int(S(8))))
    r = S(22)
    for (ax, ay), (bx, by) in zip(PULSE, PULSE[1:]):
        steps = max(1, int(math.hypot(bx - ax, by - ay) / 2))
        for i in range(steps + 1):
            u = i / steps
            x, y = ax + (bx - ax) * u, ay + (by - ay) * u
            c = grad((x - X0) / (X1 - X0))
            d.ellipse([S(x) - r, S(y) - r, S(x) + r, S(y) + r], fill=c + (255,))
    pr = S(30)
    cx, cy = S(428), S(236)
    d.ellipse([cx - pr, cy - pr, cx + pr, cy + pr], fill=RED + (255,), outline=BG + (255,), width=max(1, int(S(8))))
    return img


def main():
    os.makedirs(ASSETS, exist_ok=True)
    master = render(2048).resize((1024, 1024), Image.LANCZOS)
    master.save(os.path.join(ASSETS, "logo_1024.png"))
    iconset = os.path.join(ASSETS, "AppIcon.iconset")
    os.makedirs(iconset, exist_ok=True)
    for name, sz in {"icon_16x16.png": 16, "icon_16x16@2x.png": 32, "icon_32x32.png": 32,
                     "icon_32x32@2x.png": 64, "icon_128x128.png": 128, "icon_128x128@2x.png": 256,
                     "icon_256x256.png": 256, "icon_256x256@2x.png": 512, "icon_512x512.png": 512,
                     "icon_512x512@2x.png": 1024}.items():
        master.resize((sz, sz), Image.LANCZOS).save(os.path.join(iconset, name))
    print("ok: logo_1024.png, AppIcon.iconset/")


if __name__ == "__main__":
    main()
