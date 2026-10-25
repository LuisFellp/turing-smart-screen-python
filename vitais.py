"""Sinais vitais: a telinha vira um monitor cardíaco do próprio PC, com a estética do tema fade (480x320, paisagem).

  ECG    = CPU  (a frequência cardíaca sobe com a carga do processador)
  PLETH  = GPU  (a amplitude do pulso acompanha a carga da placa de vídeo)
  RESP   = RAM  (a respiração acelera com a memória ocupada)

Os traçados correm como num monitor de verdade: a cada quadro só vão as colunas novas e as que acabaram de ser apagadas,
então a animação cabe na banda da tela (rev. A: ~120-140 KB/s medidos em faixas, ~0,6 ms fixo por comando). Sensores
vêm do mesmo sensors_hwinfo.py do monitor normal; leitura ausente aparece como "--" e o traçado fica reto, nunca um
valor inventado.

Corrupção: quando a serial não aceita um bloco inteiro, a biblioteca só loga "Too fast! Slow down!" e o pedaço se
perde, deixando lixo na tela. Aqui cada perda é contada e a tela é redesenhada em fatias, e uma desconexão da telinha
(que volta em retrato) leva a reconfigurar e redesenhar.

Uso (de dentro do projeto):
  pythonw vitais.py                          # roda na telinha (porta e brilho do config.yaml)
  python vitais.py --preview 8 --out x.png   # sem telinha: roda 8 s e salva o quadro em x.png
"""
import argparse
import collections
import math
import os
import signal
import threading
import time
from datetime import datetime

import numpy as np
import psutil
import serial
import yaml
from PIL import Image, ImageDraw, ImageFilter, ImageFont

HERE = os.path.dirname(os.path.abspath(__file__))
os.chdir(HERE)  # library.log escreve log.log no diretório atual

from library.log import logger  # noqa: E402
from library.lcd.lcd_comm_rev_a import LcdCommRevA, Orientation  # noqa: E402
import library.sensors.sensors_hwinfo as hw  # noqa: E402

W, H = 480, 320
# FPS, DX e FIELD_BUDGET são padrões; a seção "vitais" do config.yaml os substitui
FPS = 15                 # quadros por segundo da varredura
DX = 4                   # colunas novas por quadro (60 px/s); WAVE_W precisa ser múltiplo de DX
GAP = 9                  # faixa apagada à frente do traçado (>= DX)
SUB = 12                 # amostras por coluna (o pico R do ECG é mais fino que uma coluna)
FIELD_BUDGET = 2600      # bytes por quadro para números e textos, depois da varredura (~20 ms)
SLICE_ROWS = 10          # altura das fatias de textos e do redesenho completo
HALO = 3                 # alcance do brilho em volta do traçado, em px
WAVE_X0, WAVE_W = 10, 324
PANEL_X0, PANEL_X1 = 350, 470
HEADER_H, FOOTER_Y = 24, 292
BANDS = {"ecg": (34, 80), "pleth": (124, 76), "resp": (212, 70)}   # topo, altura
WAVE_TOP, WAVE_BOT = 34, 282     # os três canais vão num comando só, de WAVE_TOP a WAVE_BOT

CPU_TEMP_ALARM = 90      # °C (Tctl do 9800X3D; o limite do chip é 95)
GPU_TEMP_ALARM = 100     # °C (hot spot)

ROSE, BLUE, GOLD = (255, 150, 172), (150, 172, 255), (255, 214, 150)
RED = (255, 84, 96)
TEXT, MUTED = (240, 232, 250), (176, 162, 210)
COLORS = {"ecg": ROSE, "pleth": BLUE, "resp": GOLD}

FONT_DIR = os.path.join(HERE, "res", "fonts", "maple-mono")
_fonts = {}


def font(weight, size):
    if (weight, size) not in _fonts:
        _fonts[(weight, size)] = ImageFont.truetype(os.path.join(FONT_DIR, f"MapleMono-{weight}.ttf"), size)
    return _fonts[(weight, size)]


def nan(v):
    return v is None or (isinstance(v, float) and math.isnan(v))


def fmt(v, pattern, missing="--"):
    return missing if nan(v) else pattern.format(v)


class LhmSource:
    """Sensores direto do hardware pelo LibreHardwareMonitor (external/LibreHardwareMonitor) com o driver PawnIO.
    Só funciona como administrador: sem admin a GPU ainda aparece, mas o Tctl volta 0.0 e a placa-mãe some, então
    a classe recusa abrir sem admin. Leitura ausente, ou 0 em temperatura (o valor falso do LHM sem acesso), vira NaN.
    Testado 2026-10-23 (9800X3D, RX 9070 XT, MSI X870E / Nuvoton NCT6687D-R): bate com o HWiNFO em ±0,5 °C."""

    WANTED = {
        ("Cpu", "Temperature", "Core (Tctl/Tdie)"): "cpu_temp",
        ("GpuAmd", "Temperature", "GPU Hot Spot"): "gpu_temp",
        ("GpuAmd", "Load", "GPU Core"): "gpu",
        ("GpuAmd", "SmallData", "GPU Memory Used"): "vram_mb",
        ("GpuAmd", "Fan", "GPU Fan"): "fan_gpu",
        ("SuperIO", "Fan", "CPU Fan"): "fan_cpu",       # conector "CPU Fan" da placa = ventoinhas do radiador
        ("SuperIO", "Fan", "Pump Fan #1"): "fan_pump",
    }

    def __init__(self):
        import ctypes
        if not ctypes.windll.shell32.IsUserAnAdmin():
            raise PermissionError("LHM precisa de administrador para CPU e placa-mãe")
        import clr
        lib = os.path.join(HERE, "external", "LibreHardwareMonitor")
        clr.AddReference(os.path.join(lib, "LibreHardwareMonitorLib.dll"))
        clr.AddReference(os.path.join(lib, "HidSharp.dll"))
        from LibreHardwareMonitor import Hardware
        self.computer = Hardware.Computer()
        self.computer.IsCpuEnabled = self.computer.IsGpuEnabled = self.computer.IsMotherboardEnabled = True
        self.computer.Open()
        found = self.read()
        if nan(found.get("cpu_temp")):
            self.computer.Close()
            raise OSError("LHM abriu, mas sem Tctl (driver PawnIO ausente?)")

    def read(self) -> dict:
        out = {}

        def walk(h):
            h.Update()
            for s in h.Sensors:
                key = self.WANTED.get((str(h.HardwareType), str(s.SensorType), s.Name))
                if key:
                    v = math.nan if s.Value is None else float(s.Value)
                    if str(s.SensorType) == "Temperature" and v <= 0:
                        v = math.nan
                    out[key] = v
            for sub in h.SubHardware:
                walk(sub)

        for h in self.computer.Hardware:
            walk(h)
        return out


def hwinfo_has_data() -> bool:
    """O HWiNFO está aberto? Olha o processo, não o registro: ao fechar, o HWiNFO deixa os últimos valores no Gadget
    (VSB), e lê-los mostraria um FPS congelado."""
    return any((p.info["name"] or "").lower() == "hwinfo64.exe" for p in psutil.process_iter(["name"]))


class PresentMonFps(threading.Thread):
    """FPS do app em primeiro plano pelo PresentMon (external/PresentMon, Intel, código aberto), que escuta os eventos
    ETW de cada quadro entregue, sem se injetar no jogo. Precisa de administrador (ou do grupo "Usuários de log de
    desempenho"). O PresentMon roda como processo filho dentro de um job object do Windows: se este programa morrer,
    o Windows mata o PresentMon junto.

    FPS = 1000 / média de MsBetweenPresents no último segundo, da cadeia de quadros (swap chain) mais ativa do app
    acompanhado. O app acompanhado é o que está em primeiro plano, casado por pid ou pelo nome do executável
    (navegadores Chromium entregam os quadros por um processo de GPU separado, com o mesmo nome). Se o app em primeiro
    plano não entrega quadros (ex.: o ARCTracker por cima do Arc Raiders), continua acompanhando o último app em
    primeiro plano que entregava, enquanto ele entregar. Sem nenhum dos dois, devolve -1 ("--").

    Cuidado: matar o PresentMon deixa a sessão ETW "telinha" viva no Windows; o próximo início a encerra
    (--stop_existing_session). Sessões ETW órfãs de outros programas (ex.: "HWiNFO64" depois de fechar o HWiNFO)
    fizeram o PresentMon perder todos os eventos num teste; "logman stop <nome> -ets" resolve."""

    WINDOW_S = 1.0

    def __init__(self):
        super().__init__(daemon=True)
        exes = sorted(p for p in os.listdir(os.path.join(HERE, "external", "PresentMon")) if p.endswith("-x64.exe"))
        if not exes:
            raise FileNotFoundError("PresentMon não encontrado em external/PresentMon")
        self.exe = os.path.join(HERE, "external", "PresentMon", exes[-1])
        self.lock = threading.Lock()
        self.frames = collections.defaultdict(lambda: collections.deque(maxlen=2000))  # (pid, app, swapchain) -> (t, ms)
        self.sticky = None      # (pid, nome) do último app em primeiro plano que entregava quadros

    def _start(self):
        import subprocess
        import win32api
        import win32con
        import win32job
        proc = subprocess.Popen(
            [self.exe, "--output_stdout", "--no_console_stats", "--session_name", "telinha",
             "--stop_existing_session", "--no_track_input", "--no_track_gpu", "--no_track_display"],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, creationflags=subprocess.CREATE_NO_WINDOW)
        self.job = win32job.CreateJobObject(None, "")
        info = win32job.QueryInformationJobObject(self.job, win32job.JobObjectExtendedLimitInformation)
        info["BasicLimitInformation"]["LimitFlags"] |= win32job.JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        win32job.SetInformationJobObject(self.job, win32job.JobObjectExtendedLimitInformation, info)
        handle = win32api.OpenProcess(win32con.PROCESS_SET_QUOTA | win32con.PROCESS_TERMINATE, False, proc.pid)
        win32job.AssignProcessToJobObject(self.job, handle)
        return proc

    def run(self):
        import io
        while True:
            proc = None
            try:
                proc = self._start()
                logger.info("fps: PresentMon iniciado (%s)", os.path.basename(self.exe))
                # Num pipe o CSV vem em ASCII; redirecionado pelo console vem em UTF-16 com BOM. Aqui é sempre pipe,
                # mas o BOM, se aparecer, é descartado.
                lines = io.TextIOWrapper(proc.stdout, encoding="utf-8", errors="replace", newline="")
                cols = {name.strip().lstrip("﻿"): i for i, name in enumerate(lines.readline().split(","))}
                i_app, i_pid, i_sc = cols["Application"], cols["ProcessID"], cols["SwapChainAddress"]
                i_ms = cols["MsBetweenPresents"]
                for line in lines:
                    f = line.split(",")
                    if len(f) <= i_ms or f[i_ms].strip() in ("", "NA"):
                        continue
                    key = (int(f[i_pid]), f[i_app].strip().lower(), f[i_sc])
                    with self.lock:
                        self.frames[key].append((time.monotonic(), float(f[i_ms])))
                logger.warning("fps: PresentMon encerrou (código %s); reiniciando em 5 s", proc.wait())
            except Exception:
                logger.exception("fps: falha no PresentMon; reiniciando em 5 s")
            if proc and proc.poll() is None:
                proc.kill()     # não deixar um PresentMon antigo rodando ao lado do novo
            time.sleep(5)

    @staticmethod
    def foreground_pid() -> int:
        import ctypes
        from ctypes import wintypes
        hwnd = ctypes.windll.user32.GetForegroundWindow()
        pid = wintypes.DWORD()
        ctypes.windll.user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        return pid.value

    def fps(self) -> int:
        pid = self.foreground_pid()
        try:
            name = psutil.Process(pid).name().lower()
        except (psutil.Error, ValueError):
            name = None
        cutoff = time.monotonic() - self.WINDOW_S
        fg_best, sticky_best = [], []
        with self.lock:
            for key in list(self.frames):
                q = self.frames[key]
                while q and q[0][0] < cutoff:
                    q.popleft()
                if not q:
                    del self.frames[key]
                    continue
                if (key[0] == pid or key[1] == name) and len(q) > len(fg_best):
                    fg_best = list(q)
                if self.sticky and (key[0] == self.sticky[0] or key[1] == self.sticky[1]) and len(q) > len(sticky_best):
                    sticky_best = list(q)
        if len(fg_best) >= 2:
            self.sticky = (pid, name)
            best = fg_best
        else:
            best = sticky_best
        if len(best) < 2:
            return -1
        return int(round(1000.0 / (sum(ms for _, ms in best) / len(best))))


class Vitals(threading.Thread):
    """Lê os sensores uma vez por segundo, fora do laço de desenho. Fonte: LibreHardwareMonitor quando o processo é
    administrador; senão, HWiNFO (cuja 1ª leitura pode esperar até 90 s). FPS: PresentMon (admin); sem admin, o do
    HWiNFO/RTSS se o HWiNFO estiver aberto; senão "--"."""

    def __init__(self, presentmon=True):
        super().__init__(daemon=True)
        self.presentmon = presentmon    # False no preview: não abrir uma 2ª sessão ETW "telinha" ao lado do monitor
        self.lock = threading.Lock()
        self.data = dict(cpu=math.nan, cpu_freq=math.nan, cpu_temp=math.nan, gpu=math.nan, gpu_temp=math.nan, vram_mb=math.nan,
                         ram=math.nan, ram_used=math.nan, ram_total=math.nan, fps=-1, fan_cpu=math.nan,
                         fan_gpu=math.nan, fan_pump=math.nan)

    def get(self):
        with self.lock:
            return dict(self.data)

    def run(self):
        try:
            lhm = LhmSource()
            hw._startup_waited = True     # FPS pelo HWiNFO é opcional: não esperar 90 s por ele
            logger.info("sensores: LibreHardwareMonitor (admin)")
        except Exception as e:
            lhm = None
            logger.warning("sensores: LibreHardwareMonitor indisponível (%s); usando HWiNFO", e)
        pm = None
        if lhm and self.presentmon:   # mesmo requisito do PresentMon: administrador
            try:
                pm = PresentMonFps()
                pm.start()
            except Exception as e:
                logger.warning("fps: PresentMon indisponível (%s)", e)
        psutil.cpu_percent(None)
        while True:
            try:
                d = {"cpu": psutil.cpu_percent(None), "cpu_freq": hw.Cpu.frequency()}
                if lhm:
                    d.update(lhm.read())
                    d["fps"] = pm.fps() if pm else (hw.Gpu.fps() if hwinfo_has_data() else -1)
                elif hwinfo_has_data():
                    load, _, used_mb, _, hotspot = hw.Gpu.stats()
                    d.update(cpu_temp=hw.Cpu.temperature(), gpu=load, gpu_temp=hotspot, vram_mb=used_mb,
                             fps=hw.Gpu.fps(), fan_cpu=hw.get_value("cpu_fan_rpm"),
                             fan_gpu=hw.get_value("gpu_fan_rpm"))
                else:
                    # Nem LHM (sem admin) nem HWiNFO aberto: o registro do HWiNFO ainda guarda os últimos valores de
                    # quando ele fechou, e lê-los mostraria temperaturas congeladas. Tudo que vem dele fica "--".
                    d.update(cpu_temp=math.nan, gpu=math.nan, gpu_temp=math.nan, vram_mb=math.nan, fps=-1,
                             fan_cpu=math.nan, fan_gpu=math.nan)
                mem = psutil.virtual_memory()
                d.update(ram=mem.percent, ram_used=(mem.total - mem.available) / 1024 ** 3, ram_total=mem.total / 1024 ** 3)
                with self.lock:
                    self.data.update(d)
            except Exception:
                logger.exception("vitais: falha ao ler sensores")
            time.sleep(1)


def ecg(phase):
    """Complexo PQRST como soma de gaussianas; fase 0..1 = um batimento."""
    v = 0.0
    for center, amp, width in ((0.10, 0.12, 0.025), (0.20, -0.10, 0.010), (0.225, 1.0, 0.011),
                               (0.25, -0.26, 0.011), (0.46, 0.30, 0.040)):
        v += amp * math.exp(-((phase - center) / width) ** 2)
    return v


def pleth(phase):
    """Onda de pulso: subida rápida, descida lenta e incisura dicrótica. Atrasada em relação ao ECG, como no corpo."""
    p = (phase - 0.30) % 1.0
    if p < 0.16:
        return math.sin(math.pi / 2 * p / 0.16) ** 2
    return math.exp(-(p - 0.16) * 3.4) + 0.10 * math.exp(-((p - 0.42) / 0.05) ** 2)


def haze():
    """Fundo do tema fade: entardecer índigo → rosa-poeira, névoa à direita, bokeh discreto e grão de filme."""
    rng = np.random.default_rng(5)
    ys, xs = np.mgrid[0:H, 0:W].astype(np.float32)
    t = ys / H
    stops = [(0.0, (14, 12, 38)), (0.45, (40, 27, 78)), (0.80, (92, 48, 106)), (1.0, (160, 82, 116))]
    img = np.zeros((H, W, 3), np.float32)
    for (t0, c0), (t1, c1) in zip(stops, stops[1:]):
        m = (t >= t0) & (t <= t1)
        k = ((t - t0) / (t1 - t0))[m][:, None]
        img[m] = np.array(c0) * (1 - k) + np.array(c1) * k
    d = np.hypot((xs - 430) / 1.4, ys - 110)
    img += (np.clip(1 - d / 230, 0, 1) ** 2)[..., None] * np.array([40, 22, 36])
    vig = np.clip(1 - np.hypot((xs - W / 2) / (W * 0.75), (ys - H / 2) / (H * 0.8)), 0.55, 1)
    im = Image.fromarray(np.clip(img * vig[..., None], 0, 255).astype(np.uint8), "RGB").convert("RGBA")

    bokeh = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    bd = ImageDraw.Draw(bokeh)
    for _ in range(22):
        r = int(rng.integers(8, 32))
        x, y = int(rng.integers(0, W)), int(rng.integers(0, H))
        c = [ROSE, BLUE, GOLD, (200, 140, 255)][int(rng.integers(0, 4))]
        bd.ellipse([x - r, y - r, x + r, y + r], fill=c + (int(rng.integers(10, 24)),))
    im = Image.alpha_composite(im, bokeh.filter(ImageFilter.GaussianBlur(4))).convert("RGB")
    arr = np.asarray(im).astype(np.float32) + rng.normal(0, 3.0, (H, W, 1))
    return Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8), "RGB")


class Monitor:
    def __init__(self, lcd, vitals: Vitals):
        self.lcd = lcd
        self.vitals = vitals
        self.bg = self.background()
        self.screen = self.bg.copy()
        self.draw = ImageDraw.Draw(self.screen)
        self.col = 0
        self.dt_col = 1.0 / (FPS * DX)
        self.heart = 0.0
        self.breath = 0.0
        self.bpm = 60.0
        self.last = {k: None for k in BANDS}
        self.beat_at = -1.0
        self.heart_on = None
        self.shown = {}
        self.fields = self.field_list()
        self.field_i = 0
        self.pending = collections.deque()   # fatias de texto esperando orçamento de banda

    # ---------- fundo fixo ----------
    def background(self):
        im = haze()
        d = ImageDraw.Draw(im, "RGBA")
        d.text((14, 5), "luispc", font=font("Italic", 13), fill=MUTED)
        labels = {"ecg": "cpu · bpm", "pleth": "gpu · %", "resp": "ram · %"}
        for k, (top, h) in BANDS.items():
            c = COLORS[k]
            d.ellipse([PANEL_X0, top - 1, PANEL_X0 + 7, top + 6], fill=c)
            d.text((PANEL_X0 + 13, top - 5), labels[k], font=font("Italic", 13), fill=c)
            # linha de base pontilhada de cada canal
            base = round(self.to_y(k, 0.0))
            for x in range(WAVE_X0, WAVE_X0 + WAVE_W, 6):
                d.point((x, base), fill=(255, 255, 255, 40))
        d.text((14, FOOTER_Y + 7), "ventoinhas", font=font("Italic", 12), fill=MUTED)
        return im

    # ---------- envio ----------
    def send(self, box):
        x0, y0, x1, y1 = box
        self.lcd.DisplayPILImage(self.screen.crop(box), x0, y0)

    def restore(self, box):
        self.screen.paste(self.bg.crop(box), box[:2])

    def wave_cols(self, start, n, fn):
        """Aplica fn(x0, x1) às colunas [start, start+n) da área dos traçados, quebrando na volta para a esquerda."""
        start %= WAVE_W
        first = min(n, WAVE_W - start)
        fn(WAVE_X0 + start, WAVE_X0 + start + first)
        if first < n:
            fn(WAVE_X0, WAVE_X0 + n - first)

    def queue(self, box):
        x0, y0, x1, y1 = box
        for y in range(y0, y1, SLICE_ROWS):
            self.pending.append((x0, y, x1, min(y + SLICE_ROWS, y1)))

    def drain(self, budget):
        while self.pending and budget > 0:
            box = self.pending.popleft()
            budget -= (box[2] - box[0]) * (box[3] - box[1]) * 2
            self.send(box)

    def repaint(self):
        """Tela inteira; a biblioteca a envia em fatias com pausa (SERIAL_SLICE_* do config.yaml)."""
        self.pending.clear()
        self.send((0, 0, W, H))

    # ---------- traçados ----------
    def sample(self, data):
        """Avança o tempo de uma subamostra e devolve o valor normalizado de cada canal (None = sem sinal)."""
        dt = self.dt_col / SUB
        cpu = data["cpu"]
        target = 55 + 1.25 * (0 if nan(cpu) else cpu)
        self.bpm += (target - self.bpm) * 0.002
        before = self.heart
        self.heart = (self.heart + dt * self.bpm / 60) % 1.0
        if before < 0.225 <= self.heart:
            self.beat_at = time.monotonic()
        ram = data["ram"]
        rate = 10 + (0 if nan(ram) else ram) / 8      # respirações por minuto
        self.breath = (self.breath + dt * rate / 60) % 1.0
        gpu = data["gpu"]
        return {
            "ecg": ecg(self.heart),
            "pleth": None if nan(gpu) else pleth(self.heart) * (0.12 + 0.88 * gpu / 100),
            "resp": 0.5 - 0.5 * math.cos(2 * math.pi * self.breath),
        }

    @staticmethod
    def to_y(kind, v):
        top, h = BANDS[kind]
        if kind == "ecg":
            return top + 0.68 * h - v * 0.58 * h
        if kind == "pleth":
            return top + 0.90 * h - (0 if v is None else v) * 0.80 * h
        return top + 0.86 * h - v * 0.70 * h

    def sweep(self, data):
        x_new = WAVE_X0 + self.col
        # as colunas novas já foram apagadas quando eram a faixa à frente; agora entra na faixa só o que está GAP adiante
        self.wave_cols(self.col + GAP, DX, lambda x0, x1: self.restore((x0, WAVE_TOP, x1, WAVE_BOT)))
        if self.col == 0:
            self.last = {k: None for k in BANDS}
        pts = {k: [] for k in BANDS}
        vals = {}
        for i in range(DX):
            for s in range(SUB):
                vals = self.sample(data)
                x = x_new + i + s / SUB
                for k, v in vals.items():
                    pts[k].append((x, self.to_y(k, v)))
        for k, p in pts.items():
            top, h = BANDS[k]
            line = ([self.last[k]] if self.last[k] else []) + p
            silent = k == "pleth" and vals["pleth"] is None
            color = MUTED if silent else COLORS[k]
            if not silent:
                # brilho: traço largo desfocado numa máscara, aplicado só nas colunas novas (as anteriores já têm)
                ox = x_new - HALO - 1
                mask = Image.new("L", (DX + 2 * HALO + 2, h), 0)
                ImageDraw.Draw(mask).line([(x - ox, y - top) for x, y in line], fill=150, width=2 * HALO + 1)
                mask = mask.filter(ImageFilter.GaussianBlur(HALO / 1.5)).crop((HALO + 1, 0, HALO + 1 + DX, h))
                self.screen.paste(Image.new("RGB", mask.size, color), (x_new, top), mask)
            self.draw.line(line, fill=color, width=2, joint="curve")
            self.last[k] = p[-1]
        # envia as colunas novas (+1 à esquerda: borda da linha de 2 px do quadro anterior) e as recém-apagadas,
        # cada grupo com os três canais num comando só
        send = lambda x0, x1: self.send((x0, WAVE_TOP, x1, WAVE_BOT))  # noqa: E731
        self.wave_cols(self.col - 1, DX + 1, send)
        self.wave_cols(self.col + GAP, DX, send)
        self.col = (self.col + DX) % WAVE_W

    # ---------- coração ----------
    def heart_icon(self):
        on = time.monotonic() - self.beat_at < 0.18
        if on == self.heart_on:
            return
        self.heart_on = on
        x, y = PANEL_X1 - 13, BANDS["ecg"][0] - 4
        box = (x, y, x + 14, y + 13)
        self.restore(box)
        d = ImageDraw.Draw(self.screen, "RGBA")
        c = ROSE + ((255,) if on else (70,))
        d.ellipse([x, y, x + 7, y + 7], fill=c)
        d.ellipse([x + 6, y, x + 13, y + 7], fill=c)
        d.polygon([(x + 0.5, y + 4.5), (x + 12.5, y + 4.5), (x + 6.5, y + 12)], fill=c)
        self.send(box)

    # ---------- números ----------
    def field_list(self):
        def fit(x, y, anchor, size, widest):
            """Caixa justa em volta do maior texto possível do campo: só ela é apagada e enviada a cada mudança."""
            l, t, r, b = font("Light" if size >= 40 else "Regular", size).getbbox(widest, anchor=anchor)
            return (x + l - 2, y + t - 1, x + r + 2, y + b + 2), (x, y)

        def big(kind):
            return fit(PANEL_X1, BANDS[kind][0] + 10, "ra", 44, "188")

        def sub(kind, widest):
            return fit(PANEL_X1, BANDS[kind][0] + 58, "ra", 12, widest)

        def blink():
            return int(time.monotonic() * 2) % 2 == 0

        def hr(d):
            hot = not nan(d["cpu_temp"]) and d["cpu_temp"] >= CPU_TEMP_ALARM
            return f"{self.bpm:.0f}", (RED if hot and blink() else TEXT), 44

        def hr_sub(d):
            return f"{fmt(d['cpu'], '{:.0f}')}% · {fmt(d['cpu_temp'], '{:.0f}')}°C", MUTED, 12

        def gpu(d):
            hot = not nan(d["gpu_temp"]) and d["gpu_temp"] >= GPU_TEMP_ALARM
            return fmt(d["gpu"], "{:.0f}"), (RED if hot and blink() else TEXT), 44

        def gpu_sub(d):
            vram = fmt(d["vram_mb"] / 1024 if not nan(d["vram_mb"]) else math.nan, "{:.1f}")
            return f"{fmt(d['gpu_temp'], '{:.0f}')}°C · {vram}G", MUTED, 12

        def ram(d):
            return fmt(d["ram"], "{:.0f}"), TEXT, 44

        def ram_sub(d):
            return f"{fmt(d['ram_used'], '{:.1f}')}/{fmt(d['ram_total'], '{:.0f}')}G", MUTED, 12

        def clock(d):
            return datetime.now().strftime("%H:%M"), TEXT, 14

        def fps(d):
            f = d["fps"]
            return ("fps --" if f < 0 else f"fps {f}"), MUTED, 13

        def alarm(d):
            msgs = []
            if not nan(d["cpu_temp"]) and d["cpu_temp"] >= CPU_TEMP_ALARM:
                msgs.append(f"cpu {d['cpu_temp']:.0f}°C")
            if not nan(d["gpu_temp"]) and d["gpu_temp"] >= GPU_TEMP_ALARM:
                msgs.append(f"gpu {d['gpu_temp']:.0f}°C")
            if nan(d["gpu"]):
                msgs.append("gpu sem sinal")
            if not msgs:
                return "", TEXT, 12
            return ("! " + " · ".join(msgs)) if blink() else "", RED, 12

        def fans(d):
            return f"rad {fmt(d['fan_cpu'], '{:.0f}')} · gpu {fmt(d['fan_gpu'], '{:.0f}')} rpm", TEXT, 12

        def fixed(box, anchor):
            return box, ((box[2] - 1) if anchor.startswith("r") else box[0], box[1])

        return [
            ("hr", *big("ecg"), "ra", hr), ("hr_sub", *sub("ecg", "100% · 100°C"), "ra", hr_sub),
            ("gpu", *big("pleth"), "ra", gpu), ("gpu_sub", *sub("pleth", "100°C · 16.0G"), "ra", gpu_sub),
            ("ram", *big("resp"), "ra", ram), ("ram_sub", *sub("resp", "31.9/31G"), "ra", ram_sub),
            ("clock", *fixed((420, 4, W - 8, HEADER_H - 2), "ra"), "ra", clock),
            ("fps", *fixed((330, 5, 412, HEADER_H - 2), "ra"), "ra", fps),
            ("alarm", *fixed((120, 5, 326, HEADER_H - 2), "la"), "la", alarm),
            ("fans", *fit(100, FOOTER_Y + 6, "la", 12, "rad 9999 · gpu 9999 rpm"), "la", fans),
        ]

    def update_field(self, data):
        """Um campo por quadro, em rodízio; só redesenha quando o texto ou a cor mudou, e o envio vai para a fila."""
        key, box, origin, anchor, fn = self.fields[self.field_i]
        self.field_i = (self.field_i + 1) % len(self.fields)
        text, color, size = fn(data)
        if self.shown.get(key) == (text, color):
            return
        self.shown[key] = (text, color)
        self.restore(box)
        self.draw.text(origin, text, font=font("Light" if size >= 40 else "Regular", size), fill=color, anchor=anchor)
        self.queue(box)

    # ---------- laço ----------
    def heal(self):
        """Se a serial perdeu algum bloco (ou a telinha caiu e voltou), reconfigura se preciso e redesenha tudo."""
        lcd = self.lcd
        if getattr(lcd, "reconnected", False):
            lcd.reconnected = False
            logger.warning("vitais: telinha reconectada; reconfigurando")
            time.sleep(2)
            lcd.setup()
            lcd.dropped = 0
        if getattr(lcd, "dropped", 0):
            logger.warning("vitais: %d bloco(s) perdido(s) na serial; redesenhando a tela", lcd.dropped)
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
            self.sweep(data)
            self.heart_icon()
            self.update_field(data)
            self.drain(FIELD_BUDGET)
            self.heal()
            work += time.perf_counter() - t0
            frames += 1
            if time.monotonic() >= report:
                # perto de 100 % a tela não dá conta da animação: reduzir FPS, DX ou FIELD_BUDGET
                logger.info("vitais: %d quadros, %.0f ms por quadro (%.0f %% do tempo)",
                            frames, 1000 * work / frames, 100 * work * FPS / frames)
                work, frames, report = 0.0, 0, time.monotonic() + 300
            next_t += 1.0 / FPS
            delay = next_t - time.perf_counter()
            if delay > 0:
                time.sleep(delay)
            else:
                # quadro atrasado (envio maior que o normal ou o Windows segurou o processo): descarta a dívida em
                # vez de disparar quadros seguidos. O tempo das ondas conta por coluna, então só fica mais lento.
                next_t = time.perf_counter()


class NullLcd:
    def DisplayPILImage(self, image, x=0, y=0):
        pass


class SafeLcd(LcdCommRevA):
    """Rev. A que conta os blocos perdidos em vez de só logar, e não reinicia o programa quando a porta cai."""

    def __init__(self, port, brightness, **slices):
        self.dropped = 0
        self.reconnected = False
        self.brightness = brightness
        # sem fila: envio síncrono
        super().__init__(com_port=port, display_width=320, display_height=480, **slices)

    def setup(self):
        self.InitializeComm()
        self.SetBrightness(level=self.brightness)
        self.SetOrientation(orientation=Orientation.LANDSCAPE)

    def WriteLine(self, line: bytes):
        try:
            self.serial_write(line)
        except serial.SerialTimeoutException:
            self.dropped += 1
            time.sleep(0.05)
        except serial.SerialException:
            logger.error("vitais: a telinha caiu; reabrindo a porta")
            self.dropped += 1
            self.reconnected = True
            self.closeSerial()
            time.sleep(1)
            self.openSerial()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--preview", type=float, help="roda sem a telinha por N segundos e salva o quadro")
    ap.add_argument("--out", default="vitais_preview.png")
    args = ap.parse_args()

    global FPS, DX, FIELD_BUDGET
    with open("config.yaml", encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    tuning = cfg.get("vitais") or {}
    FPS = tuning.get("FPS", FPS)
    DX = tuning.get("DX", DX)
    FIELD_BUDGET = tuning.get("FIELD_BUDGET", FIELD_BUDGET)
    assert WAVE_W % DX == 0 and DX <= GAP, f"vitais.DX precisa dividir {WAVE_W} e ser <= {GAP}"

    stop = threading.Event()
    signal.signal(signal.SIGINT, lambda *_: stop.set())
    signal.signal(signal.SIGTERM, lambda *_: stop.set())

    vitals = Vitals(presentmon=not args.preview)
    vitals.start()

    if args.preview:
        mon = Monitor(NullLcd(), vitals)
        mon.run(stop, args.preview)
        mon.screen.save(args.out)
        print("salvo em", args.out)
        return

    port = cfg["config"].get("COM_PORT", "AUTO")
    brightness = cfg["display"].get("BRIGHTNESS", 25)

    logger.info("vitais: iniciando na porta %s (%d fps, %d px por quadro)", port, FPS, DX)
    lcd = SafeLcd(port, brightness,
                  slice_max_bytes=cfg["display"].get("SERIAL_SLICE_MAX_BYTES", 9600),
                  slice_pause_ms=cfg["display"].get("SERIAL_SLICE_PAUSE_MS", 1),
                  resync_bytes=cfg["display"].get("SERIAL_RESYNC_BYTES", 307200))
    if cfg["display"].get("RESET_ON_STARTUP", True):
        lcd.Reset()
        time.sleep(2)   # logo depois do reset a telinha ainda perde blocos grandes
    lcd.setup()
    Monitor(lcd, vitals).run(stop)
    logger.info("vitais: encerrado")


if __name__ == "__main__":
    main()
