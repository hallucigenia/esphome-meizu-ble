#include "protocol.h"
#include <algorithm>
#include <cstring>

namespace esphome::meizu_ble {
namespace { bool reached(uint32_t now, uint32_t deadline) { return int32_t(now - deadline) >= 0; } }

void Protocol::command_(uint8_t command, const uint8_t *payload, size_t length) {
  this->frame_.bytes = {};
  this->frame_.bytes[0] = 0x55; this->frame_.bytes[1] = length + 3;
  this->frame_.bytes[2] = this->sequence_; this->frame_.bytes[3] = command;
  if (length) std::memcpy(this->frame_.bytes.data() + 4, payload, length);
  this->frame_.length = length + 4; this->write_pending_ = true;
}

void Protocol::begin_(Operation operation, uint8_t sequence, uint32_t now, Phase phase) {
  this->operation_ = operation; this->sequence_ = sequence; this->phase_ = phase;
  this->status_ = "pending"; this->ack_ = 0; this->cancelling_ = false;
  this->raw_length_ = this->capture_length_ = this->packet_count_ = this->packet_index_ = this->repairs_ = 0;
  this->deadline_ = now + 8000; this->hard_deadline_ = now + 45000;
}

bool Protocol::start_poll(uint8_t sequence, uint32_t now) {
  if (this->busy()) return false;
  this->begin_(Operation::POLL, sequence, now, Phase::TEMPERATURE); this->command_(0x11); return true;
}

bool Protocol::start_send(uint8_t sequence, uint32_t now, const uint8_t *key, size_t key_length,
                          const uint8_t *code, size_t length) {
  if (this->busy() || key_length != 9 || !key || length > MAX_IR_CODE || (length && !code) ||
      (length && size_t(key[0] | (key[1] << 8)) != length)) return false;
  this->begin_(Operation::SEND, sequence, now, Phase::CACHE);
  std::copy(key, key + 9, this->key_.begin()); this->code_length_ = length;
  if (length) std::copy(code, code + length, this->code_.begin());
  this->deadline_ = this->hard_deadline_;
  this->command_(0x03, key, 9); return true;
}

bool Protocol::start_learn(uint8_t sequence, uint32_t now, uint32_t window_ms) {
  if (this->busy() || window_ms < 1000 || window_ms > 15000) return false;
  this->begin_(Operation::LEARN, sequence, now, Phase::READY); this->window_ms_ = window_ms;
  this->command_(0x05); return true;
}

void Protocol::packet_(size_t index) {
  uint8_t payload[16] = {uint8_t(index)};
  if (index == 0) {
    payload[1] = uint8_t(1 + (this->code_length_ + 14) / 15);
    std::copy(this->key_.begin(), this->key_.end(), payload + 2); this->command_(0x00, payload, 11);
  } else {
    const size_t offset = (index - 1) * 15, length = std::min<size_t>(15, this->code_length_ - offset);
    std::copy(this->code_.begin() + offset, this->code_.begin() + offset + length, payload + 1);
    this->command_(0x00, payload, length + 1);
  }
}

void Protocol::success_() { this->status_ = "ok"; this->phase_ = Phase::DONE; this->write_pending_ = false; }

void Protocol::stop_(const char *status, uint32_t now) {
  this->status_ = status; this->phase_ = Phase::STOP; this->deadline_ = now + 1000;
  if (std::strcmp(status, "ok")) this->raw_length_ = 0;
  this->command_(0x06);
}

void Protocol::abort(const char *reason, uint32_t now) {
  if (!this->busy()) return;
  if (this->operation_ == Operation::LEARN && this->phase_ != Phase::STOP) {
    if (this->write_pending_) {
      if (this->cancelling_) { this->phase_ = Phase::DONE; this->write_pending_ = false; return; }
      this->status_ = reason; this->raw_length_ = 0; this->cancelling_ = true; this->deadline_ = now + 1000;
    } else this->stop_(reason, now);
  }
  else { this->status_ = reason; this->raw_length_ = 0; this->phase_ = Phase::DONE; this->write_pending_ = false; }
}

void Protocol::cancel(uint32_t now) {
  if (this->operation_ != Operation::LEARN || !this->busy()) return;
  if (this->write_pending_) { this->cancelling_ = true; this->status_ = "cancelled"; this->raw_length_ = 0; }
  else this->stop_("cancelled", now);
}

void Protocol::write_complete(bool success, uint32_t now) {
  if (!this->write_pending_) return;
  this->write_pending_ = false;
  if (!success) { this->abort("write_failed", now); return; }
  if (this->cancelling_) { this->cancelling_ = false; this->stop_(this->status_, now); return; }
  if (this->phase_ == Phase::STOP) { this->phase_ = Phase::DONE; return; }
  if (this->phase_ == Phase::FINISH) { this->stop_("ok", now); return; }
  if (this->phase_ == Phase::UPLOAD) {
    if (++this->packet_index_ < this->packet_count_) this->packet_(this->packet_index_);
    else this->phase_ = Phase::UPLOAD_ACK;
  }
}

void Protocol::tick(uint32_t now) {
  if (!this->busy()) return;
  if (reached(now, this->deadline_) || reached(now, this->hard_deadline_)) {
    this->abort(this->phase_ == Phase::READY ? "ready_timeout" : this->operation_ == Operation::LEARN ? "learning_timeout" : "timeout", now);
  }
}

void Protocol::receive(const uint8_t *data, size_t length, uint32_t now) {
  if (!this->busy() || this->write_pending_ || length < 4 || data[0] != 0x55 || data[2] != this->sequence_) return;
  const uint8_t cmd = data[3];
  if (length > 20 || size_t(data[1]) + 1 != length) { this->abort("invalid_frame", now); return; }
  if (this->phase_ == Phase::TEMPERATURE && cmd == 0x11) {
    if (length != 8) { this->abort("invalid_measurement", now); return; }
    this->temperature_ = data[4] | (data[5] << 8); this->humidity_ = data[6] | (data[7] << 8);
    if (this->temperature_ > 12500 || this->humidity_ > 10000) { this->abort("invalid_measurement", now); return; }
    this->phase_ = Phase::VOLTAGE; this->sequence_ = this->sequence_ >= 254 ? 1 : this->sequence_ + 1;
    this->command_(0x10); return;
  }
  if (this->phase_ == Phase::VOLTAGE && cmd == 0x10) {
    if (length != 5 || data[4] == 0 || data[4] > 50) { this->abort("invalid_measurement", now); return; }
    this->voltage_ = data[4]; this->success_(); return;
  }
  if (this->operation_ == Operation::SEND) {
    if (this->phase_ == Phase::CACHE && cmd == 0x04) {
      if (length != 5 || data[4] > 1) { this->abort("invalid_ack", now); return; }
      if (data[4]) { this->ack_ = 4; this->success_(); }
      else if (!this->code_length_) this->abort("cache_miss", now);
      else {
        this->phase_ = Phase::UPLOAD; this->packet_count_ = 1 + (this->code_length_ + 14) / 15;
        this->packet_index_ = 0; this->packet_(0);
      }
    } else if (this->phase_ == Phase::UPLOAD_ACK && cmd == 0x02) {
      if (length != 4) this->abort("invalid_ack", now);
      else { this->ack_ = 2; this->success_(); }
    } else if (this->phase_ == Phase::UPLOAD_ACK && cmd == 0x01) {
      if (length != 5 || data[4] >= this->packet_count_ || ++this->repairs_ > this->packet_count_ * 2 + 2)
        this->abort("invalid_ack", now);
      else this->packet_(data[4]);
    }
    return;
  }
  if (this->operation_ != Operation::LEARN) return;
  if (cmd == 0x08) { this->abort("learning_timeout", now); return; }
  if (cmd == 0x07 && this->phase_ == Phase::READY) {
    if (length != 4) { this->abort("invalid_frame", now); return; }
    this->phase_ = Phase::CAPTURE; this->deadline_ = now + this->window_ms_; this->hard_deadline_ = now + 30000; return;
  }
  if (cmd != 0x09 || this->phase_ != Phase::CAPTURE || length < 5) return;
  if (data[4] != this->packet_index_) {
    const uint8_t expected = this->packet_index_;
    if (++this->repairs_ > 204) this->abort("repair_failed", now);
    else this->command_(0x0a, &expected, 1);
    return;
  }
  if (this->packet_index_ == 0) {
    if (length != 8) { this->abort("invalid_header", now); return; }
    this->packet_count_ = data[5]; this->capture_length_ = data[6] | (data[7] << 8);
    if (this->capture_length_ < 10 || this->capture_length_ > MAX_CAPTURE ||
        this->packet_count_ != 1 + (this->capture_length_ + 14) / 15) { this->abort("invalid_header", now); return; }
  } else {
    const size_t count = std::min<size_t>(15, this->capture_length_ - this->raw_length_);
    if (length != count + 5) { this->abort("length_mismatch", now); return; }
    std::copy(data + 5, data + length, this->raw_.begin() + this->raw_length_); this->raw_length_ += count;
  }
  ++this->packet_index_;
  if (this->packet_index_ == this->packet_count_) { this->phase_ = Phase::FINISH; this->command_(0x0b); }
  else this->deadline_ = now + 5000;
}
}  // namespace esphome::meizu_ble
