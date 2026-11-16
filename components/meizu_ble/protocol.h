#pragma once
#include "codec.h"

namespace esphome::meizu_ble {

enum class Operation : uint8_t { NONE, POLL, SEND, LEARN };
enum class Phase : uint8_t { IDLE, TEMPERATURE, VOLTAGE, CACHE, UPLOAD, UPLOAD_ACK, READY, CAPTURE, FINISH, STOP, DONE };
struct Frame { std::array<uint8_t, 20> bytes{}; size_t length{0}; };

// Pure, bounded protocol state machine. BLE scheduling belongs to the ESPHome adapter.
class Protocol {
 public:
  bool start_poll(uint8_t sequence, uint32_t now);
  bool start_send(uint8_t sequence, uint32_t now, const uint8_t *key, size_t key_length,
                  const uint8_t *code, size_t code_length);
  bool start_learn(uint8_t sequence, uint32_t now, uint32_t window_ms);
  void receive(const uint8_t *data, size_t length, uint32_t now);
  void write_complete(bool success, uint32_t now);
  void tick(uint32_t now);
  void cancel(uint32_t now);
  void abort(const char *reason, uint32_t now);
  void reset() { this->operation_ = Operation::NONE; this->phase_ = Phase::IDLE; this->write_pending_ = false; this->raw_length_ = 0; }
  bool busy() const { return this->phase_ != Phase::IDLE && this->phase_ != Phase::DONE; }
  bool done() const { return this->phase_ == Phase::DONE; }
  bool has_write() const { return this->write_pending_; }
  bool needs_read() const { return this->operation_ == Operation::POLL && !this->write_pending_ && this->busy(); }
  const Frame &frame() const { return this->frame_; }
  Operation operation() const { return this->operation_; }
  Phase phase() const { return this->phase_; }
  const char *status() const { return this->status_; }
  bool confirmed() const { return this->ack_ == 2 || this->ack_ == 4; }
  uint8_t ack() const { return this->ack_; }
  size_t received() const { return this->raw_length_; }
  size_t expected() const { return this->capture_length_; }
  uint32_t window_ms() const { return this->window_ms_; }
  const uint8_t *raw() const { return this->raw_.data(); }
  float temperature() const { return this->temperature_ / 100.0f; }
  float humidity() const { return this->humidity_ / 100.0f; }
  uint8_t voltage() const { return this->voltage_; }

 protected:
  void begin_(Operation operation, uint8_t sequence, uint32_t now, Phase phase);
  void command_(uint8_t command, const uint8_t *payload = nullptr, size_t length = 0);
  void packet_(size_t index);
  void stop_(const char *status, uint32_t now);
  void success_();
  Operation operation_{Operation::NONE};
  Phase phase_{Phase::IDLE};
  Frame frame_;
  std::array<uint8_t, MAX_IR_CODE> code_{};
  std::array<uint8_t, MAX_CAPTURE> raw_{};
  std::array<uint8_t, 9> key_{};
  size_t code_length_{0}, raw_length_{0}, capture_length_{0}, packet_count_{0}, packet_index_{0}, repairs_{0};
  uint32_t deadline_{0}, hard_deadline_{0}, window_ms_{0};
  uint16_t temperature_{0}, humidity_{0};
  uint8_t sequence_{0}, ack_{0}, voltage_{0};
  bool write_pending_{false}, cancelling_{false};
  const char *status_{"idle"};
};
}  // namespace esphome::meizu_ble
