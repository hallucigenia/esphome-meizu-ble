"""Regression cases from a real R16 capture and its 20-byte GATT limit."""
import importlib.util
from pathlib import Path
import unittest

FILE = Path(__file__).resolve().parents[2] / 'tools/ir_compat.py'
SPEC = importlib.util.spec_from_file_location('meizu_ir_codec', FILE)
codec = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(codec)

# Captured HDMI input 2, including the trailing NEC repeat frame.
PULSES = [343, 162, 22, 60, 22, 19, 22, 19, 22, 19, 22, 19, 22, 19,
          22, 19, 22, 19, 22, 19, 22, 60, 22, 60, 22, 60, 22, 60,
          22, 60, 22, 60, 22, 60, 22, 19, 22, 60, 22, 60, 22, 60,
          22, 60, 22, 19, 22, 19, 22, 19, 22, 60, 22, 19, 22, 19,
          22, 19, 22, 19, 22, 60, 22, 60, 22, 60, 22, 1441, 343, 81, 22, 7599]
CODE = bytes.fromhex(
    '54005c22002400000123015700a20016003c00160013001605a1015700510016'
    '1daf000000000000000000000000000000000000000000000000000000000000'
    '00000000000000000000012222222211111112111122212222111345'
)


class IrCodecTests(unittest.TestCase):
    def test_real_full_frame_matches_independently_known_input_2(self):
        self.assertEqual(codec.nec_code(PULSES), '01fe1ee1')

    def test_repeat_only_capture_cannot_be_saved_as_a_new_key(self):
        with self.assertRaisesRegex(ValueError, 'complete NEC'):
            codec.nec_code(PULSES[-4:])

    def test_corrupt_address_inverse_is_rejected(self):
        corrupted = PULSES.copy()
        corrupted[3] = 19
        with self.assertRaisesRegex(ValueError, 'inverse-byte'):
            codec.nec_code(corrupted)

    def test_upload_respects_actual_r16_limit_and_preserves_all_bytes(self):
        self.assertEqual(len(CODE), 92)
        key = codec.cache_key(CODE)
        payloads = codec.send_payloads(key, CODE)
        self.assertTrue(all(len(payload) + 4 <= 20 for payload in payloads))
        self.assertEqual(payloads[0], bytes([0, len(payloads)]) + key)
        self.assertEqual(b''.join(payload[1:] for payload in payloads[1:]), CODE)

    def test_upload_rejects_a_header_for_different_data(self):
        with self.assertRaisesRegex(ValueError, 'length'):
            codec.send_payloads(codec.cache_key(CODE), CODE[:-1])


if __name__ == '__main__':
    unittest.main()
