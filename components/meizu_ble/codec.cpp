#include "codec.h"
#include <algorithm>
#include <cstring>
#include <cstdlib>

namespace esphome::meizu_ble {
namespace {
uint16_t be16(const uint8_t *p) { return (uint16_t(p[0]) << 8) | p[1]; }
void put16(uint8_t *p, uint16_t value) { p[0] = value >> 8; p[1] = value; }
int hex_digit(char c) {
  if (c >= '0' && c <= '9') return c - '0';
  if (c >= 'a' && c <= 'f') return c - 'a' + 10;
  if (c >= 'A' && c <= 'F') return c - 'A' + 10;
  return -1;
}
}  // namespace

bool parse_hex(const std::string &text, uint8_t *output, size_t capacity, size_t &length) {
  length = 0;
  if (text.size() % 2 || text.size() / 2 > capacity) return false;
  for (size_t i = 0; i < text.size(); i += 2) {
    int high = hex_digit(text[i]), low = hex_digit(text[i + 1]);
    if (high < 0 || low < 0) return false;
    output[length++] = uint8_t((high << 4) | low);
  }
  return true;
}

std::string hex_string(const uint8_t *data, size_t length) {
  static constexpr char HEX[] = "0123456789abcdef";
  std::string value(length * 2, '0');
  for (size_t i = 0; i < length; i++) { value[i * 2] = HEX[data[i] >> 4]; value[i * 2 + 1] = HEX[data[i] & 15]; }
  return value;
}

bool convert_capture(const uint8_t *raw, size_t length, std::array<uint8_t, MAX_IR_CODE> &code,
                     size_t &code_length, uint32_t &carrier_hz) {
  code_length = 0;
  if (length < 10 || length > MAX_CAPTURE) return false;
  const size_t groups = raw[3], offset = raw[1], table_length = raw[2];
  const uint32_t period = uint32_t(raw[4]) + 1;
  if (groups < 1 || groups > 31 || offset != groups + 1 || table_length != 4 * (groups + 1) || period == 256)
    return false;
  const size_t table = 5 + offset, bits = table + table_length;
  if (bits > length) return false;
  size_t count = 0;
  for (size_t i = 0; i < offset; i++) { if (!raw[5 + i]) return false; count += raw[5 + i]; }
  if (count < 3 || count > 600 || length != bits + (count + 7) / 8) return false;
  carrier_hz = 2500000 / period;
  if (carrier_hz < 10000 || carrier_hz > 100000) return false;

  std::array<std::array<uint16_t, 2>, 8> dictionary{};
  std::array<uint8_t, 600> indices{};
  size_t dictionary_size = 0, position = 0;
  for (size_t run = 0; run < offset; run++) {
    for (size_t n = 0; n < raw[5 + run]; n++, position++) {
      const uint8_t bit = (raw[bits + position / 8] >> (7 - position % 8)) & 1;
      if (run == 0 && !bit) return false;
      const size_t pair_index = run == 0 ? 0 : run - 1 + bit;
      uint32_t mark = be16(raw + table + pair_index * 4), space = be16(raw + table + pair_index * 4 + 2);
      if (!mark || !space || (position + 1 == count ? space != 0xffff : space == 0xffff)) return false;
      space = space * 8 / period + 1;
      if (space > 65535) return false;
      size_t index = 0;
      for (; index < dictionary_size; index++) {
        if (std::abs(int(mark) - dictionary[index][0]) < 2 && std::abs(int(space) - dictionary[index][1]) < 2) break;
      }
      if (index == dictionary_size) {
        if (dictionary_size == dictionary.size()) return false;
        dictionary[dictionary_size++] = {uint16_t(mark), uint16_t(space)};
      }
      indices[position] = uint8_t(index);
    }
  }
  code_length = 74 + (count + 1) / 2;
  std::fill(code.begin(), code.begin() + code_length, 0);
  code[0] = 0x54; put16(code.data() + 1, code_length);
  code[3] = uint8_t(1200000 / carrier_hz + 1); put16(code.data() + 4, count); code[8] = 1;
  for (size_t i = 0; i < dictionary_size; i++) {
    put16(code.data() + 10 + i * 4, dictionary[i][0]); put16(code.data() + 12 + i * 4, dictionary[i][1]);
  }
  for (size_t i = 0; i < count; i++) code[74 + i / 2] |= indices[i] << (i % 2 ? 0 : 4);
  uint8_t crc = 0;
  for (size_t i = 10; i < code_length; i++) {
    crc ^= code[i];
    for (unsigned n = 0; n < 8; n++) crc = (crc >> 1) ^ ((crc & 1) ? 0x8c : 0);
  }
  code[9] = crc;
  return true;
}

bool make_cache_key(size_t length, const uint8_t digest[16], const std::string &device, std::array<uint8_t, 9> &key) {
  if (!length || length > MAX_IR_CODE || device.empty() || device.size() > 320) return false;
  uint32_t key_id = 1, remote = 0;
  for (unsigned i = 0; i < 16; i++) key_id = key_id * 31 + int32_t(int8_t(digest[i]));
  for (char c : std::string("ha:")) remote = remote * 31 + uint8_t(c);
  size_t characters = 0;
  for (size_t i = 0; i < device.size();) {
    const uint8_t lead = device[i++];
    uint32_t cp = lead;
    unsigned follow = 0;
    if (lead >= 0xc2 && lead <= 0xdf) { cp = lead & 31; follow = 1; }
    else if (lead >= 0xe0 && lead <= 0xef) { cp = lead & 15; follow = 2; }
    else if (lead >= 0xf0 && lead <= 0xf4) { cp = lead & 7; follow = 3; }
    else if (lead >= 0x80) return false;
    if (i + follow > device.size()) return false;
    for (unsigned n = 0; n < follow; n++) {
      const uint8_t value = device[i++]; if ((value & 0xc0) != 0x80) return false;
      cp = (cp << 6) | (value & 63);
    }
    if ((follow == 1 && cp < 0x80) || (follow == 2 && cp < 0x800) || (follow == 3 && cp < 0x10000) ||
        cp > 0x10ffff || (cp >= 0xd800 && cp <= 0xdfff) || ++characters > 80) return false;
    if (cp > 0xffff) { cp -= 0x10000; remote = remote * 31 + 0xd800 + (cp >> 10); cp = 0xdc00 + (cp & 1023); }
    remote = remote * 31 + cp;
  }
  key[0] = length; key[1] = length >> 8; key[2] = 0x1c;
  for (unsigned i = 0; i < 4; i++) key[3 + i] = key_id >> (8 * i);
  key[7] = remote; key[8] = remote >> 8;
  return true;
}
}  // namespace esphome::meizu_ble
