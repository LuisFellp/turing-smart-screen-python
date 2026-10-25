"""Gera background.png do tema aurora-luis (320x480): céu noturno com auroras, estrelas e cartões de vidro fosco.

Rode de dentro do projeto:  python res/themes/aurora-luis/gerar_fundo.py
Cores e layout ficam nas constantes abaixo; theme.yaml usa as mesmas coordenadas (CARD_Y, RING_*).
"""
import math
import os

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

W, H = 320, 480
HERE = os.path.dirname(os.path.abspath(__file__))
FONT_PATHS = [
    os.path.join(HERE, "..", "..", "fonts", "tektur", "Tektur-Bold.ttf"),
    os.path.join(HERE, "..", "..", "..", "fonts", "tektur", "Tektur-Bold.ttf"),
]

# Paleta (texto e anéis)
CPU = (255, 107, 150)
GPU = (64, 224, 214)
RAM = (190, 164, 255)
DIM = (204, 215, 242)

# Cartões: x0, x1, y0 e altura; o 4º é o painel de ventoinhas/FPS
CARD_X0, CARD_X1 = 12, 308
CARD_H = 120
CARD_Y = [12, 142, 272]
BOTTOM = (12, 402, 308, 468)

# Anel: centro relativo ao cartão, raio, espessura, ângulos (iguais aos do theme.yaml)
RING_CX, RING_CY_OFF, RING_R, RING_W = 70, 60, 48, 9
RING_START, RING_END, RING_STEPS, RING_SEP = 135, 45, 27, 3


def font(size):
    for p in FONT_PATHS:
        if os.path.exists(p):
            return ImageFont.truetype(p, size)
    raise FileNotFoundError("Tektur-Bold.ttf não encontrada em res/fonts/tektur")


def aurora_sky():
    rng = np.random.default_rng(7)
    ys, xs = np.mgrid[0:H, 0:W].astype(np.float32)

    # céu: azul-marinho profundo, um pouco mais claro perto do horizonte (embaixo)
    t = ys / H
    sky = np.zeros((H, W, 3), np.float32)
    sky[..., 0] = 8 + 14 * t
    sky[..., 1] = 10 + 22 * t
    sky[..., 2] = 28 + 44 * t

    # fitas de aurora: cada uma é uma curva senoidal com brilho gaussiano na vertical
    ribbons = [
        # (y médio, amplitude, fase, cor, largura, intensidade)
        (95, 34, 0.0, (40, 235, 150), 38, 1.00),
        (150, 28, 1.7, (70, 200, 255), 46, 0.80),
        (330, 36, 3.1, (150, 90, 255), 44, 0.95),
        (410, 26, 4.4, (40, 235, 170), 36, 0.85),
        (240, 30, 5.2, (255, 90, 170), 40, 0.55),
    ]
    glow = np.zeros_like(sky)
    for y0, amp, ph, col, wid, inten in ribbons:
        centre = (y0 + amp * np.sin(xs / 52.0 + ph) + 0.5 * amp * np.sin(xs / 21.0 + 2 * ph)
                  + 0.12 * (xs - W / 2))
        prof = np.exp(-((ys - centre) ** 2) / (2 * wid ** 2))
        # estrias verticais finas, típicas de aurora
        streak = 0.72 + 0.28 * np.sin(xs / 3.1 + 5 * np.sin(xs / 37.0 + ph))
        k = (prof * streak * inten)[..., None]
        glow += k * np.array(col, np.float32)
    img = np.clip(sky + glow * 0.62, 0, 255).astype(np.uint8)
    im = Image.fromarray(img, "RGB").filter(ImageFilter.GaussianBlur(1.2))

    # estrelas
    d = ImageDraw.Draw(im)
    for _ in range(95):
        x, y = int(rng.integers(0, W)), int(rng.integers(0, H))
        b = int(rng.integers(120, 255))
        d.point((x, y), fill=(b, b, min(255, b + 20)))
    for _ in range(9):
        x, y = int(rng.integers(6, W - 6)), int(rng.integers(6, H - 6))
        d.line([(x - 2, y), (x + 2, y)], fill=(235, 240, 255))
        d.line([(x, y - 2), (x, y + 2)], fill=(235, 240, 255))
    return im


def glass(im, box, radius=18):
    """Vidro fosco: borra e escurece o que está atrás, com borda fina e brilho no topo."""
    x0, y0, x1, y1 = box
    region = im.crop(box).filter(ImageFilter.GaussianBlur(5))
    dark = Image.new("RGB", region.size, (6, 9, 24))
    region = Image.blend(region, dark, 0.70)
    mask = Image.new("L", region.size, 0)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, region.width - 1, region.height - 1], radius=radius, fill=255)
    im.paste(region, (x0, y0), mask)
    d = ImageDraw.Draw(im, "RGBA")
    d.rounded_rectangle([x0, y0, x1 - 1, y1 - 1], radius=radius, outline=(170, 200, 255, 70), width=1)
    d.line([(x0 + radius, y0 + 1), (x1 - radius, y0 + 1)], fill=(220, 235, 255, 90))


def ring_track(im, cx, cy, color):
    """Trilho do anel: os mesmos segmentos que a biblioteca desenha, em tom apagado."""
    d = ImageDraw.Draw(im)
    start = RING_START % 361
    end = RING_END % 361
    ecart = 360 - start + end if end < start else end - start
    step = ecart / RING_STEPS
    track = tuple(int(c * 0.22 + 14) for c in color)
    box = [cx - RING_R, cy - RING_R, cx + RING_R - 1, cy + RING_R - 1]
    for i in range(RING_STEPS):
        d.arc(box, start + i * step, start + (i + 1) * step - RING_SEP, fill=track, width=RING_W)


def main():
    im = aurora_sky()
    for y in CARD_Y:
        glass(im, (CARD_X0, y, CARD_X1, y + CARD_H))
    glass(im, BOTTOM)

    d = ImageDraw.Draw(im, "RGBA")
    f_tag, f_cap = font(15), font(11)

    cards = [("CPU", CPU, "CLOCK"), ("GPU", GPU, "VRAM"), ("RAM", RAM, "LIVRE")]
    for y, (tag, color, cap) in zip(CARD_Y, cards):
        ring_track(im, RING_CX, y + RING_CY_OFF, color)
        d.text((132, y + 12), tag, font=f_tag, fill=color, anchor="lt")
        d.text((132, y + 92), cap, font=f_cap, fill=DIM, anchor="lt")

    # painel de baixo: ventoinhas à esquerda, FPS à direita
    d.text((26, 414), "RAD", font=f_tag, fill=CPU, anchor="lt")
    d.text((26, 440), "GPU", font=f_tag, fill=GPU, anchor="lt")
    d.line([(198, 414), (198, 456)], fill=(170, 200, 255, 70), width=1)
    d.text((214, 412), "FPS", font=f_cap, fill=DIM, anchor="lt")

    im.save(os.path.join(HERE, "background.png"))
    print("background.png gerado")


if __name__ == "__main__":
    main()
