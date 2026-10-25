"""Gera background.png do tema aneis-luis (480x320, paisagem): três anéis concêntricos (CPU, GPU, RAM) com hardware no centro e FPS embaixo, na coluna da direita.

Rode de dentro do projeto:  python res/themes/aneis-luis/gerar_fundo.py
Os anéis desenhados aqui são só o trilho apagado; o preenchimento vem do RADIAL do theme.yaml (mesmas constantes).
"""
import math
import os

import numpy as np
from PIL import Image, ImageDraw, ImageFont

W, H = 480, 320
HERE = os.path.dirname(os.path.abspath(__file__))
FONT_DIRS = [os.path.join(HERE, "..", "..", "fonts", "roboto"), os.path.join(HERE, "..", "..", "..", "fonts", "roboto")]

BG = (8, 10, 18)
CPU, GPU, RAM = (255, 72, 110), (40, 214, 255), (170, 255, 90)
DIM = (150, 160, 190)

CX, CY = 150, 160
RINGS = [(146, CPU), (116, GPU), (86, RAM)]   # raio externo, cor
RING_W = 24
START, END, STEPS, SEP = 270, 260, 40, 2      # igual ao theme.yaml
COL_X = 316
BLOCK_Y = [4, 86, 168]


def font(name, size):
    for d in FONT_DIRS:
        p = os.path.join(d, name)
        if os.path.exists(p):
            return ImageFont.truetype(p, size)
    raise FileNotFoundError(name)


def main():
    ys, xs = np.mgrid[0:H, 0:W].astype(np.float32)
    dist = np.hypot(xs - CX, ys - CY)
    glow = np.clip(1 - dist / 260, 0, 1) ** 2
    img = np.zeros((H, W, 3), np.float32)
    for i, c in enumerate(BG):
        img[..., i] = c + glow * (10, 14, 26)[i]
    im = Image.fromarray(img.astype(np.uint8), "RGB")
    d = ImageDraw.Draw(im, "RGBA")

    # marcas de relógio ao redor do anel externo
    for k in range(60):
        a = math.radians(k * 6 - 90)
        r1, r2 = 150, 154 if k % 5 else 158
        d.line([(CX + r1 * math.cos(a), CY + r1 * math.sin(a)), (CX + r2 * math.cos(a), CY + r2 * math.sin(a))],
               fill=(120, 130, 170, 90 if k % 5 else 170), width=1)

    # trilhos dos anéis: os mesmos segmentos que a biblioteca desenha
    ecart = 360 - START + END
    step = ecart / STEPS
    for r, col in RINGS:
        track = tuple(int(c * 0.17 + 9) for c in col)
        box = [CX - r, CY - r, CX + r - 1, CY + r - 1]
        for i in range(STEPS):
            d.arc(box, START + i * step, START + (i + 1) * step - SEP, fill=track, width=RING_W)

    # coluna da direita
    d.line([(COL_X - 12, 14), (COL_X - 12, 306)], fill=(120, 130, 170, 60), width=1)
    f_tag = font("Roboto-Black.ttf", 13)
    for y, (name, col) in zip(BLOCK_Y, (("CPU", CPU), ("GPU", GPU), ("RAM", RAM))):
        d.ellipse([COL_X, y + 3, COL_X + 9, y + 12], fill=col)
        d.text((COL_X + 16, y), name, font=f_tag, fill=col, anchor="lt")
    # centro (o anel de RAM apaga o miolo a cada ciclo, então só texto fixo aqui) e rodapé
    d.text((CX, CY - 12), "9800X3D", font=font("Roboto-Black.ttf", 20), fill=(235, 238, 250), anchor="mm")
    d.text((CX, CY + 12), "RX 9070 XT", font=font("Roboto-Bold.ttf", 13), fill=DIM, anchor="mm")
    d.line([(COL_X, 244), (W - 14, 244)], fill=(120, 130, 170, 60), width=1)
    d.text((COL_X, 262), "FPS", font=font("Roboto-Black.ttf", 13), fill=(235, 238, 250), anchor="lt")
    d.text((COL_X, 290), "RAD", font=font("Roboto-Black.ttf", 12), fill=CPU, anchor="lt")
    d.text((COL_X, 303), "GPU", font=font("Roboto-Black.ttf", 12), fill=GPU, anchor="lt")

    im.save(os.path.join(HERE, "background.png"))
    print("background.png gerado")


if __name__ == "__main__":
    main()
