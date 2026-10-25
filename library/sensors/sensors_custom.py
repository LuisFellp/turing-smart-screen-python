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

# This file allows to add custom data source as sensors and display them in System Monitor themes
# There is no limitation on how much custom data source classes can be added to this file
# See CustomDataExample theme for the theme implementation part

import math
import platform
from abc import ABC, abstractmethod
from typing import List

from library import config

_HW_SENSORS = config.CONFIG_DATA["config"].get("HW_SENSORS", "AUTO")

get_hw_and_update = None
Hardware = None
if _HW_SENSORS in ("LHM", "AUTO"):
    try:
        # Only available when HW_SENSORS is LHM/AUTO in config.yaml (Windows only): sensors_librehardwaremonitor.py
        # is imported by library/stats.py before this file, which loads the LibreHardwareMonitor .NET assembly.
        # Guarded here so this file still imports fine on other HW_SENSORS backends (PYTHON/STUB/STATIC) or on Linux/macOS.
        # LOCAL PATCH: not even attempted on other backends, because importing that module opens the hardware
        # and exits the program when not running as administrator.
        from library.sensors.sensors_librehardwaremonitor import get_hw_and_update
        from LibreHardwareMonitor import Hardware
    except Exception:
        # Broad catch on purpose: besides ImportError (module absent on this HW_SENSORS backend), the .NET/pythonnet
        # bridge used by sensors_librehardwaremonitor.py can also raise RuntimeError or other errors (e.g. incompatible
        # pythonnet/Python ABI, missing LibreHardwareMonitorLib.dll). None of that should prevent this file from loading.
        get_hw_and_update = None
        Hardware = None

hwinfo = None
if _HW_SENSORS == "HWINFO":
    try:
        # LOCAL ADDITION: HWiNFO Gadget backend (Windows only), see sensors_hwinfo.py
        import library.sensors.sensors_hwinfo as hwinfo
    except Exception:
        hwinfo = None


# Custom data classes must be implemented in this file, inherit the CustomDataSource and implement its 2 methods
class CustomDataSource(ABC):
    @abstractmethod
    def as_numeric(self) -> float:
        # Numeric value will be used for graph and radial progress bars
        # If there is no numeric value, keep this function empty
        pass

    @abstractmethod
    def as_string(self) -> str:
        # Text value will be used for text display and radial progress bar inner text
        # Numeric value can be formatted here to be displayed as expected
        # It is also possible to return a text unrelated to the numeric value
        # If this function is empty, the numeric value will be used as string without formatting
        pass

    @abstractmethod
    def last_values(self) -> List[float]:
        # List of last numeric values will be used for plot graph
        # If you do not want to draw a line graph or if your custom data has no numeric values, keep this function empty
        pass


# Example for a custom data class that has numeric and text values
class ExampleCustomNumericData(CustomDataSource):
    # This list is used to store the last 10 values to display a line graph
    last_val = [math.nan] * 10  # By default, it is filed with math.nan values to indicate there is no data stored

    def as_numeric(self) -> float:
        # Numeric value will be used for graph and radial progress bars
        # Here a Python function from another module can be called to get data
        # Example: self.value = my_module.get_rgb_led_brightness() / audio.system_volume() ...
        self.value = 75.845

        # Store the value to the history list that will be used for line graph
        self.last_val.append(self.value)
        # Also remove the oldest value from history list
        self.last_val.pop(0)

        return self.value

    def as_string(self) -> str:
        # Text value will be used for text display and radial progress bar inner text.
        # Numeric value can be formatted here to be displayed as expected
        # It is also possible to return a text unrelated to the numeric value
        # If this function is empty, the numeric value will be used as string without formatting
        # Example here: format numeric value: add unit as a suffix, and keep 1 digit decimal precision
        return f'{self.value:>5.1f}%'
        # Important note! If your numeric value can vary in size, be sure to display it with a default size.
        # E.g. if your value can range from 0 to 9999, you need to display it with at least 4 characters every time.
        # --> return f'{self.as_numeric():>4}%'
        # Otherwise, part of the previous value can stay displayed ("ghosting") after a refresh

    def last_values(self) -> List[float]:
        # List of last numeric values will be used for plot graph
        return self.last_val


# Example for a custom data class that only has text values
class ExampleCustomTextOnlyData(CustomDataSource):
    def as_numeric(self) -> float:
        # If there is no numeric value, keep this function empty
        pass

    def as_string(self) -> str:
        # If a custom data class only has text values, it won't be possible to display graph or radial bars
        return "Python: " + platform.python_version()

    def last_values(self) -> List[float]:
        # If a custom data class only has text values, it won't be possible to display line graph
        pass


# AMD GPU "junction" / "hotspot" temperature, read from LibreHardwareMonitor (Windows only).
# The turing-smart-screen-python built-in GPU sensor only exposes the "GPU Core" (edge) temperature,
# so this custom sensor scans the GPU's sensors for the hotspot/junction reading instead.
class GPU_HOTSPOT(CustomDataSource):
    last_val = [math.nan] * 10

    # Latest valid reading, kept across calls so a single transient failure to read the
    # sensor (LibreHardwareMonitor occasionally returns no value for a cycle on AMD GPUs)
    # does not flash "N/A" on screen: the last known good value is reused instead.
    last_valid_value = math.nan

    def as_numeric(self) -> float:
        self.value = self.last_valid_value

        if hwinfo is not None:
            value = hwinfo.get_value("gpu_hotspot")
            if not math.isnan(value):
                self.value = value
                self.last_valid_value = value
            self.last_val.append(self.value)
            self.last_val.pop(0)
            return self.value

        if get_hw_and_update is None or Hardware is None:
            # LibreHardwareMonitor is not available (HW_SENSORS is not LHM/AUTO, or not on Windows)
            self.last_val.append(self.value)
            self.last_val.pop(0)
            return self.value

        GPU = get_hw_and_update(Hardware.HardwareType.GpuAmd)
        if GPU is not None:
            # LHM sensor name for this reading varies (e.g. "Hot Spot", "GPU Hot Spot Temperature (Max)"),
            # so match on substring instead of an exact name.
            for sensor in GPU.Sensors:
                if "Hot Spot" in str(sensor.Name) and sensor.Value is not None:
                    self.value = float(sensor.Value)
                    self.last_valid_value = self.value
                    break

        self.last_val.append(self.value)
        self.last_val.pop(0)

        return self.value

    def as_string(self) -> str:
        return f'{self.value:>5.0f}°C' if not math.isnan(self.value) else "N/A"

    def last_values(self) -> List[float]:
        return self.last_val

# LOCAL ADDITION: CPU fan speed in RPM, read from HWiNFO's Gadget (HW_SENSORS: HWINFO).
# The motherboard only reports this fan in RPM, so it is shown here instead of the built-in CPU FAN_SPEED (%).
class CPU_FAN_RPM(CustomDataSource):
    last_val = [math.nan] * 10

    def as_numeric(self) -> float:
        self.value = hwinfo.get_value("cpu_fan_rpm") if hwinfo is not None else math.nan
        self.last_val.append(self.value)
        self.last_val.pop(0)
        return self.value

    def as_string(self) -> str:
        return f'{self.value:>4.0f} RPM' if not math.isnan(self.value) else "N/A"

    def last_values(self) -> List[float]:
        return self.last_val

# LOCAL ADDITION: GPU fan speed in RPM, read from HWiNFO's Gadget (HW_SENSORS: HWINFO).
# Shown instead of the built-in GPU FAN_SPEED (%). 0 RPM is normal at idle (zero-RPM fan mode).
class GPU_FAN_RPM(CustomDataSource):
    last_val = [math.nan] * 10

    def as_numeric(self) -> float:
        self.value = hwinfo.get_value("gpu_fan_rpm") if hwinfo is not None else math.nan
        self.last_val.append(self.value)
        self.last_val.pop(0)
        return self.value

    def as_string(self) -> str:
        return f'{self.value:>4.0f} RPM' if not math.isnan(self.value) else "N/A"

    def last_values(self) -> List[float]:
        return self.last_val
