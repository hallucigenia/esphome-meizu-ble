#pragma once
#include <array>
#include <vector>
#include <cstring>
#ifdef __APPLE__
#include <CommonCrypto/CommonDigest.h>
#else
#include <openssl/md5.h>
#endif
namespace esphome::md5 {
class MD5Digest {
 public:
  void init() { input_.clear(); }
  void add(const uint8_t *bytes, size_t length) { input_.insert(input_.end(), bytes, bytes + length); }
  void calculate() {
#ifdef __APPLE__
    CC_MD5(input_.data(), input_.size(), digest_.data());
#else
    MD5(input_.data(), input_.size(), digest_.data());
#endif
  }
  void get_bytes(uint8_t *out) { std::memcpy(out, digest_.data(), digest_.size()); }
 protected:
  std::vector<uint8_t> input_;
  std::array<uint8_t, 16> digest_{};
};
}
