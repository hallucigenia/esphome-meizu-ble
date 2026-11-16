"""Independent on-device captures, original converter vectors and bad inputs."""
import importlib.util
import json
from pathlib import Path
import struct
import unittest

ROOT = Path(__file__).resolve().parent
SPEC = importlib.util.spec_from_file_location("meizu_codec_test", ROOT.parents[1] / "custom_components/meizu_remoter_gateway/codec.py")
codec = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(codec)
SAMPLES = json.loads((ROOT / "fixtures/learned.json").read_text())


class CodecTests(unittest.TestCase):
    def test_nine_real_captures_match_original_converter_byte_for_byte(self):
        for sample in SAMPLES:
            with self.subTest(command=sample["name"]):
                result = codec.convert_capture(bytes.fromhex(sample["raw"]), "hdmi_switch")
                self.assertEqual(result["ir_code"], sample["code"])
                self.assertEqual(len(bytes.fromhex(result["key"])), 9)
                self.assertEqual(result["carrier_hz"], codec.decode_capture(bytes.fromhex(sample["raw"]))[0])

    def test_malformed_lengths_headers_and_incomplete_capture_are_rejected(self):
        raw = bytes.fromhex(SAMPLES[0]["raw"])
        for value in (b"", raw[:-1], raw + b"\0", raw[:3] + b"\xff" + raw[4:],
                      raw[:4] + b"\xff" + raw[5:], raw[:5] + b"\0" + raw[6:]):
            with self.subTest(value=value.hex()), self.assertRaises(ValueError):
                codec.convert_capture(value, "tv")

    def test_dictionary_supports_other_waveforms_without_nec_decoding(self):
        pulses = [200, 100, 12, 15, 12, 30, 12, 15, 12, 8000]
        code = codec.encode_pulses(38000, pulses)
        self.assertEqual(code[0], 0x54)
        self.assertEqual(int.from_bytes(code[1:3], "big"), len(code))
        self.assertEqual(int.from_bytes(code[4:6], "big"), 5)
        self.assertEqual(struct.unpack_from(">HH", code, 10), (200, 100))
        self.assertEqual(code[-3:], bytes.fromhex("012130"))
        self.assertEqual(code[9], codec.crc8(code[10:]))

    def test_unsupported_waveform_does_not_become_a_playable_key(self):
        for frequency, pulses in ((0, [1] * 6), (38000, [1] * 5),
                                  (38000, [value for i in range(9) for value in (i * 5 + 1, 10)])):
            with self.assertRaises(ValueError):
                codec.encode_pulses(frequency, pulses)

    def test_cache_id_is_stable_and_retains_original_hdmi_context(self):
        code = bytes.fromhex(SAMPLES[0]["code"])
        self.assertEqual(codec.cache_key(code, "hdmi_switch"), "5c001c6f151272ba04")
        self.assertNotEqual(codec.cache_key(code, "tv"), codec.cache_key(code, "hdmi_switch"))
