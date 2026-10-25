"""Gera background.png do tema fade-luis (480x320, paisagem): entardecer enevoado, bokeh e grão de filme.

Rode de dentro do projeto:  python res/themes/fade-luis/gerar_fundo.py
As constantes de layout abaixo são as mesmas usadas no theme.yaml (barras em X=BAR_X, Y=linha+BAR_DY etc.).
"""
import os

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

W, H = 480, 320
HERE = os.path.dirname(os.path.abspath(__file__))
FONT_DIRS = [os.path.join(HERE, "..", "..", "fonts", "maple-mono"), os.path.join(HERE, "..", "..", "..", "fonts", "maple-mono")]

ROSE, BLUE, GOLD = (255, 150, 172), (150, 172, 255), (255, 214, 150)
LILAC = (240, 232, 250)
MUTED = (186, 172, 218)

ROW_Y = [22, 112, 202]          # topo de cada linha
BAR_X, BAR_DY, BAR_W, BAR_H = 196, 34, 258, 8
TRACK_ALPHA = 0.22
rng = np.random.default_rng(5)


def font(name, size):
    for d in FONT_DIRS:
        p = os.path.join(d, f"MapleMono-{name}.ttf")
        if os.path.exists(p):
            return ImageFont.truetype(p, size)
    raise FileNotFoundError(name)


def haze():
    ys, xs = np.mgrid[0:H, 0:W].astype(np.float32)
    t = ys / H
    stops = [(0.0, (14, 12, 38)), (0.45, (46, 30, 86)), (0.80, (112, 58, 120)), (1.0, (196, 98, 128))]
    img = np.zeros((H, W, 3), np.float32)
    for (t0, c0), (t1, c1) in zip(stops, stops[1:]):
        m = (t >= t0) & (t <= t1)
        k = ((t - t0) / (t1 - t0))[m][:, None]
        img[m] = np.array(c0) * (1 - k) + np.array(c1) * k
    # névoa clara que se desfaz da direita para a esquerda
    d = np.hypot((xs - 430) / 1.4, ys - 110)
    img += (np.clip(1 - d / 230, 0, 1) ** 2)[..., None] * np.array([46, 24, 40])
    vig = np.clip(1 - np.hypot((xs - W / 2) / (W * 0.75), (ys - H / 2) / (H * 0.8)), 0.55, 1)
    return img * vig[..., None]


def main():
    im = Image.fromarray(np.clip(haze(), 0, 255).astype(np.uint8), "RGB")

    # bokeh: discos grandes, desfocados, em tons da paleta
    bokeh = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    bd = ImageDraw.Draw(bokeh)
    for _ in range(26):
        r = int(rng.integers(8, 34))
        x, y = int(rng.integers(0, W)), int(rng.integers(0, H))
        c = [ROSE, BLUE, GOLD, (200, 140, 255)][int(rng.integers(0, 4))]
        bd.ellipse([x - r, y - r, x + r, y + r], fill=c + (int(rng.integers(14, 34)),))
    bokeh = bokeh.filter(ImageFilter.GaussianBlur(4))
    im = Image.alpha_composite(im.convert("RGBA"), bokeh)

    # trilhos das barras: uma linha de luz que se desfaz para a direita (o preenchimento vem do theme.yaml)
    glow = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    gd = ImageDraw.Draw(glow)
    for y, c in zip(ROW_Y, (ROSE, BLUE, GOLD)):
        by = y + BAR_DY
        for x in range(BAR_W):
            a = int(255 * TRACK_ALPHA * (1 - 0.8 * x / BAR_W))
            gd.line([(BAR_X + x, by + 1), (BAR_X + x, by + BAR_H - 2)], fill=c + (a,))
    halo = glow.filter(ImageFilter.GaussianBlur(5))
    im = Image.alpha_composite(im, halo)
    im = Image.alpha_composite(im, glow)
    im = im.convert("RGB")

    # grão de filme
    arr = np.asarray(im).astype(np.float32) + rng.normal(0, 3.2, (H, W, 1))
    im = Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8), "RGB")

    d = ImageDraw.Draw(im)
    f_tag = font("Italic", 15)
    for y, (name, c) in zip(ROW_Y, (("cpu", ROSE), ("gpu", BLUE), ("ram", GOLD))):
        d.text((26, y), name, font=f_tag, fill=c, anchor="lt")
    f_foot = font("Italic", 13)
    d.text((26, 296), "fps", font=f_foot, fill=MUTED, anchor="lt")
    d.text((196, 296), "rad", font=f_foot, fill=MUTED, anchor="lt")
    d.text((340, 296), "gpu", font=f_foot, fill=MUTED, anchor="lt")
    im.save(os.path.join(HERE, "background.png"))
    print("background.png gerado")


if __name__ == "__main__":
    main()
