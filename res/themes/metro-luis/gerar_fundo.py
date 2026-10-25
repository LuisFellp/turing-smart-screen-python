"""Gera background.png do tema metro-luis (480x320, paisagem): mapa de metrô com uma linha por sensor.

Rode de dentro do projeto:  python res/themes/metro-luis/gerar_fundo.py
As constantes de layout abaixo são as mesmas usadas no theme.yaml (barras em X=BAR_X, Y=row+BAR_DY etc.).
"""
import os

from PIL import Image, ImageDraw, ImageFont

W, H = 480, 320
HERE = os.path.dirname(os.path.abspath(__file__))
FONT_DIRS = [os.path.join(HERE, "..", "..", "fonts", "roboto"), os.path.join(HERE, "..", "..", "..", "fonts", "roboto")]

PAPER = (245, 240, 228)
GRID = (233, 226, 208)
INK = (28, 32, 48)
MUTED = (112, 108, 98)
NAVY = (22, 28, 50)
WHITE = (255, 255, 255)

RED, BLUE, GREEN = (226, 54, 62), (28, 110, 222), (24, 156, 92)

HEADER_H = 36
FOOTER_Y = 288
ROW_Y = [36, 120, 204]   # topo de cada linha (altura 84)
ROW_H = 84
BADGE_CX, BADGE_R = 38, 22
BAR_X, BAR_DY, BAR_W, BAR_H = 76, 32, 250, 16
TRACK_TINT = 0.22


def font(name, size):
    for d in FONT_DIRS:
        p = os.path.join(d, name)
        if os.path.exists(p):
            return ImageFont.truetype(p, size)
    raise FileNotFoundError(name)


def tint(c, k):
    return tuple(int(255 - (255 - v) * k) for v in c)


def main():
    S = 4  # supersampling só para formas (círculos, trilhos); texto é desenhado depois, em 1x
    big = Image.new("RGB", (W * S, H * S), PAPER)
    d = ImageDraw.Draw(big)

    # papel quadriculado
    for x in range(0, W, 20):
        d.line([(x * S, 0), (x * S, H * S)], fill=GRID, width=S // 2)
    for y in range(0, H, 20):
        d.line([(0, y * S), (W * S, y * S)], fill=GRID, width=S // 2)

    # cabeçalho e rodapé
    d.rectangle([0, 0, W * S, HEADER_H * S], fill=NAVY)
    d.rectangle([0, FOOTER_Y * S, W * S, H * S], fill=NAVY)
    for i, c in enumerate((RED, BLUE, GREEN)):
        d.ellipse([(16 + i * 16) * S, 13 * S, (26 + i * 16) * S, 23 * S], fill=c)

    rows = [(RED, "C", "CPU"), (BLUE, "G", "GPU"), (GREEN, "R", "RAM")]
    for ty, (color, letter, name) in zip(ROW_Y, rows):
        # linha de separação
        d.line([(16 * S, (ty + ROW_H) * S), ((W - 16) * S, (ty + ROW_H) * S)], fill=(214, 206, 186), width=S)
        # "trilho" da linha: a barra preenche por cima. Estações nos 5 marcos (0/25/50/75/100 %)
        by = ty + BAR_DY
        d.rounded_rectangle([BAR_X * S, by * S, (BAR_X + BAR_W) * S - 1, (by + BAR_H) * S - 1],
                            radius=BAR_H * S // 2, fill=tint(color, TRACK_TINT))
        for k in range(5):
            sx = BAR_X + 8 + k * (BAR_W - 16) / 4
            sy = by + BAR_H + 7
            d.ellipse([(sx - 3) * S, (sy - 3) * S, (sx + 3) * S, (sy + 3) * S], fill=PAPER, outline=color, width=S)
        # selo da linha
        cy = ty + ROW_H // 2
        d.ellipse([(BADGE_CX - BADGE_R) * S, (cy - BADGE_R) * S, (BADGE_CX + BADGE_R) * S, (cy + BADGE_R) * S], fill=color)

    im = big.resize((W, H), Image.LANCZOS)
    t = ImageDraw.Draw(im)

    f_badge, f_name, f_cap = font("Roboto-Black.ttf", 26), font("Roboto-Black.ttf", 20), font("Roboto-Bold.ttf", 11)
    t.text((100, 11), "LINHAS DO SISTEMA", font=font("Roboto-Black.ttf", 15), fill=WHITE, anchor="lt")
    t.text((340, 12), "FPS", font=font("Roboto-Bold.ttf", 13), fill=(190, 200, 230), anchor="lt")
    for ty, (color, letter, name) in zip(ROW_Y, rows):
        cy = ty + ROW_H // 2
        t.text((BADGE_CX, cy), letter, font=f_badge, fill=WHITE, anchor="mm")
        t.text((BAR_X, ty + 6), name, font=f_name, fill=INK, anchor="lt")
    t.text((16, 297), "RAD", font=font("Roboto-Black.ttf", 15), fill=(255, 130, 136), anchor="lt")
    t.text((250, 297), "GPU", font=font("Roboto-Black.ttf", 15), fill=(120, 175, 255), anchor="lt")

    im.save(os.path.join(HERE, "background.png"))
    print("background.png gerado")


if __name__ == "__main__":
    main()
