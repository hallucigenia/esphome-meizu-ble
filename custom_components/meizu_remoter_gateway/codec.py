"""Bounded R16 ET4007 capture decoder and transmitter encoder, without JNI.

Capture uses an opaque leading byte, run counts, a table of (mark, space) pairs and an MSB-first
bitstream selecting adjacent pairs. Times are carrier cycles. The transmitter
uses a padded 64-byte pair table, nibble indices and a Dallas/Maxim CRC-8.
"""

import hashlib
import struct

MAX_RAW = 1500
MAX_CODE = 3800


def decode_capture(raw: bytes) -> tuple[int, list[int]]:
    """Return measured carrier Hz and pulse lengths in carrier cycles."""
    if not 10 <= len(raw) <= MAX_RAW:
        raise ValueError("Unsupported or incomplete ET4007 capture")
    offset, table_size, groups, period = raw[1], raw[2], raw[3], raw[4] + 1
    if not 1 <= groups <= 31 or offset != groups + 1 or table_size != 4 * (groups + 1) or period == 256:
        raise ValueError("Invalid ET4007 capture header")
    table_start = 5 + offset
    bits_start = table_start + table_size
    counts = raw[5:table_start]
    if len(counts) != offset or not counts[0] or any(count == 0 for count in counts):
        raise ValueError("Invalid ET4007 run counts")
    count = sum(counts)
    if len(raw) != bits_start + (count + 7) // 8:
        raise ValueError("ET4007 capture length mismatch")
    table = [struct.unpack_from(">HH", raw, index) for index in range(table_start, bits_start, 4)]
    bits = [(raw[bits_start + index // 8] >> (7 - index % 8)) & 1 for index in range(count)]
    pairs = []
    index = 0
    for _ in range(counts[0]):
        if not bits[index]:
            raise ValueError("Unsupported empty ET4007 leader")
        pairs.append(table[0])
        index += 1
    for group, run in enumerate(counts[1:]):
        for _ in range(run):
            pairs.append(table[group + bits[index]])
            index += 1
    if len(pairs) < 3 or pairs[-1][1] != 0xFFFF:
        raise ValueError("ET4007 capture has no complete ending")
    if any(not mark or not space or space == 0xFFFF for mark, space in pairs[:-1]) or not pairs[-1][0]:
        raise ValueError("Invalid ET4007 pulse data")
    pulses = [value for mark, space in pairs for value in (mark, space * 8 // period + 1)]
    return 2_500_000 // period, pulses


def crc8(data: bytes) -> int:
    crc = 0
    for value in data:
        crc ^= value
        for _ in range(8):
            crc = (crc >> 1) ^ (0x8C if crc & 1 else 0)
    return crc


def encode_pulses(frequency: int, pulses: list[int]) -> bytes:
    """Encode the measured waveform; preserve its repeats and trailing gap."""
    if not 10_000 <= frequency <= 100_000 or not 6 <= len(pulses) <= 1200 or len(pulses) % 2:
        raise ValueError("Unsupported carrier or waveform length")
    if any(type(value) is not int or not 1 <= value <= 65535 for value in pulses):
        raise ValueError("Invalid pulse duration")
    pairs = []
    indices = []
    for offset in range(0, len(pulses), 2):
        pair = tuple(pulses[offset:offset + 2])
        index = next((index for index, saved in enumerate(pairs)
                      if all(abs(a - b) < 2 for a, b in zip(pair, saved))), len(pairs))
        if index == len(pairs):
            if index == 8:
                raise ValueError("Waveform needs more than eight timing pairs")
            pairs.append(pair)
        indices.append(index)
    table = b"".join(struct.pack(">HH", *pair) for pair in pairs).ljust(64, b"\0")
    packed = bytes((indices[index] << 4) | (indices[index + 1] if index + 1 < len(indices) else 0)
                   for index in range(0, len(indices), 2))
    payload = table + packed
    return b"\x54" + struct.pack(">HBH", 10 + len(payload), 1_200_000 // frequency + 1, len(indices)) + b"\0\0\x01" + bytes([crc8(payload)]) + payload


def cache_key(code: bytes, device: str) -> str:
    key_id = 1
    for value in hashlib.md5(code).digest():
        key_id = (31 * key_id + (value if value < 128 else value - 256)) & 0xFFFFFFFF
    remote_id = 0
    name = ("ha:" + device).encode("utf-16-be")
    for offset in range(0, len(name), 2):
        remote_id = (31 * remote_id + int.from_bytes(name[offset:offset + 2], "big")) & 0xFFFFFFFF
    return struct.pack("<HBIH", len(code), 0x1C, key_id, remote_id & 0xFFFF).hex()


def convert_capture(raw: bytes, device: str) -> dict:
    frequency, pulses = decode_capture(raw)
    code = encode_pulses(frequency, pulses)
    return {"key": cache_key(code, device), "ir_code": code.hex(), "raw": raw.hex(),
            "format": "et4007-v1", "carrier_hz": frequency,
            "sha256": hashlib.sha256(code).hexdigest()}
