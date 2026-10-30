"""Room sensors of the satellite: BME280 compensation, DS18B20 parsing, re-detection."""

import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "agents" / "satellite"))

from friday_satellite_agent import capabilities as cap  # noqa: E402
from friday_satellite_agent import room_sensors as rs  # noqa: E402

# Bosch BMP280/BME280 datasheet worked example (temperature and pressure)
CAL = {"T1": 27504, "T2": 26435, "T3": -1000,
       "P1": 36477, "P2": -10685, "P3": 3024, "P4": 2855, "P5": 140, "P6": -7,
       "P7": 15500, "P8": -14600, "P9": 6000,
       "H1": 75, "H2": 362, "H3": 0, "H4": 316, "H5": 50, "H6": 30}


def reference_pressure(adc_p, t_fine, c):
    """Datasheet appendix 8.2 (double precision), an independent check of the integer formula."""
    var1 = t_fine / 2.0 - 64000.0
    var2 = var1 * var1 * c["P6"] / 32768.0
    var2 = var2 + var1 * c["P5"] * 2.0
    var2 = var2 / 4.0 + c["P4"] * 65536.0
    var1 = (c["P3"] * var1 * var1 / 524288.0 + c["P2"] * var1) / 524288.0
    var1 = (1.0 + var1 / 32768.0) * c["P1"]
    p = 1048576.0 - adc_p
    p = (p - var2 / 4096.0) * 6250.0 / var1
    var1 = c["P9"] * p * p / 2147483648.0
    var2 = p * c["P8"] / 32768.0
    return p + (var1 + var2 + c["P7"]) / 16.0


def test_bme280_compensation_matches_datasheet_example():
    temp, t_fine = rs.compensate_temperature(519888, CAL)
    assert temp == 25.08 and t_fine == 128422
    pressure = rs.compensate_pressure(415148, t_fine, CAL)
    assert abs(pressure - 100653.27) < 0.05  # the datasheet value (double formula)
    assert abs(pressure - reference_pressure(415148, t_fine, CAL)) < 0.05


def test_bme280_humidity_matches_datasheet_float_formula():
    # datasheet appendix 8.1 (double precision) as an independent reference
    def reference(adc_h, t_fine, c):
        v = t_fine - 76800.0
        v = (adc_h - (c["H4"] * 64.0 + c["H5"] / 16384.0 * v)) * (
            c["H2"] / 65536.0 * (1.0 + c["H6"] / 67108864.0 * v * (1.0 + c["H3"] / 67108864.0 * v)))
        v = v * (1.0 - c["H1"] * v / 524288.0)
        return min(100.0, max(0.0, v))

    for adc_h in (20000, 30000, 40000, 50000):
        got = rs.compensate_humidity(adc_h, 128422, CAL)
        assert abs(got - reference(adc_h, 128422, CAL)) < 0.05
    assert 0 <= rs.compensate_humidity(0, 128422, CAL) <= 100
    assert rs.compensate_humidity(65535, 128422, CAL) == 100.0


def test_calibration_parse_roundtrip():
    block1 = struct.pack("<HhhHhhhhhhhh", 27504, 26435, -1000, 36477, -10685, 3024, 2855, 140, -7,
                         15500, -14600, 6000)
    # H4 = 0x13C (316), H5 = 0x032 (50): E4 = H4>>4, E5 low nibble = H4&0xF, E5 high nibble = H5&0xF, E6 = H5>>4
    block2 = bytes([362 & 255, 362 >> 8, 0, 316 >> 4, (316 & 15) | ((50 & 15) << 4), 50 >> 4, 30])
    assert rs.parse_calibration(block1, 75, block2) == CAL


def test_ds18b20_parse():
    ok = "72 01 4b 46 7f ff 0e 10 57 : crc=57 YES\n72 01 4b 46 7f ff 0e 10 57 t=23125\n"
    assert rs.parse_ds18b20(ok) == 23.1
    assert rs.parse_ds18b20(ok.replace("YES", "NO ")) is None
    assert rs.parse_ds18b20("garbage") is None
    assert rs.parse_ds18b20(ok.replace("t=23125", "t=85000")) is None  # power-on value


def test_ds18b20_detected_from_sysfs(tmp_path):
    dev = tmp_path / "28-000005e2fdc3"
    dev.mkdir()
    (dev / "w1_slave").write_text("aa : crc=aa YES\naa t=21500\n")
    sensors = rs.RoomSensors(bus_factory=lambda: None, w1_glob=str(tmp_path / "28-*/w1_slave"))
    assert sensors.detect() == ["room_temperature"]
    assert sensors.read() == {"room_temperature": 21.5}


def test_without_smbus2_bme280_is_not_detected(tmp_path):
    def no_bus():
        raise ImportError("smbus2")

    sensors = rs.RoomSensors(bus_factory=no_bus, w1_glob=str(tmp_path / "none"))
    assert sensors.detect() == [] and sensors.read() == {}


class FakeBus:
    def __init__(self, present):
        self.present = present

    def read_byte_data(self, addr, reg):
        if addr not in self.present:
            raise OSError("no device")
        return 0x60 if reg == 0xD0 else 75

    def read_i2c_block_data(self, addr, reg, n):
        if reg == 0x88:
            return list(struct.pack("<HhhHhhhhhhhh", 27504, 26435, -1000, 36477, -10685, 3024, 2855,
                                    140, -7, 15500, -14600, 6000))
        return [106, 1, 0, 19, 0xC2, 3, 30][:n]

    def write_byte_data(self, *a):
        pass


def test_bme280_probe_finds_second_address():
    sensors = rs.RoomSensors(bus_factory=lambda: FakeBus({0x77}), w1_glob="/nonexistent/*")
    assert sensors.detect() == ["room_humidity", "room_pressure", "room_temperature"]
    assert sensors.drivers[0].address == 0x77


async def test_redetect_pushes_capabilities(tmp_path):
    now = [0.0]
    pushed, caps = [], []

    async def push_state(v):
        pushed.append(v)

    async def push_caps():
        caps.append(1)

    sensors = rs.RoomSensors(bus_factory=lambda: None, w1_glob=str(tmp_path / "28-*/w1_slave"))
    mon = rs.Monitor(sensors, push_state, push_caps, clock=lambda: now[0])
    assert mon.prime() == []
    await mon.step()
    assert pushed == [] and caps == []
    dev = tmp_path / "28-abc"
    dev.mkdir()
    (dev / "w1_slave").write_text("aa : crc=aa YES\naa t=20000\n")
    now[0] = 60
    await mon.step()
    assert caps == []  # not due yet: detection is every 5 min
    now[0] = 301
    await mon.step()
    assert caps == [1] and pushed == [{"room_temperature": 20.0}]
    now[0] = 400
    await mon.step()
    assert caps == [1]  # unchanged keys: no second announcement


def test_room_descriptors_in_capabilities():
    d = cap.room_descriptors(["room_temperature", "room_pressure"])
    assert [x["key"] for x in d] == ["room_temperature", "room_pressure"]
    assert d[0]["device_class"] == "temperature" and d[1]["unit"] == "hPa"
    assert cap.room_descriptors([]) == []
