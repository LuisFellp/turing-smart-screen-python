"""Osciloscópio de fósforo: a telinha vira um osciloscópio de 4 canais do próprio PC (480x320, paisagem).

Cada traço é o próprio sinal medido ao longo do tempo, como num osciloscópio de verdade:

  CH1 cpu     carga da CPU (psutil), 10 leituras/s, 0-100 % (50 %/div)
  CH2 gpu 3d  carga do motor 3D da GPU (contador "GPU Engine" do Windows, o mesmo do Gerenciador de Tarefas),
              10 leituras/s, 0-100 % (50 %/div); o número do painel usa a mesma fonte
  CH3 ram     memória em uso (psutil), 10 leituras/s, ampliada a 1 %/div em volta da média recente (acoplamento AC)
  CH4 temp    a mais quente entre CPU Tctl e hot spot da GPU (HWiNFO, que só atualiza a cada 2 s: o traço sai em
              degraus), 5 °C/div em volta da média recente
Os traços são desenhados com DELAY de atraso para que cada ponto fique entre duas leituras reais (interpolação
linear); a temperatura não é interpolada (mantém a última leitura), porque não há leitura entre os degraus.

Os traçados são desenhados com o comando 195 (pixels soltos), decodificado do app oficial: pacote fixo de 64 bytes
com cabeçalho de 6 bytes (x, y = origem; ex = bytes de dados, até 56; comando 195), uma cor RGB565 big-endian e até
28 pares (dx, dy) relativos à origem. Medido nesta telinha: 1000 pacotes/s (28.000 pixels/s) sem perdas. Como cada
pacote leva uma cor só, o fundo da área dos traçados tem só preto e a cor da retícula, e o rastro de fósforo usa
4 tons por canal. Só os pixels que mudam são enviados, uma fração do que custaria mandar colunas em bitmap.
Números e textos vão por bitmap (comando 197), em caixas justas, com orçamento por quadro.

Uso (de dentro do projeto):
  pythonw osciloscopio.py                          # roda na telinha (porta e brilho do config.yaml)
  python osciloscopio.py --preview 8 --out x.png   # sem telinha: 8 s, salva o quadro e confere os pacotes 195
"""
import argparse
import collections
import ctypes
import math
import signal
import threading
import time
from ctypes import wintypes
from datetime import datetime

import numpy as np
import psutil
import yaml
from PIL import Image, ImageDraw, ImageFont

from vitais import CPU_TEMP_ALARM, GPU_TEMP_ALARM, SafeLcd, Vitals, fmt, logger, nan

W, H = 480, 320
# FPS, DX e FIELD_BUDGET são padrões; a seção "osciloscopio" do config.yaml os substitui
FPS = 30                 # quadros por segundo
DX = 2                   # colunas novas por quadro (60 px/s)
SUB = 6                  # amostras por coluna
GAP = 10                 # colunas apagadas à frente do feixe
FIELD_BUDGET = 1600      # bytes de bitmap por quadro para números e textos
SLICE_ROWS = 10

DIV = 35
GX0, GY0 = 8, 18
GW, GH = 10 * DIV, 8 * DIV            # retícula de 10 x 8 divisões
LANE = 2 * DIV                        # cada canal ocupa 2 divisões
AMP = 27                              # meia altura máxima do sinal dentro da faixa
PANEL_X0, PANEL_X1 = GX0 + GW + 12, W - 6

BLACK = (0, 0, 0)
GRID, FRAME = (34, 54, 42), (70, 92, 78)
TEXT, MUTED, RED = (226, 234, 228), (120, 140, 128), (255, 70, 80)
CHANNELS = [("cpu", (255, 214, 10)), ("gpu", (40, 220, 255)), ("ram", (255, 90, 210)), ("temp", (90, 255, 130))]
SCALES = ["50%/div", "50%/div", "1%/div", "5°/div"]
RAM_PX_PER_PCT = DIV / 1.0            # 1 %/div
TEMP_PX_PER_C = DIV / 5.0             # 5 °C/div
CENTER_TAU = 20.0                     # s: média recente em volta da qual RAM e temperatura são ampliadas
SAMPLE_HZ = 10                        # leituras por segundo de CPU, GPU e RAM
DELAY = 0.25                          # s de atraso do desenho, para cada ponto ficar entre duas leituras
DEAD = (90, 96, 92)                   # canal sem sinal (sensor ausente): traço reto e cinza
FADE_AT = (60, 170)                   # colunas atrás do feixe em que o traço passa para o tom seguinte
MAX_WAIT = 3                         # quadros que uma troca de tom ou um apagamento pode esperar por pacote cheio


_fonts = {}


def font(weight, size):
    """IBM Plex Sans Condensed, sans condensada como na interface dos osciloscópios atuais (Keysight, Rigol).
    "Medium" e "Bold" usam o Medium; o resto, o Regular."""
    face = "Medium" if weight in ("Medium", "Bold") else "Regular"
    if (face, size) not in _fonts:
        _fonts[(face, size)] = ImageFont.truetype(f"res/fonts/instrument/IBMPlexSansCondensed-{face}.ttf", size)
    return _fonts[(face, size)]


def shades(c):
    """Tons do fósforo: ponta quase branca, cor cheia, meia, um quarto."""
    return (tuple(int(v + (255 - v) * 0.6) for v in c), c,
            tuple(int(v * 0.5) for v in c), tuple(int(v * 0.24) for v in c))


def rgb565(c):
    return ((c[0] << 8) & 0xF800) | ((c[1] << 3) & 0x7E0) | (c[2] >> 3)


def packets195(color, pts):
    """Empacota pixels de uma cor em pacotes 195 de 64 bytes (até 28 pixels, todos a < 256 px da origem).
    Devolve (pacote, pixels do pacote)."""
    v = rgb565(color)
    pts = sorted(pts)
    out = []
    i = 0
    while i < len(pts):
        chunk = [pts[i]]
        y0 = y1 = pts[i][1]
        j = i + 1
        while j < len(pts) and len(chunk) < 28:
            x, y = pts[j]
            if x - chunk[0][0] > 255 or max(y1, y) - min(y0, y) > 255:
                break
            chunk.append(pts[j])
            y0, y1 = min(y0, y), max(y1, y)
            j += 1
        ox, oy = chunk[0][0], y0
        n = 2 * len(chunk)
        pkt = bytearray(64)
        pkt[0] = ox >> 2
        pkt[1] = ((ox & 3) << 6) + (oy >> 4)
        pkt[2] = ((oy & 15) << 4) + (n >> 6)
        pkt[3] = (n & 63) << 2
        pkt[5] = 195
        pkt[6], pkt[7] = v >> 8, v & 0xFF
        for k, (x, y) in enumerate(chunk):
            pkt[8 + 2 * k], pkt[9 + 2 * k] = x - ox, y - oy
        out.append((bytes(pkt), chunk))
        i = j
    return out


class GpuCounter:
    """Utilização do motor 3D da GPU pelo contador do Windows (PDH, "GPU Engine(*engtype_3D)"), somada entre
    processos, como o Gerenciador de Tarefas. Pode ser lido em qualquer ritmo. Devolve NaN se o contador falhar."""

    class _Fmt(ctypes.Structure):
        _fields_ = [("CStatus", wintypes.DWORD), ("doubleValue", ctypes.c_double)]

    class _Item(ctypes.Structure):
        pass

    def __init__(self):
        self.pdh = ctypes.WinDLL("pdh")
        self.query, self.counter = wintypes.HANDLE(), wintypes.HANDLE()
        if self.pdh.PdhOpenQueryW(None, 0, ctypes.byref(self.query)) != 0:
            raise OSError("PdhOpenQueryW falhou")
        r = self.pdh.PdhAddEnglishCounterW(self.query, r"\GPU Engine(*engtype_3D)\Utilization Percentage", 0,
                                           ctypes.byref(self.counter))
        if r != 0:
            raise OSError(f"PdhAddEnglishCounterW falhou ({r & 0xFFFFFFFF:#x})")
        self.pdh.PdhCollectQueryData(self.query)

    def read(self) -> float:
        if self.pdh.PdhCollectQueryData(self.query) != 0:
            return math.nan
        size, count = wintypes.DWORD(0), wintypes.DWORD(0)
        self.pdh.PdhGetFormattedCounterArrayW(self.counter, 0x200, ctypes.byref(size), ctypes.byref(count), None)
        if not size.value:
            return math.nan
        buf = (ctypes.c_byte * size.value)()
        if self.pdh.PdhGetFormattedCounterArrayW(self.counter, 0x200, ctypes.byref(size), ctypes.byref(count),
                                                 buf) != 0:
            return math.nan
        items = ctypes.cast(buf, ctypes.POINTER(self._Item))
        return min(100.0, sum(items[i].FmtValue.doubleValue for i in range(count.value)
                              if items[i].FmtValue.CStatus == 0))


GpuCounter._Item._fields_ = [("szName", wintypes.LPWSTR), ("FmtValue", GpuCounter._Fmt)]


class FastSignals(threading.Thread):
    """Lê CPU, GPU 3D e RAM SAMPLE_HZ vezes por segundo e guarda o histórico recente com o instante de cada leitura."""

    def __init__(self):
        super().__init__(daemon=True)
        self.lock = threading.Lock()
        self.hist = collections.deque(maxlen=SAMPLE_HZ * 5)   # (t, cpu, gpu, ram)

    def run(self):
        try:
            gpu = GpuCounter()
        except OSError:
            logger.exception("osciloscopio: contador de GPU do Windows indisponível")
            gpu = None
        psutil.cpu_percent(None)
        next_t = time.monotonic()
        while True:
            next_t += 1.0 / SAMPLE_HZ
            time.sleep(max(0.0, next_t - time.monotonic()))
            try:
                sample = (time.monotonic(), psutil.cpu_percent(None), gpu.read() if gpu else math.nan,
                          psutil.virtual_memory().percent)
            except Exception:
                logger.exception("osciloscopio: falha ao ler sinais")
                continue
            with self.lock:
                self.hist.append(sample)

    def at(self, t):
        """(cpu, gpu, ram) no instante t, interpolados entre as duas leituras em volta; NaN se não houver leitura."""
        with self.lock:
            h = list(self.hist)
        if not h:
            return math.nan, math.nan, math.nan
        if t <= h[0][0]:
            return h[0][1:]
        for a, b in zip(h, h[1:]):
            if a[0] <= t <= b[0]:
                k = (t - a[0]) / (b[0] - a[0])
                return tuple(va + (vb - va) * k for va, vb in zip(a[1:], b[1:]))
        return h[-1][1:]

    def gpu_mean(self, seconds=1.0):
        with self.lock:
            h = list(self.hist)[-int(SAMPLE_HZ * seconds):]
        vals = [s[2] for s in h if not nan(s[2])]
        return sum(vals) / len(vals) if vals else math.nan


class Scope:
    def __init__(self, lcd, vitals: Vitals, fast: FastSignals):
        self.lcd = lcd
        self.vitals = vitals
        self.fast = fast
        self.bg = self.background()
        self.screen = self.bg.copy()
        self.px = self.screen.load()
        self.bgpx = self.bg.load()
        self.beam = 0
        self.pix = [[None] * GW for _ in CHANNELS]      # por canal e coluna: (ys, cor base)
        self.lvl = [[-1] * GW for _ in CHANNELS]        # tom atual de cada coluna
        self.last_y = [None] * len(CHANNELS)
        self.t_prev = None                              # instante (já com DELAY) da última subamostra desenhada
        self.ram_center = None
        self.temp_center = None
        self.pending_px = {}            # pixel -> (cor mais recente, quadro em que entrou na fila)
        self.heads = {shades(c)[0] for _, c in CHANNELS} | {shades(DEAD)[0]}
        self.frame = 0
        self.shown = {}
        self.fields = self.field_list()
        self.field_i = 0
        self.pending = []

    # ---------- fundo fixo ----------
    def background(self):
        im = Image.new("RGB", (W, H), BLACK)
        d = ImageDraw.Draw(im)
        # retícula: linhas pontilhadas a cada divisão, eixos centrais com marcas, moldura sólida
        for k in range(1, 10):
            x = GX0 + k * DIV
            for y in range(GY0, GY0 + GH, 5):
                d.point((x, y), fill=GRID)
        for k in range(1, 8):
            y = GY0 + k * DIV
            for x in range(GX0, GX0 + GW, 5):
                d.point((x, y), fill=GRID)
        cx, cy = GX0 + GW // 2, GY0 + GH // 2
        for x in range(GX0, GX0 + GW, 7):
            d.line([(x, cy - 2), (x, cy + 2)], fill=GRID)
        for y in range(GY0, GY0 + GH, 7):
            d.line([(cx - 2, y), (cx + 2, y)], fill=GRID)
        d.rectangle([GX0 - 1, GY0 - 1, GX0 + GW, GY0 + GH], outline=FRAME)

        # barra de status, como num osciloscópio: estado e base de tempo (cada divisão = DIV px de varredura)
        d.rounded_rectangle([GX0 - 1, 2, GX0 + 31, 15], radius=3, fill=(30, 120, 60))
        d.text((GX0 + 15, 9), "RUN", font=font("Medium", 11), fill=(220, 255, 230), anchor="mm")
        ms = 1000 * DIV / (FPS * DX)
        d.text((GX0 + 40, 9), "H", font=font("Medium", 12), fill=MUTED, anchor="lm")
        d.text((GX0 + 51, 9), f"{ms:.0f} ms/div", font=font("Regular", 12), fill=TEXT, anchor="lm")
        for k, (name, c) in enumerate(CHANNELS):
            top = GY0 + k * LANE
            d.rounded_rectangle([PANEL_X0, top + 2, PANEL_X0 + 27, top + 15], radius=3, fill=c)
            d.text((PANEL_X0 + 14, top + 9), f"CH{k + 1}", font=font("Medium", 11), fill=BLACK, anchor="mm")
            d.text((PANEL_X0 + 33, top + 9), name, font=font("Regular", 12), fill=c, anchor="lm")
            d.text((PANEL_X1, top + 9), SCALES[k], font=font("Regular", 11), fill=MUTED, anchor="rm")
            d.line([(PANEL_X0, top + LANE - 1), (PANEL_X1, top + LANE - 1)], fill=(24, 32, 28))
        # selo FAN no canto de baixo à esquerda (os valores ficam à direita, embaixo do fps)
        d.rounded_rectangle([GX0 - 1, H - 15, GX0 + 31, H - 2], radius=3, fill=(120, 138, 128))
        d.text((GX0 + 15, H - 9), "FAN", font=font("Medium", 11), fill=BLACK, anchor="mm")
        return im

    # ---------- sinais ----------
    def sample(self, data, t, dt):
        """Valor de cada canal no instante t, em -1..1 da faixa (None = sem sinal), já na escala do canal."""
        def clip(v):
            return max(-1.0, min(1.0, v))

        cpu, gpu, ram = self.fast.at(t)
        out = [None if nan(cpu) else clip(cpu / 50 - 1),       # 0 % embaixo, 100 % em cima
               None if nan(gpu) else clip(gpu / 50 - 1)]

        # RAM e temperatura variam pouco: ampliadas em volta da média recente (a média segue o valor devagar)
        k = 1 - math.exp(-dt / CENTER_TAU)
        if nan(ram):
            out.append(None)
        else:
            self.ram_center = ram if self.ram_center is None else self.ram_center + (ram - self.ram_center) * k
            out.append(clip((ram - self.ram_center) * RAM_PX_PER_PCT / AMP))
        temps = [v for v in (data["cpu_temp"], data["gpu_temp"]) if not nan(v)]
        if not temps:
            out.append(None)
        else:
            temp = max(temps)
            self.temp_center = temp if self.temp_center is None else self.temp_center + (temp - self.temp_center) * k
            out.append(clip((temp - self.temp_center) * TEMP_PX_PER_C / AMP))
        return out

    # ---------- pixels ----------
    def queue_px(self, p, color):
        old = self.pending_px.get(p)
        self.pending_px[p] = (color, old[1] if old else self.frame)

    def paint(self, color, pts):
        px = self.px
        for p in pts:
            px[p] = color
            self.queue_px(p, color)

    def erase(self, pts):
        bgpx, px = self.bgpx, self.px
        for p in pts:
            px[p] = bgpx[p]
            self.queue_px(p, bgpx[p])

    def flush(self, everything=False):
        """A ponta do feixe sai na hora; trocas de tom e apagamentos esperam até MAX_WAIT quadros para encher pacotes.
        Cada pixel guarda só a cor mais recente, então a ordem de chegada na telinha nunca deixa um tom velho por cima."""
        groups = {}
        for p, (c, f0) in self.pending_px.items():
            g = groups.setdefault(c, [[], False])
            g[0].append(p)
            if everything or c in self.heads or self.frame - f0 >= MAX_WAIT:
                g[1] = True
        for c, (pts, urgent) in groups.items():
            if not urgent and len(pts) < 28:
                continue
            for pkt, members in packets195(c, pts):
                if urgent or len(members) == 28:
                    self.lcd.WriteLine(pkt)
                    for m in members:
                        del self.pending_px[m]
        self.frame += 1

    @staticmethod
    def level(d):
        if d < DX:
            return 0
        if d < FADE_AT[0]:
            return 1
        if d < FADE_AT[1]:
            return 2
        return 3

    def sweep(self, data):
        # 1) apaga as colunas que entram na faixa à frente do feixe
        for i in range(DX):
            x = (self.beam + GAP + i) % GW
            for ch in range(len(CHANNELS)):
                col = self.pix[ch][x]
                if col:
                    self.erase([(GX0 + x, y) for y in col[0]])
                    self.pix[ch][x] = None
                    self.lvl[ch][x] = -1
        # 2) desenha as colunas novas na cor da ponta; as subamostras cobrem o tempo real desde o quadro anterior
        t_now = time.monotonic() - DELAY
        t_start = self.t_prev if self.t_prev is not None else t_now - 1.0 / FPS
        if t_now - t_start > 1.0:
            t_start = t_now - 1.0 / FPS     # depois de uma parada longa, não espreme segundos em 2 colunas
        n = DX * SUB
        dt = (t_now - t_start) / n
        self.t_prev = t_now
        for i in range(DX):
            x = (self.beam + i) % GW
            if x == 0:
                self.last_y = [None] * len(CHANNELS)
            ys = [set() for _ in CHANNELS]
            dead = [False] * len(CHANNELS)
            for s in range(SUB):
                for ch, v in enumerate(self.sample(data, t_start + dt * (i * SUB + s + 1), dt)):
                    dead[ch] = v is None
                    y = round(GY0 + LANE // 2 + LANE * ch - (0 if v is None else v) * AMP)
                    prev = self.last_y[ch] if self.last_y[ch] is not None else y
                    ys[ch].update(range(min(prev, y), max(prev, y) + 1))
                    self.last_y[ch] = y
            for ch, s in enumerate(ys):
                s |= {y + 1 for y in s}           # traço de 2 px
                base = DEAD if dead[ch] else CHANNELS[ch][1]
                self.pix[ch][x] = (sorted(s), base)
                self.lvl[ch][x] = 0
                self.paint(shades(base)[0], [(GX0 + x, y) for y in s])
        self.beam = (self.beam + DX) % GW
        # 3) rastro: colunas que cruzaram um limiar trocam de tom
        newest = self.beam - 1
        for ch in range(len(CHANNELS)):
            pix, lvl = self.pix[ch], self.lvl[ch]
            for x in range(GW):
                if pix[x] is None:
                    continue
                want = self.level((newest - x) % GW)
                if want != lvl[x]:
                    lvl[x] = want
                    ys, base = pix[x]
                    self.paint(shades(base)[want], [(GX0 + x, y) for y in ys])
        self.flush()

    # ---------- números (bitmap) ----------
    def field_list(self):
        def fit(x, y, anchor, size, weight, widest):
            l, t, r, b = font(weight, size).getbbox(widest, anchor=anchor)
            return (x + l - 2, y + t - 1, x + r + 2, y + b + 2), (x, y), anchor, size, weight

        def blink():
            return int(time.monotonic() * 2) % 2 == 0

        def big(k, widest="100%"):
            return fit(PANEL_X1, GY0 + k * LANE + 19, "ra", 26, "Medium", widest)

        def sub(k, widest):
            return fit(PANEL_X1, GY0 + k * LANE + 50, "ra", 12, "Regular", widest)

        def cpu(d):
            return f"{fmt(d['cpu'], '{:.0f}')}%", TEXT

        def cpu_sub(d):
            return f"{fmt(d['cpu_freq'] / 1000 if not nan(d['cpu_freq']) else math.nan, '{:.2f}')} GHz", MUTED

        def gpu(d):
            return f"{fmt(d['gpu3d'], '{:.0f}')}%", TEXT      # mesma fonte do traço (contador 3D do Windows)

        def gpu_sub(d):
            return f"vram {fmt(d['vram_mb'] / 1024 if not nan(d['vram_mb']) else math.nan, '{:.1f}')}G", MUTED

        def ram(d):
            return f"{fmt(d['ram'], '{:.0f}')}%", TEXT

        def ram_sub(d):
            return f"{fmt(d['ram_used'], '{:.1f}')}G em uso", MUTED

        def heat(d):
            hot = (not nan(d["cpu_temp"]) and d["cpu_temp"] >= CPU_TEMP_ALARM) or \
                  (not nan(d["gpu_temp"]) and d["gpu_temp"] >= GPU_TEMP_ALARM)
            temps = [t for t in (d["cpu_temp"], d["gpu_temp"]) if not nan(t)]
            return (f"{max(temps):.0f}°" if temps else "--°"), (RED if hot and blink() else TEXT)

        def heat_sub(d):
            return f"c{fmt(d['cpu_temp'], '{:.0f}')}° g{fmt(d['gpu_temp'], '{:.0f}')}°", MUTED

        def clock(d):
            return datetime.now().strftime("%H:%M"), TEXT

        def fps(d):
            return ("fps --" if d["fps"] < 0 else f"fps {d['fps']}"), MUTED

        def fan_rad(d):
            return f"rad {fmt(d['fan_cpu'], '{:.0f}')} rpm", TEXT

        def fan_gpu(d):
            return f"gpu {fmt(d['fan_gpu'], '{:.0f}')} rpm", TEXT

        return [
            ("cpu", *big(0), cpu), ("cpu_sub", *sub(0, "5.55 GHz"), cpu_sub),
            ("gpu", *big(1), gpu), ("gpu_sub", *sub(1, "vram 15.9G"), gpu_sub),
            ("ram", *big(2), ram), ("ram_sub", *sub(2, "31.9G em uso"), ram_sub),
            ("heat", *big(3, "100°"), heat), ("heat_sub", *sub(3, "c100° g100°"), heat_sub),
            ("clock", *fit(PANEL_X1, 9, "rm", 13, "Medium", "88:88"), clock),
            ("fps", *fit(GX0 + GW, 9, "rm", 12, "Regular", "fps 888"), fps),
            ("fan_rad", *fit(GX0 + GW // 2, H - 9, "mm", 12, "Regular", "rad 9999 rpm"), fan_rad),
            ("fan_gpu", *fit(GX0 + GW, H - 9, "rm", 12, "Regular", "gpu 9999 rpm"), fan_gpu),
        ]

    def update_field(self, data):
        key, box, origin, anchor, size, weight, fn = self.fields[self.field_i]
        self.field_i = (self.field_i + 1) % len(self.fields)
        text, color = fn(data)
        if self.shown.get(key) == (text, color):
            return
        self.shown[key] = (text, color)
        self.screen.paste(self.bg.crop(box), box[:2])
        ImageDraw.Draw(self.screen).text(origin, text, font=font(weight, size), fill=color, anchor=anchor)
        x0, y0, x1, y1 = box
        self.pending += [(x0, y, x1, min(y + SLICE_ROWS, y1)) for y in range(y0, y1, SLICE_ROWS)]

    def drain(self, budget):
        while self.pending and budget > 0:
            box = self.pending.pop(0)
            budget -= (box[2] - box[0]) * (box[3] - box[1]) * 2
            self.lcd.DisplayPILImage(self.screen.crop(box), box[0], box[1])

    # ---------- laço ----------
    def repaint(self):
        self.pending = []
        self.pending_px = {}            # o bitmap da tela inteira já leva o estado atual de todos os pixels
        self.lcd.DisplayPILImage(self.screen, 0, 0)

    def heal(self):
        lcd = self.lcd
        if getattr(lcd, "reconnected", False):
            lcd.reconnected = False
            logger.warning("osciloscopio: telinha reconectada; reconfigurando")
            time.sleep(2)
            lcd.setup()
            lcd.dropped = 0
        if getattr(lcd, "dropped", 0):
            logger.warning("osciloscopio: %d bloco(s) perdido(s) na serial; redesenhando a tela", lcd.dropped)
            lcd.dropped = 0
            self.repaint()

    def run(self, stop, seconds=None):
        self.repaint()
        t_end = None if seconds is None else time.monotonic() + seconds
        next_t = time.perf_counter()
        work, frames, report = 0.0, 0, time.monotonic() + 30
        while not stop.is_set() and (t_end is None or time.monotonic() < t_end):
            t0 = time.perf_counter()
            data = self.vitals.get()
            data["gpu3d"] = self.fast.gpu_mean()
            self.sweep(data)
            self.update_field(data)
            self.drain(FIELD_BUDGET)
            self.heal()
            work += time.perf_counter() - t0
            frames += 1
            if time.monotonic() >= report:
                logger.info("osciloscopio: %d quadros, %.0f ms por quadro (%.0f %% do tempo)",
                            frames, 1000 * work / frames, 100 * work * FPS / frames)
                work, frames, report = 0.0, 0, time.monotonic() + 300
            next_t += 1.0 / FPS
            delay = next_t - time.perf_counter()
            if delay > 0:
                time.sleep(delay)
            else:
                next_t = time.perf_counter()   # atrasou: descarta a dívida em vez de disparar quadros seguidos


class PreviewLcd:
    """Tela virtual: aplica bitmaps e decodifica os pacotes 195, para conferir o empacotamento sem a telinha."""

    def __init__(self):
        self.img = Image.new("RGB", (W, H), BLACK)
        self.packets = 0
        self.pixels = 0

    def DisplayPILImage(self, image, x=0, y=0):
        self.img.paste(image, (x, y))

    def WriteLine(self, pkt):
        assert len(pkt) == 64 and pkt[5] == 195
        ox = (pkt[0] << 2) + (pkt[1] >> 6)
        oy = ((pkt[1] & 63) << 4) + (pkt[2] >> 4)
        n = ((pkt[2] & 15) << 6) + (pkt[3] >> 2)
        v = (pkt[6] << 8) | pkt[7]
        c = ((v >> 11) << 3, ((v >> 5) & 63) << 2, (v & 31) << 3)
        for k in range(n // 2):
            self.img.putpixel((ox + pkt[8 + 2 * k], oy + pkt[9 + 2 * k]), c)
        self.packets += 1
        self.pixels += n // 2


def to565(im):
    a = np.asarray(im).astype(np.uint16)
    return (a[..., 0] >> 3, a[..., 1] >> 2, a[..., 2] >> 3)


def main():
    global FPS, DX, FIELD_BUDGET
    ap = argparse.ArgumentParser()
    ap.add_argument("--preview", type=float, help="roda sem a telinha por N segundos, salva o quadro e confere")
    ap.add_argument("--out", default="osciloscopio_preview.png")
    args = ap.parse_args()

    with open("config.yaml", encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    tuning = cfg.get("osciloscopio") or {}
    FPS = tuning.get("FPS", FPS)
    DX = tuning.get("DX", DX)
    FIELD_BUDGET = tuning.get("FIELD_BUDGET", FIELD_BUDGET)
    assert DX <= GAP, f"osciloscopio.DX precisa ser <= {GAP}"

    stop = threading.Event()
    signal.signal(signal.SIGINT, lambda *_: stop.set())
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    vitals = Vitals(presentmon=not args.preview)
    vitals.start()
    fast = FastSignals()
    fast.start()

    if args.preview:
        lcd = PreviewLcd()
        scope = Scope(lcd, vitals, fast)
        t = time.perf_counter()
        scope.run(stop, args.preview)
        scope.flush(everything=True)
        scope.drain(10 ** 9)
        dt = time.perf_counter() - t
        scope.screen.save(args.out)
        diff = sum(int((a != b).sum()) for a, b in zip(to565(scope.screen), to565(lcd.img)))
        print(f"salvo em {args.out}; {lcd.packets} pacotes 195 em {dt:.1f} s "
              f"({lcd.packets / dt:.0f}/s, limite da telinha 1000/s, {lcd.pixels / max(lcd.packets, 1):.1f} de 28 "
              f"pixels por pacote); pixels divergentes na tela virtual: {diff}")
        return

    port = cfg["config"].get("COM_PORT", "AUTO")
    logger.info("osciloscopio: iniciando na porta %s (%d fps, %d px por quadro)", port, FPS, DX)
    lcd = SafeLcd(port, cfg["display"].get("BRIGHTNESS", 25),
                  slice_max_bytes=cfg["display"].get("SERIAL_SLICE_MAX_BYTES", 9600),
                  slice_pause_ms=cfg["display"].get("SERIAL_SLICE_PAUSE_MS", 1),
                  resync_bytes=cfg["display"].get("SERIAL_RESYNC_BYTES", 307200))
    if cfg["display"].get("RESET_ON_STARTUP", True):
        lcd.Reset()
        time.sleep(2)
    lcd.setup()
    Scope(lcd, vitals, fast).run(stop)
    logger.info("osciloscopio: encerrado")


if __name__ == "__main__":
    main()
