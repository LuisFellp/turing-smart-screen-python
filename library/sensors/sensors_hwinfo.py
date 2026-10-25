# SPDX-License-Identifier: GPL-3.0-or-later
#
# turing-smart-screen-python - a Python system monitor and library for USB-C displays like Turing Smart Screen or XuanFang
# https://github.com/mathoudebine/turing-smart-screen-python/
#
# Copyright (C) 2021 Matthieu Houdebine (mathoudebine)
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <https://www.gnu.org/licenses/>.

# LOCAL ADDITION (not in upstream): reads hardware sensors from HWiNFO's "Gadget" registry interface
# (HKCU\Software\HWiNFO64\VSB) instead of polling the hardware a second time with LibreHardwareMonitor.
# HWiNFO must be running with "Report value in Gadget" enabled for the readings listed in VSB_INDEX below.
# Readings that come from Windows itself (CPU %, RAM, disk, network) are read with psutil.
# Windows only. Does not need administrator rights.

import collections
import math
import threading
import time
import winreg
from typing import Tuple

import psutil

import library.sensors.sensors as sensors
from library.log import logger

# Position of each reading in HWiNFO's Gadget list ("VSBidx" set in HWiNFO sensor settings).
# Labels are localized by HWiNFO, so readings are matched by index, not by name.
VSB_INDEX = {
    "cpu_temp": 0,      # CPU (Tctl/Tdie)
    "gpu_temp": 1,      # GPU Temperature (edge)
    "gpu_hotspot": 2,   # GPU Hot Spot Temperature
    "gpu_load": 3,      # GPU Utilization
    "gpu_mem_used": 4,  # GPU D3D Memory Dedicated (MB)
    "gpu_fan": 5,       # GPU Fan PWM (%)
    "cpu_clock": 6,     # Average Effective Clock (MHz)
    "cpu_fan_rpm": 7,   # Motherboard "CPU" fan (RPM)
    "fps": 8,           # RTSS Framerate
    "gpu_fan_rpm": 9,   # GPU Fan (RPM)
}

_VSB_KEY = r"Software\HWiNFO64\VSB"
_CACHE_SECONDS = 0.25
_lock = threading.Lock()
_cache = {}
_cache_time = 0.0
_warned = False
_labels = {}
# The monitor can start a few seconds before HWiNFO publishes its Gadget data; stats.py hides a field for good
# when it first reads NaN, so the first read waits (up to this long) for HWiNFO instead of returning nothing.
_STARTUP_WAIT_SECONDS = 90
_startup_waited = False


def _read_vsb() -> dict:
    """Read every ValueRawN from HWiNFO's Gadget key, cached briefly because several scheduler threads read at once."""
    global _cache, _cache_time, _warned, _startup_waited
    with _lock:
        if not _startup_waited:
            _startup_waited = True
            deadline = time.monotonic() + _STARTUP_WAIT_SECONDS
            while time.monotonic() < deadline:
                try:
                    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _VSB_KEY) as key:
                        if winreg.QueryInfoKey(key)[1] > 0:
                            break
                except OSError:
                    pass
                time.sleep(1)
        now = time.monotonic()
        if now - _cache_time < _CACHE_SECONDS:
            return _cache
        values = {}
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _VSB_KEY) as key:
                i = 0
                while True:
                    try:
                        name, data, _ = winreg.EnumValue(key, i)
                    except OSError:
                        break
                    i += 1
                    if name.startswith("Label"):
                        try:
                            _labels[int(name[5:])] = str(data)
                        except ValueError:
                            pass
                    elif name.startswith("ValueRaw"):
                        try:
                            values[int(name[8:])] = float(str(data).replace(",", "."))
                        except ValueError:
                            pass
            _warned = False
        except OSError:
            if not _warned:
                logger.warning("HWiNFO Gadget data not found: is HWiNFO running with Gadget reporting enabled?")
                _warned = True
        _cache = values
        _cache_time = now
        return _cache


def _label_is_hotspot() -> bool:
    label = _labels.get(VSB_INDEX["gpu_hotspot"], "").lower().replace(" ", "").replace("-", "")
    return "hotspot" in label or "junction" in label


def get_value(name: str) -> float:
    return _read_vsb().get(VSB_INDEX[name], math.nan)


_pdh_query = None
_pdh_counter = None


def _gpu_dedicated_used_mb() -> float:
    """Dedicated VRAM in use, from the Windows "GPU Adapter Memory" counter (same source as Task Manager)."""
    global _pdh_query, _pdh_counter
    try:
        import win32pdh
        with _lock:
            if _pdh_query is None:
                _pdh_query = win32pdh.OpenQuery()
                _pdh_counter = win32pdh.AddEnglishCounter(_pdh_query, r"\GPU Adapter Memory(*)\Dedicated Usage")
            win32pdh.CollectQueryData(_pdh_query)
            items = win32pdh.GetFormattedCounterArray(_pdh_counter, win32pdh.PDH_FMT_LARGE)
        # One instance per adapter: the discrete GPU is the one using the most dedicated memory
        return max(items.values()) / (1024 * 1024) if items else math.nan
    except Exception:
        return math.nan


_cpu_freq_query = None
_cpu_freq_counters = None


def _cpu_actual_frequency_mhz() -> float:
    """Average actual CPU clock across cores, as shown in Task Manager ("Speed"):
    base frequency x current "% Processor Performance" (which goes above 100% with boost)."""
    global _cpu_freq_query, _cpu_freq_counters
    try:
        import win32pdh
        with _lock:
            if _cpu_freq_query is None:
                _cpu_freq_query = win32pdh.OpenQuery()
                _cpu_freq_counters = (
                    win32pdh.AddEnglishCounter(_cpu_freq_query, r"\Processor Information(_Total)\Processor Frequency"),
                    win32pdh.AddEnglishCounter(_cpu_freq_query,
                                               r"\Processor Information(_Total)\% Processor Performance"))
                # "% Processor Performance" is a rate counter: it needs two samples
                win32pdh.CollectQueryData(_cpu_freq_query)
                time.sleep(0.1)
            win32pdh.CollectQueryData(_cpu_freq_query)
            _, base = win32pdh.GetFormattedCounterValue(_cpu_freq_counters[0], win32pdh.PDH_FMT_DOUBLE)
            _, perf = win32pdh.GetFormattedCounterValue(_cpu_freq_counters[1], win32pdh.PDH_FMT_DOUBLE)
        return base * perf / 100.0
    except Exception:
        return math.nan


def _gpu_total_mem_mb() -> float:
    # Total VRAM is static: read it once from the display driver's registry key (first adapter with a size).
    base = r"SYSTEM\CurrentControlSet\Control\Class\{4d36e968-e325-11ce-bfc1-08002be10318}"
    best = 0
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, base) as cls:
            i = 0
            while True:
                try:
                    sub = winreg.EnumKey(cls, i)
                except OSError:
                    break
                i += 1
                try:
                    with winreg.OpenKey(cls, sub) as adapter:
                        size, _ = winreg.QueryValueEx(adapter, "HardwareInformation.qwMemorySize")
                        if isinstance(size, bytes):
                            size = int.from_bytes(size, "little")
                        best = max(best, int(size))
                except OSError:
                    pass
    except OSError:
        pass
    return best / (1024 * 1024) if best else math.nan


class Cpu(sensors.Cpu):
    @staticmethod
    def percentage(interval: float) -> float:
        try:
            return psutil.cpu_percent(interval=interval)
        except:
            return math.nan

    @staticmethod
    def frequency() -> float:
        # Average core clock as in Task Manager; HWiNFO's "Average Effective Clock" is the fallback
        freq = _cpu_actual_frequency_mhz()
        return freq if not math.isnan(freq) and freq > 0 else get_value("cpu_clock")

    @staticmethod
    def load() -> Tuple[float, float, float]:  # 1 / 5 / 15min avg (%):
        try:
            return psutil.getloadavg()
        except:
            return math.nan, math.nan, math.nan

    @staticmethod
    def temperature() -> float:
        return get_value("cpu_temp")

    @staticmethod
    def fan_percent(fan_name: str = None) -> float:
        # The motherboard only reports the CPU fan in RPM (see CPU_FAN_RPM in sensors_custom.py)
        return math.nan


class Gpu(sensors.Gpu):
    total_mem_mb = math.nan

    @classmethod
    def stats(cls) -> Tuple[
        float, float, float, float, float]:  # load (%) / used mem (%) / used mem (Mb) / total mem (Mb) / temp (°C)
        if math.isnan(cls.total_mem_mb):
            cls.total_mem_mb = _gpu_total_mem_mb()
        load = get_value("gpu_load")
        used_mem = _gpu_dedicated_used_mb()
        if math.isnan(used_mem) or used_mem <= 0:
            used_mem = get_value("gpu_mem_used")
        try:
            used_pct = used_mem / cls.total_mem_mb * 100.0
        except ZeroDivisionError:
            used_pct = math.nan
        # Hot spot (junction) temperature is shown as "the" GPU temperature. No fallback to the edge temperature:
        # if the hot spot is missing or the HWiNFO index no longer points at it, report NaN rather than a wrong value.
        temp = get_value("gpu_hotspot")
        if not math.isnan(temp) and not _label_is_hotspot():
            logger.warning("HWiNFO Gadget index %d is not 'GPU Hot Spot' (label: %r); check VSB_INDEX",
                           VSB_INDEX["gpu_hotspot"], _labels.get(VSB_INDEX["gpu_hotspot"]))
            temp = math.nan
        return load, used_pct, used_mem, cls.total_mem_mb, temp

    # Recent FPS readings, for a moving average (the theme refreshes FPS every 2 s, so 5 readings ~= 10 s)
    _fps_history = collections.deque(maxlen=5)

    @classmethod
    def fps(cls) -> int:
        fps = get_value("fps")
        if math.isnan(fps):
            cls._fps_history.clear()
            return -1
        if fps <= 0:
            # No 3D app running: reset the average so a new game does not start from old values
            cls._fps_history.clear()
            return 0
        cls._fps_history.append(fps)
        return int(round(sum(cls._fps_history) / len(cls._fps_history)))

    @staticmethod
    def fan_percent() -> float:
        return get_value("gpu_fan")

    @staticmethod
    def frequency() -> float:
        # GPU clock is not reported to the Gadget (not used by the theme)
        return math.nan

    @staticmethod
    def is_available() -> bool:
        return not math.isnan(get_value("gpu_load"))


class Memory(sensors.Memory):
    @staticmethod
    def swap_percent() -> float:
        return psutil.swap_memory().percent

    @staticmethod
    def virtual_percent() -> float:
        return psutil.virtual_memory().percent

    @staticmethod
    def virtual_used() -> int:  # In bytes
        # Do not use psutil.virtual_memory().used: from https://psutil.readthedocs.io/en/latest/#memory
        # "It is calculated differently depending on the platform and designed for informational purposes only"
        return psutil.virtual_memory().total - psutil.virtual_memory().available

    @staticmethod
    def virtual_free() -> int:  # In bytes
        return psutil.virtual_memory().available


class Disk(sensors.Disk):
    @staticmethod
    def disk_usage_percent() -> float:
        return psutil.disk_usage("/").percent

    @staticmethod
    def disk_used() -> int:  # In bytes
        return psutil.disk_usage("/").used

    @staticmethod
    def disk_free() -> int:  # In bytes
        return psutil.disk_usage("/").free


class Net(sensors.Net):
    # Previous psutil counters, per interface: {interface name: (monotonic timestamp, counters)}
    _psutil_before = {}

    @staticmethod
    def stats(if_name, interval) -> Tuple[
        int, int, int, int]:  # up rate (B/s), uploaded (B), dl rate (B/s), downloaded (B)
        upload_rate = 0
        uploaded = 0
        download_rate = 0
        downloaded = 0

        counters = psutil.net_io_counters(pernic=True).get(if_name) if if_name else None
        if counters is not None:
            now = time.monotonic()
            before = Net._psutil_before.get(if_name)
            if before is not None and now > before[0]:
                elapsed = now - before[0]
                upload_rate = int(max(0, counters.bytes_sent - before[1].bytes_sent) / elapsed)
                download_rate = int(max(0, counters.bytes_recv - before[1].bytes_recv) / elapsed)
            uploaded = counters.bytes_sent
            downloaded = counters.bytes_recv
            Net._psutil_before[if_name] = (now, counters)

        return upload_rate, uploaded, download_rate, downloaded
