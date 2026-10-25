# Gera background.png (320x480) do tema taiko-luis.
# Desenho original inspirado na paleta de Taiko no Tatsujin (sem personagens nem logos oficiais).
# Rodar a partir da raiz do projeto:  .venv\Scripts\python.exe res\themes\taiko-luis\gerar_fundo.py

import math
import os

from PIL import Image, ImageDraw, ImageFont

W, H = 320, 480
AQUI = os.path.dirname(os.path.abspath(__file__))
FONTE = os.path.join(AQUI, "..", "..", "fonts", "mplus-rounded", "MPLUSRounded1c-Black.ttf")

# Paleta
VERMELHO = (232, 67, 46)      # don
VERMELHO_ESC = (176, 40, 28)
AZUL = (45, 155, 216)         # ka
AZUL_ESC = (24, 104, 160)
AMARELO = (255, 205, 60)
AMARELO_CLARO = (255, 226, 120)
LARANJA = (240, 138, 28)
CREME = (255, 228, 168)
MARROM = (58, 31, 18)
BRANCO = (255, 255, 255)

# Layout (as posições do theme.yaml dependem destes valores)
MARGEM = 10
PAINEIS = [  # (y0, y1, cor de destaque, rótulo)
    (64, 148, VERMELHO, "CPU"),
    (156, 240, AZUL, "GPU"),
    (248, 332, LARANJA, "RAM"),
]
FANS = (340, 410)
PISTA = (418, 472)


def ondas_seigaiha(d, x0, y0, x1, y1, raio, cor_a, cor_b):
    """Padrão de ondas japonesas (seigaiha) preenchendo o retângulo."""
    passo_y = raio // 2
    linha = 0
    y = y0 - raio
    while y < y1 + raio:
        deslocamento = raio if linha % 2 else 0
        x = x0 - raio * 2 + deslocamento
        while x < x1 + raio * 2:
            for k, r in enumerate(range(raio, 2, -max(3, raio // 4))):
                cor = cor_a if k % 2 == 0 else cor_b
                d.ellipse((x - r, y - r, x + r, y + r), fill=cor)
            x += raio * 2
        y += passo_y
        linha += 1


def painel(d, x0, y0, x1, y1, raio=12, borda=3):
    d.rounded_rectangle((x0 + 3, y0 + 4, x1 + 3, y1 + 4), raio, fill=MARROM)  # sombra
    d.rounded_rectangle((x0, y0, x1, y1), raio, fill=CREME, outline=MARROM, width=borda)


def nota(d, cx, cy, r, cor, cor_esc):
    """Nota estilo Taiko: círculo colorido com aro branco e contorno escuro."""
    d.ellipse((cx - r - 3, cy - r - 3, cx + r + 3, cy + r + 3), fill=MARROM)
    d.ellipse((cx - r, cy - r, cx + r, cy + r), fill=BRANCO)
    ri = int(r * 0.78)
    d.ellipse((cx - ri, cy - ri, cx + ri, cy + ri), fill=cor)
    rb = int(ri * 0.55)
    d.ellipse((cx - rb - 2, cy - rb - 2, cx - rb + rb // 2, cy - rb + rb // 2), fill=tuple(min(255, c + 60) for c in cor))
    _ = cor_esc


def tambor(d, cx, cy, r):
    """Tambor visto de frente: pele creme, aro vermelho, tachas."""
    d.ellipse((cx - r - 3, cy - r - 3, cx + r + 3, cy + r + 3), fill=MARROM)
    d.ellipse((cx - r, cy - r, cx + r, cy + r), fill=VERMELHO)
    for i in range(10):
        a = i * math.tau / 10
        tx, ty = cx + math.cos(a) * (r - 4), cy + math.sin(a) * (r - 4)
        d.ellipse((tx - 1.6, ty - 1.6, tx + 1.6, ty + 1.6), fill=AMARELO)
    ri = int(r * 0.72)
    d.ellipse((cx - ri, cy - ri, cx + ri, cy + ri), fill=CREME, outline=MARROM, width=2)


def texto_contornado(d, xy, txt, fonte, cor, contorno=MARROM, esp=3):
    d.text(xy, txt, font=fonte, fill=cor, stroke_width=esp, stroke_fill=contorno)


def gerar():
    img = Image.new("RGB", (W, H), AMARELO)
    d = ImageDraw.Draw(img)

    # Fundo inteiro estampado (disfarça manchas): ondas amarelas
    ondas_seigaiha(d, 0, 0, W, H, 18, AMARELO_CLARO, AMARELO)

    # Cabeçalho vermelho com ondas mais escuras
    cab = Image.new("RGB", (W, 58), VERMELHO)
    dc = ImageDraw.Draw(cab)
    ondas_seigaiha(dc, 0, 0, W, 58, 14, VERMELHO, VERMELHO_ESC)
    img.paste(cab, (0, 0))
    d.rectangle((0, 56, W, 59), fill=MARROM)

    f_cab = ImageFont.truetype(FONTE, 26)
    f_jp = ImageFont.truetype(FONTE, 15)
    tambor(d, 34, 29, 20)
    texto_contornado(d, (64, 8), "TAIKO", f_cab, BRANCO)
    texto_contornado(d, (162, 4), "ドン!", f_jp, AMARELO, esp=2)
    texto_contornado(d, (164, 28), "カッ!", f_jp, (150, 220, 255), esp=2)
    nota(d, 246, 29, 13, VERMELHO, VERMELHO_ESC)
    nota(d, 284, 29, 13, AZUL, AZUL_ESC)

    # Painéis CPU / GPU / RAM com etiqueta colorida
    f_tag = ImageFont.truetype(FONTE, 14)
    for y0, y1, cor, rotulo in PAINEIS:
        painel(d, MARGEM, y0, W - MARGEM, y1)
        d.rounded_rectangle((MARGEM + 8, y0 + 7, MARGEM + 62, y0 + 27), 10, fill=cor, outline=MARROM, width=2)
        bw = d.textlength(rotulo, font=f_tag)
        d.text((MARGEM + 35 - bw / 2, y0 + 7), rotulo, font=f_tag, fill=BRANCO)
        # faixa decorativa na lateral direita do painel
        d.rounded_rectangle((W - MARGEM - 8, y0 + 12, W - MARGEM - 4, y1 - 12), 2, fill=cor)

    # Painel das ventoinhas
    painel(d, MARGEM, FANS[0], W - MARGEM, FANS[1])
    d.line((MARGEM + 10, (FANS[0] + FANS[1]) // 2, W - MARGEM - 10, (FANS[0] + FANS[1]) // 2), fill=(230, 210, 170), width=2)

    # Pista de notas (FPS) na base, onde a tela tem mais manchas: bem colorida
    py0, py1 = PISTA
    d.rounded_rectangle((MARGEM + 3, py0 + 4, W - MARGEM + 3, py1 + 4), 14, fill=MARROM)
    d.rounded_rectangle((MARGEM, py0, W - MARGEM, py1), 14, fill=(70, 46, 38), outline=MARROM, width=3)
    d.rectangle((MARGEM + 6, py0 + 10, W - MARGEM - 6, py1 - 10), fill=(96, 66, 54))
    cy = (py0 + py1) // 2
    # círculo de acerto
    d.ellipse((MARGEM + 12, cy - 19, MARGEM + 50, cy + 19), outline=CREME, width=3)
    d.ellipse((MARGEM + 20, cy - 11, MARGEM + 42, cy + 11), outline=(200, 180, 150), width=2)
    for x, cor, cesc, r in [(232, VERMELHO, VERMELHO_ESC, 13), (262, AZUL, AZUL_ESC, 11), (290, VERMELHO, VERMELHO_ESC, 11)]:
        nota(d, x, cy, r, cor, cesc)

    img.save(os.path.join(AQUI, "background.png"))
    print("background.png gerado")


if __name__ == "__main__":
    gerar()
