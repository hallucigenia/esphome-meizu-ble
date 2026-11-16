"""Compile production protocol, codec and GATT adapter with a deterministic BLE HAL."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import platform
import random
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("meizu_oracle", ROOT / "custom_components/meizu_remoter_gateway/codec.py")
codec = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(codec)
SAMPLES = json.loads((ROOT / "tests/meizu_remoter_gateway/fixtures/learned.json").read_text())


class NativeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.folder = tempfile.TemporaryDirectory()
        cls.binary = Path(cls.folder.name) / "meizu-host"
        component = ROOT / "components/meizu_ble"
        command = [os.environ.get("CXX", "c++"), "-std=c++17", "-Wall", "-Wextra", "-Werror", "-Wno-deprecated-declarations",
                   "-g", "-fsanitize=address,undefined", "-DUSE_MEIZU_BLE_API", "-I", str(ROOT / "tests/esphome/stubs"),
                   "-I", str(component), str(component / "codec.cpp"), str(component / "protocol.cpp"),
                   str(component / "meizu_ble.cpp"), str(ROOT / "tests/esphome/host_test.cpp"), "-o", str(cls.binary)]
        if platform.system() != "Darwin":
            command += ["-lcrypto"]
        subprocess.run(command, check=True, capture_output=True, text=True)

    @classmethod
    def tearDownClass(cls):
        cls.folder.cleanup()

    def execute(self, args, content):
        env = {**os.environ, "ASAN_OPTIONS": "alloc_dealloc_mismatch=1:detect_leaks=" + ("0" if platform.system() == "Darwin" else "1")}
        result = subprocess.run([str(self.binary), *args], input=content, text=True, capture_output=True, env=env)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return result.stdout.splitlines()

    def test_protocol_and_real_gatt_adapter_ordering(self):
        sample = SAMPLES[0]
        converted = codec.convert_capture(bytes.fromhex(sample["raw"]), "tv")
        self.execute([], f'{sample["raw"]} {sample["code"]} {converted["key"]}\n')

    def test_periodic_polling_preserves_capped_retry_backoff_and_recovers(self):
        self.execute(["poll-backoff"], "")

    def test_cpp_conversion_matches_independent_vectors_and_unicode_cache_keys(self):
        rows, expected = [], []
        for sample in SAMPLES:
            for context in ("tv", "hdmi_switch", "客厅电视📺"):
                converted = codec.convert_capture(bytes.fromhex(sample["raw"]), context)
                digest = hashlib.md5(bytes.fromhex(sample["code"])).hexdigest()
                rows.append(f'{sample["raw"]} {context.encode().hex()} {digest}')
                expected.append(f'{sample["code"]} {converted["key"]} {converted["carrier_hz"]}')
        self.assertEqual(self.execute(["codec"], "\n".join(rows) + "\n"), expected)

    def test_mutated_captures_match_python_acceptance_and_never_overrun(self):
        rng = random.Random(123)
        rows, expected = [], []
        original = bytes.fromhex(SAMPLES[0]["raw"])
        values = [original[:n] for n in range(1, len(original))] + [original + b'\x00']
        for _ in range(300):
            raw = bytearray(original)
            raw[rng.randrange(len(raw))] = rng.randrange(256)
            values.append(bytes(raw))
        for raw in values:
            try:
                result = codec.convert_capture(raw, "tv")
            except ValueError:
                rows.append(f'{raw.hex()} 7476 {"00" * 16}')
                expected.append("error")
            else:
                digest = hashlib.md5(bytes.fromhex(result["ir_code"])).hexdigest()
                rows.append(f'{raw.hex()} 7476 {digest}')
                expected.append(f'{result["ir_code"]} {result["key"]} {result["carrier_hz"]}')
        self.assertEqual(self.execute(["codec"], "\n".join(rows) + "\n"), expected)


if __name__ == "__main__":
    unittest.main()
