"""Gera background.png do tema anything-luis (480x320, paisagem): pôr do sol pintado à mão, cartões de papel e ícones rabiscados.

Rode de dentro do projeto:  python res/themes/anything-luis/gerar_fundo.py
As constantes de layout abaixo são as mesmas usadas no theme.yaml (barras em X=BAR_X, Y=cartão+BAR_DY etc.).
"""
import math
import os

import numpy as np
from PIL import Image, ImageDraw, ImageFont

W, H = 480, 320
HERE = os.path.dirname(os.path.abspath(__file__))
FONT_DIRS = [os.path.join(HERE, "..", "..", "fonts", "patrickhand"), os.path.join(HERE, "..", "..", "..", "fonts", "patrickhand")]

INK = (74, 44, 48)
MUTED = (150, 104, 98)
CREAM = (255, 246, 232)
TOMATO, PLUM, SAGE = (222, 84, 64), (150, 72, 128), (88, 140, 80)

CARD_X0, CARD_X1, CARD_H = 12, 468, 88
CARD_Y = [10, 104, 198]
BAR_X, BAR_DY, BAR_W, BAR_H = 76, 46, 282, 18   # igual ao theme.yaml
TRACK_TINT = 0.24
S = 4
rng = np.random.default_rng(11)


def font(size):
    for d in FONT_DIRS:
        p = os.path.join(d, "PatrickHand-Regular.ttf")
        if os.path.exists(p):
            return ImageFont.truetype(p, size)
    raise FileNotFoundError("PatrickHand-Regular.ttf")


def tint(c, k):
    return tuple(int(255 - (255 - v) * k) for v in c)


def sky():
    ys, xs = np.mgrid[0:H, 0:W].astype(np.float32)
    t = ys / H
    stops = [(0.0, (255, 226, 160)), (0.35, (255, 186, 140)), (0.65, (240, 130, 130)), (1.0, (150, 76, 120))]
    img = np.zeros((H, W, 3), np.float32)
    for (t0, c0), (t1, c1) in zip(stops, stops[1:]):
        m = (t >= t0) & (t <= t1)
        k = ((t - t0) / (t1 - t0))[m][:, None]
        img[m] = np.array(c0) * (1 - k) + np.array(c1) * k
    # brilho quente de fim de tarde
    d = np.hypot(xs - 392, ys - 150)
    glow = np.clip(1 - d / 210, 0, 1) ** 2.2
    img += glow[..., None] * np.array([95, 70, 30])
    return img


def wobble_line(d, pts, fill, width, jitter=0.8):
    j = [(x + rng.normal(0, jitter), y + rng.normal(0, jitter)) for x, y in pts]
    d.line(j, fill=fill, width=width, joint="curve")


def sun_icon(d, cx, cy, color):
    r = 11 * S
    d.ellipse([cx - r, cy - r, cx + r, cy + r], outline=color, width=3 * S // 2)
    for k in range(10):
        a = k * math.tau / 10 + 0.15
        r1, r2 = 16 * S, (23 + (k % 2) * 3) * S
        wobble_line(d, [(cx + r1 * math.cos(a), cy + r1 * math.sin(a)), (cx + r2 * math.cos(a), cy + r2 * math.sin(a))],
                    color, 3 * S // 2, 1.2)


def flower_icon(d, cx, cy, color):
    for k in range(6):
        a = k * math.tau / 6
        px, py = cx + 13 * S * math.cos(a), cy + 13 * S * math.sin(a)
        r = 9 * S
        d.ellipse([px - r, py - r, px + r, py + r], outline=color, width=3 * S // 2)
    r = 6 * S
    d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=(255, 205, 90), outline=color, width=S)


def leaf_icon(d, cx, cy, color):
    pts = []
    for k in range(25):
        t = k / 24
        pts.append((cx - 20 * S + 40 * S * t, cy + 13 * S * math.sin(t * math.pi) * 0.9 - 4 * S * t))
    back = [(x, cy - (y - cy) - 8 * S * (x - (cx - 20 * S)) / (40 * S)) for x, y in pts]
    d.polygon(pts + back[::-1], outline=color, fill=tint(color, 0.18))
    d.line(pts + back[::-1] + [pts[0]], fill=color, width=3 * S // 2, joint="curve")
    wobble_line(d, [(cx - 20 * S, cy + 2 * S), (cx + 16 * S, cy - 6 * S)], color, S)
    wobble_line(d, [(cx - 24 * S, cy + 5 * S), (cx - 20 * S, cy + 2 * S)], color, 3 * S // 2)




def main():
    base = sky()
    base += rng.normal(0, 4.0, (H, W, 1))          # grão de filme
    im = Image.fromarray(np.clip(base, 0, 255).astype(np.uint8), "RGB").resize((W * S, H * S), Image.BICUBIC)
    d = ImageDraw.Draw(im, "RGBA")

    rows = [(TOMATO, sun_icon), (PLUM, flower_icon), (SAGE, leaf_icon)]
    for y, (color, icon) in zip(CARD_Y, rows):
        # cartão de papel: sombra suave, folha creme meio transparente
        d.rounded_rectangle([(CARD_X0 + 2) * S, (y + 4) * S, (CARD_X1 + 2) * S, (y + CARD_H + 4) * S], radius=26 * S,
                            fill=(90, 40, 60, 46))
        d.rounded_rectangle([CARD_X0 * S, y * S, CARD_X1 * S, (y + CARD_H) * S], radius=26 * S,
                            fill=CREAM + (200,), outline=tint(color, 0.45) + (255,), width=S)
        icon(d, 40 * S, (y + 40) * S, color)
        # trilho da barra: o preenchimento vem por cima, vindo do theme.yaml
        by = y + BAR_DY
        d.rounded_rectangle([BAR_X * S, by * S, (BAR_X + BAR_W) * S - 1, (by + BAR_H) * S - 1], radius=BAR_H * S // 2,
                            fill=tint(color, TRACK_TINT) + (255,))

    out = im.resize((W, H), Image.LANCZOS)
    t = ImageDraw.Draw(out)
    f_name, f_foot = font(26), font(17)
    for y, (name, color) in zip(CARD_Y, (("cpu", TOMATO), ("gpu", PLUM), ("ram", SAGE))):
        t.text((BAR_X, y + 8), name, font=f_name, fill=color, anchor="lt")
    t.text((24, 294), "fps", font=f_foot, fill=(255, 244, 228), anchor="lt")
    t.text((236, 294), "rad", font=f_foot, fill=(255, 244, 228), anchor="lt")
    t.text((352, 294), "gpu", font=f_foot, fill=(255, 244, 228), anchor="lt")
    out.save(os.path.join(HERE, "background.png"))
    print("background.png gerado")


if __name__ == "__main__":
    main()
