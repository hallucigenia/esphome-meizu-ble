#pragma once

#include <array>
#include <cstddef>
#include <cstdint>
#include <string>

namespace esphome::meizu_ble {

constexpr size_t MAX_CAPTURE = 1500;
constexpr size_t MAX_IR_CODE = 3800;

// Ported from the MIT-licensed HA ET4007 codec. See this component's LICENSE.
bool convert_capture(const uint8_t *raw, size_t length, std::array<uint8_t, MAX_IR_CODE> &code,
                     size_t &code_length, uint32_t &carrier_hz);
bool make_cache_key(size_t code_length, const uint8_t digest[16], const std::string &device,
                    std::array<uint8_t, 9> &key);
bool parse_hex(const std::string &text, uint8_t *output, size_t capacity, size_t &length);
std::string hex_string(const uint8_t *data, size_t length);

}  // namespace esphome::meizu_ble
