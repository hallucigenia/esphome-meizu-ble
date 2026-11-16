"""Offline NEC validation and legacy R16 packet formatting; no device I/O."""
import hashlib
import struct


def nec_code(pulses):
    """Decode a complete NEC frame, including both inverse-byte checks."""
    if len(pulses) < 67:
        raise ValueError('No complete NEC frame')
    marks, spaces = pulses[2:66:2], pulses[3:66:2]
    unit = sum(marks) / len(marks)
    if unit <= 0 or pulses[0] < unit * 8 or pulses[1] < unit * 4:
        raise ValueError('Invalid NEC leader')
    if any(not .5 * unit <= mark <= 1.5 * unit for mark in marks):
        raise ValueError('Invalid NEC bit marks')
    short, long = min(spaces), max(spaces)
    if short <= 0 or not 2 < long / short < 4.5:
        raise ValueError('Invalid NEC bit spaces')
    threshold = (short + long) / 2
    bits = [int(space > threshold) for space in spaces]
    data = bytes(sum(bits[offset + bit] << bit for bit in range(8))
                 for offset in range(0, 32, 8))
    if data[0] ^ data[1] != 255 or data[2] ^ data[3] != 255:
        raise ValueError('NEC inverse-byte check failed')
    return data.hex()


def cache_key(code, remote='ha:hdmi_switch'):
    """Original app header, excluding its separately generated packet count."""
    key_id = 1
    for byte in hashlib.md5(code).digest():
        key_id = (31 * key_id + (byte if byte < 128 else byte - 256)) & 0xffffffff
    remote_id = 0
    encoded = remote.encode('utf-16-be')
    for offset in range(0, len(encoded), 2):
        remote_id = (31 * remote_id + int.from_bytes(encoded[offset:offset + 2], 'big')) & 0xffffffff
    return struct.pack('<HBIH', len(code), 0x1c, key_id, remote_id & 0xffff)


def send_payloads(key, code):
    if len(key) != 9 or int.from_bytes(key[:2], 'little') != len(code):
        raise ValueError('Code length does not match cache header')
    count = 1 + (len(code) + 14) // 15
    if count > 255:
        raise ValueError('Too many BLE packets')
    return [bytes([0, count]) + key] + [bytes([index + 1]) + code[offset:offset + 15]
        for index, offset in enumerate(range(0, len(code), 15))]
