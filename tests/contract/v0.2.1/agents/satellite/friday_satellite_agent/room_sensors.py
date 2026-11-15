"""Room climate sensors of the satellite: BME280 (I2C) and DS18B20 (1-Wire).

Pure parsing and compensation functions plus thin, injectable readers. `smbus2` is optional:
without it a BME280 is simply not detected (the DS18B20 still works). Reads every 60 s,
re-detection every 5 min, so a sensor plugged in later appears without a restart.
"""

from __future__ import annotations

import asyncio
import glob
import logging
import struct
import time
from collections.abc import Callable
from typing import Protocol

LOG = logging.getLogger("friday_satellite_agent.room")

READ_S = 60
REDETECT_S = 300
I2C_BUS = 1
BME280_ADDRESSES = (0x76, 0x77)
BME280_CHIP_ID = 0x60
W1_GLOB = "/sys/bus/w1/devices/28-*/w1_slave"


class RoomSensor(Protocol):
    keys: list[str]

    def read(self) -> dict[str, float]: ...


# ---- BME280 compensation (Bosch datasheet, section 4.2.3, integer formulas) ----

def _div(a: int, b: int) -> int:
    """C-style integer division (truncates toward zero)."""
    q = abs(a) // abs(b)
    return q if (a < 0) == (b < 0) else -q


def _s16(v: int) -> int:
    return v - 65536 if v > 32767 else v


def parse_calibration(block1: bytes, h1: int, block2: bytes) -> dict[str, int]:
    """block1 = registers 0x88..0x9F (24 bytes), h1 = register 0xA1, block2 = 0xE1..0xE7 (7 bytes)."""
    t1, t2, t3, p1, p2, p3, p4, p5, p6, p7, p8, p9 = struct.unpack("<HhhHhhhhhhhh", block1)
    h2 = struct.unpack("<h", block2[0:2])[0]
    h3 = block2[2]
    h4 = (block2[3] << 4) | (block2[4] & 0x0F)
    h4 = h4 - 4096 if h4 > 2047 else h4
    h5 = (block2[5] << 4) | (block2[4] >> 4)
    h5 = h5 - 4096 if h5 > 2047 else h5
    h6 = block2[6] - 256 if block2[6] > 127 else block2[6]
    return {"T1": t1, "T2": t2, "T3": t3, "P1": p1, "P2": p2, "P3": p3, "P4": p4, "P5": p5,
            "P6": p6, "P7": p7, "P8": p8, "P9": p9, "H1": h1, "H2": h2, "H3": h3, "H4": h4,
            "H5": h5, "H6": h6}


def compensate_temperature(adc_t: int, c: dict) -> tuple[float, int]:
    """Returns (degrees C, t_fine)."""
    var1 = (((adc_t >> 3) - (c["T1"] << 1)) * c["T2"]) >> 11
    var2 = (((((adc_t >> 4) - c["T1"]) * ((adc_t >> 4) - c["T1"])) >> 12) * c["T3"]) >> 14
    t_fine = var1 + var2
    return ((t_fine * 5 + 128) >> 8) / 100.0, t_fine


def compensate_pressure(adc_p: int, t_fine: int, c: dict) -> float | None:
    """Pascals, from the 64-bit formula; None when the calibration would divide by zero."""
    var1 = t_fine - 128000
    var2 = var1 * var1 * c["P6"]
    var2 += (var1 * c["P5"]) << 17
    var2 += c["P4"] << 35
    var1 = ((var1 * var1 * c["P3"]) >> 8) + ((var1 * c["P2"]) << 12)
    var1 = (((1 << 47) + var1) * c["P1"]) >> 33
    if var1 == 0:
        return None
    p = 1048576 - adc_p
    p = _div(((p << 31) - var2) * 3125, var1)
    var1 = (c["P9"] * (p >> 13) * (p >> 13)) >> 25
    var2 = (c["P8"] * p) >> 19
    p = ((p + var1 + var2) >> 8) + (c["P7"] << 4)
    return p / 256.0


def compensate_humidity(adc_h: int, t_fine: int, c: dict) -> float:
    """Percent relative humidity, 32-bit formula, clamped to 0..100."""
    v = t_fine - 76800
    v = (((((adc_h << 14) - (c["H4"] << 20) - (c["H5"] * v)) + 16384) >> 15)
         * (((((((v * c["H6"]) >> 10) * (((v * c["H3"]) >> 11) + 32768)) >> 10) + 2097152)
              * c["H2"] + 8192) >> 14))
    v = v - (((((v >> 15) * (v >> 15)) >> 7) * c["H1"]) >> 4)
    v = max(0, min(v, 419430400))
    return (v >> 12) / 1024.0


def parse_raw(data: bytes) -> tuple[int, int, int]:
    """Registers 0xF7..0xFE -> (adc_p, adc_t, adc_h)."""
    return ((data[0] << 12) | (data[1] << 4) | (data[2] >> 4),
            (data[3] << 12) | (data[4] << 4) | (data[5] >> 4),
            (data[6] << 8) | data[7])


def compensate(data: bytes, c: dict) -> dict[str, float]:
    adc_p, adc_t, adc_h = parse_raw(data)
    temp, t_fine = compensate_temperature(adc_t, c)
    out = {"room_temperature": round(temp, 1),
           "room_humidity": round(compensate_humidity(adc_h, t_fine, c), 1)}
    pressure = compensate_pressure(adc_p, t_fine, c)
    if pressure is not None:
        out["room_pressure"] = round(pressure / 100.0, 1)  # hPa
    return out


class Bme280:
    keys = ["room_temperature", "room_humidity", "room_pressure"]

    def __init__(self, bus, address: int) -> None:
        self.bus, self.address = bus, address
        self.cal = parse_calibration(
            bytes(bus.read_i2c_block_data(address, 0x88, 24)),
            bus.read_byte_data(address, 0xA1),
            bytes(bus.read_i2c_block_data(address, 0xE1, 7)))

    @classmethod
    def probe(cls, bus, address: int):
        try:
            if bus.read_byte_data(address, 0xD0) != BME280_CHIP_ID:
                return None
            return cls(bus, address)
        except OSError:
            return None

    def read(self) -> dict[str, float]:
        self.bus.write_byte_data(self.address, 0xF2, 0x01)  # humidity oversampling x1
        self.bus.write_byte_data(self.address, 0xF4, 0x25)  # temp/press x1, forced mode
        time.sleep(0.015)
        return compensate(bytes(self.bus.read_i2c_block_data(self.address, 0xF7, 8)), self.cal)


# ---- DS18B20 ----

def parse_ds18b20(text: str) -> float | None:
    """w1_slave: first line must end in YES (CRC ok); second carries `t=<milli-degrees>`."""
    lines = text.strip().splitlines()
    if len(lines) < 2 or not lines[0].strip().endswith("YES"):
        return None
    _, _, raw = lines[1].partition("t=")
    try:
        milli = int(raw)
    except ValueError:
        return None
    if milli == 85000:  # 85 C is the power-on value: not a measurement
        return None
    return round(milli / 1000, 1)


class Ds18b20:
    keys = ["room_temperature"]

    def __init__(self, path: str) -> None:
        self.path = path

    def read(self) -> dict[str, float]:
        with open(self.path, encoding="ascii") as f:
            value = parse_ds18b20(f.read())
        if value is None:
            raise OSError("ds18b20 read failed")
        return {"room_temperature": value}


def _default_bus_factory():
    try:
        import smbus2  # optional dependency
    except ImportError:
        return None
    return smbus2.SMBus(I2C_BUS)


class RoomSensors:
    """Finds the drivers and reads them; BME280 wins over a DS18B20 for the temperature."""

    def __init__(self, bus_factory: Callable = _default_bus_factory, w1_glob: str = W1_GLOB) -> None:
        self._bus_factory, self._w1_glob = bus_factory, w1_glob
        self.drivers: list[RoomSensor] = []

    def detect(self) -> list[str]:
        drivers: list[RoomSensor] = []
        try:
            bus = self._bus_factory()
        except Exception:  # noqa: BLE001 - no I2C bus (or no permission): not detected
            bus = None
        if bus is not None:
            for address in BME280_ADDRESSES:
                found = Bme280.probe(bus, address)
                if found:
                    drivers.append(found)
                    break
        for path in sorted(glob.glob(self._w1_glob)):
            drivers.append(Ds18b20(path))
        self.drivers = drivers
        return sorted({k for d in drivers for k in d.keys})

    def read(self) -> dict[str, float]:
        out: dict[str, float] = {}
        for driver in self.drivers:
            try:
                values = driver.read()
            except Exception as err:  # noqa: BLE001 - a flaky sensor never stops the others
                LOG.warning("room sensor read failed: %s", type(err).__name__)
                continue
            for k, v in values.items():
                out.setdefault(k, v)
        return out


class Monitor:
    """Reads every READ_S, re-detects every REDETECT_S; pushes capabilities when the keys change."""

    def __init__(self, sensors: RoomSensors, push_state, push_capabilities,
                 clock=time.monotonic) -> None:
        self.sensors, self._push_state, self._push_caps, self._clock = (
            sensors, push_state, push_capabilities, clock)
        self.keys: list[str] = []
        self._next_detect = 0.0

    def prime(self) -> list[str]:
        """Detect once before the first hello, so the hello already lists the descriptors."""
        self.keys = self.sensors.detect()
        self._next_detect = self._clock() + REDETECT_S
        return self.keys

    async def step(self) -> dict:
        if self._clock() >= self._next_detect:
            self._next_detect = self._clock() + REDETECT_S
            keys = await asyncio.to_thread(self.sensors.detect)
            if keys != self.keys:
                self.keys = keys
                await self._push_caps()
        values = await asyncio.to_thread(self.sensors.read) if self.keys else {}
        if values:
            await self._push_state(values)
        return values
