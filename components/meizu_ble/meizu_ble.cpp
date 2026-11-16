#include "meizu_ble.h"
#include "esphome/components/md5/md5.h"
#include "esphome/core/log.h"
#include <algorithm>
#include <cmath>
#include <cstring>

namespace esphome::meizu_ble {
namespace {
const char *const TAG = "meizu_ble";
bool reached(uint32_t now, uint32_t deadline) { return int32_t(now - deadline) >= 0; }
const char *operation_name(Operation op) { return op == Operation::SEND ? "send" : op == Operation::LEARN ? "learn" : "poll"; }
void publish(sensor::Sensor *sensor, float value) { if (sensor != nullptr) sensor->publish_state(value); }
}

void MeizuBLE::setup() {
  this->parent()->set_auto_connect(false);
  this->next_poll_ = millis() + 1000;
  this->invalidate_();
#ifdef USE_MEIZU_BLE_API
  if (!this->api_service_.empty())
    this->register_service(&MeizuBLE::api_command_, this->api_service_, {"request_id", "operation", "key", "ir_code", "timeout", "device"});
#endif
}

void MeizuBLE::dump_config() {
  ESP_LOGCONFIG(TAG, "Meizu R16 BLE client");
  LOG_UPDATE_INTERVAL(this);
  LOG_SENSOR("  ", "Temperature", this->temperature_sensor_);
  LOG_SENSOR("  ", "Humidity", this->humidity_sensor_);
  LOG_SENSOR("  ", "Battery", this->battery_sensor_);
  LOG_SENSOR("  ", "Voltage", this->voltage_sensor_);
}

void MeizuBLE::invalidate_() {
  this->sample_valid_ = false;
  publish(this->temperature_sensor_, NAN); publish(this->humidity_sensor_, NAN);
  publish(this->battery_sensor_, NAN); publish(this->voltage_sensor_, NAN); publish(this->rssi_sensor_, NAN);
  if (this->available_sensor_) this->available_sensor_->publish_state(false);
}

void MeizuBLE::update() {
  // loop() owns failed-poll retries; periodic refreshes must preserve their deadline.
  if (this->failures_ == 0) this->next_poll_ = millis();
}

bool MeizuBLE::accept_(const std::string &request_id, Operation operation) {
  if (request_id.empty() || request_id.size() > 64) return false;
  if (request_id == this->active_id_ || request_id == this->pending_id_) return false;
  if (std::find(this->completed_.begin(), this->completed_.end(), request_id) != this->completed_.end()) {
    this->reject_(request_id, operation, "duplicate_request"); return false;
  }
  if (this->queued_ != Operation::NONE || (this->active_ != Operation::NONE && this->active_ != Operation::POLL)) {
    this->reject_(request_id, operation, "busy"); return false;
  }
  this->pending_id_ = request_id; this->pending_deadline_ = millis() + 15000; this->queued_ = operation;
  return true;
}

void MeizuBLE::send(const std::string &request_id, const std::string &key, const std::string &code) {
  // Parse into temporary key and validate the full input before changing an active request.
  std::array<uint8_t, 9> decoded{}; size_t length = 0;
  if (!parse_hex(key, decoded.data(), decoded.size(), length) || length != 9 || code.size() > MAX_IR_CODE * 2 || code.size() % 2 ||
      (!code.empty() && size_t(decoded[0] | (decoded[1] << 8)) != code.size() / 2) ||
      !std::all_of(code.begin(), code.end(), [](char c) { return (c >= '0' && c <= '9') || (c >= 'a' && c <= 'f') || (c >= 'A' && c <= 'F'); })) {
    this->reject_(request_id, Operation::SEND, "invalid_parameters"); return;
  }
  if (!this->accept_(request_id, Operation::SEND)) return;
  this->request_key_ = decoded;
  parse_hex(code, this->request_code_.data(), this->request_code_.size(), this->request_code_length_);
}

void MeizuBLE::learn(const std::string &request_id, const std::string &device, uint32_t timeout) {
  uint8_t digest[16]{}; std::array<uint8_t, 9> key{};
  if (timeout < 1 || timeout > 15 || !make_cache_key(1, digest, device, key)) {
    this->reject_(request_id, Operation::LEARN, "invalid_parameters"); return;
  }
  if (!this->accept_(request_id, Operation::LEARN)) return;
  this->device_ = device; this->learning_timeout_ = timeout * 1000;
  MeizuEvent event; event.request_id = request_id; event.operation = "learn"; event.phase = "queued"; this->emit_(event);
}

void MeizuBLE::cancel(const std::string &request_id) {
  if (this->queued_ == Operation::LEARN && this->pending_id_ == request_id) {
    this->queued_ = Operation::NONE; this->pending_id_.clear();
    this->completed_[this->completed_index_++ % this->completed_.size()] = request_id;
    this->reject_(request_id, Operation::LEARN, "cancelled");
  } else if (this->active_ == Operation::LEARN && this->active_id_ == request_id) {
    if (this->link_ == Link::ACTIVE) this->protocol_.cancel(millis());
    else this->finish_("cancelled");
  }
}

void MeizuBLE::start_(Operation operation) {
  this->active_ = operation; this->active_id_ = operation == Operation::POLL ? "" : this->pending_id_;
  this->start_deadline_ = operation == Operation::POLL ? millis() + 15000 : this->pending_deadline_;
  if (operation != Operation::POLL) { this->queued_ = Operation::NONE; this->pending_id_.clear(); }
  this->link_ = Link::CONNECTING; this->connect_started_ = false; this->rssi_valid_ = false;
  this->characteristic_ = this->descriptor_ = 0; this->inbox_head_ = this->inbox_size_ = 0;
  if (operation == Operation::LEARN) {
    MeizuEvent event; event.request_id = this->active_id_; event.operation = "learn"; event.phase = "connecting"; this->emit_(event);
  }
}

void MeizuBLE::connected_() {
  if (reached(millis(), this->start_deadline_)) { this->finish_("queue_timeout"); return; }
  this->node_state = esp32_ble_tracker::ClientState::ESTABLISHED;
  this->link_ = Link::ACTIVE; this->writing_ = this->reading_ = false;
  this->sequence_ = this->sequence_ >= 253 ? 1 : this->sequence_ + 2;
  const uint32_t now = millis();
  this->last_fallback_read_ = now;
  bool started = this->active_ == Operation::POLL ? this->protocol_.start_poll(this->sequence_, now) :
      this->active_ == Operation::SEND ? this->protocol_.start_send(this->sequence_, now, this->request_key_.data(), 9,
          this->request_code_.data(), this->request_code_length_) :
          this->protocol_.start_learn(this->sequence_, now, this->learning_timeout_);
  if (!started) this->finish_("invalid_parameters");
  else esp_ble_gap_read_rssi(this->parent()->get_remote_bda());
}

void MeizuBLE::publish_measurement_() {
  this->temperature_ = this->protocol_.temperature(); this->humidity_ = this->protocol_.humidity();
  this->voltage_ = this->protocol_.voltage() / 10.0f;
  uint8_t voltage = this->protocol_.voltage();
  this->battery_ = voltage >= 30 ? 100 : voltage == 29 ? 90 : voltage == 28 ? 80 : voltage == 27 ? 60 : voltage == 26 ? 30 : 10;
  this->sample_at_ = millis(); this->sample_valid_ = true; this->failures_ = 0;
  publish(this->temperature_sensor_, this->temperature_); publish(this->humidity_sensor_, this->humidity_);
  publish(this->battery_sensor_, this->battery_); publish(this->voltage_sensor_, this->voltage_);
  publish(this->rssi_sensor_, this->rssi_valid_ ? this->rssi_ : NAN); publish(this->sample_age_sensor_, 0);
  if (this->available_sensor_) this->available_sensor_->publish_state(true);
}

void MeizuBLE::finish_(const char *reason) {
  if (this->active_ == Operation::NONE) return;
  const Operation operation = this->active_;
  MeizuEvent event; event.request_id = this->active_id_; event.operation = operation_name(operation);
  event.status = reason != nullptr ? reason : this->protocol_.status();
  event.phase = event.status == "ok" ? "complete" : event.status == "cancelled" ? "cancelled" : "failed";
  if (event.status == "ok" && operation == Operation::LEARN) {
    // Preserve a complete capture for diagnostics even when conversion rejects it.
    event.received = event.length = this->protocol_.received();
    event.raw = hex_string(this->protocol_.raw(), event.length);
    uint32_t frequency = 0; size_t length = 0;
    if (!convert_capture(this->protocol_.raw(), this->protocol_.received(), this->request_code_, length, frequency)) {
      event.status = "unsupported_capture"; event.phase = "failed";
    } else {
      md5::MD5Digest digest; digest.init(); digest.add(this->request_code_.data(), length); digest.calculate();
      uint8_t bytes[16]; digest.get_bytes(bytes);
      std::array<uint8_t, 9> key{};
      if (!make_cache_key(length, bytes, this->device_, key)) { event.status = "invalid_parameters"; event.phase = "failed"; }
      else {
        event.ir_code = hex_string(this->request_code_.data(), length); event.key = hex_string(key.data(), key.size());
        event.carrier_hz = frequency;
      }
    }
  }
  if (event.status == "ok" && operation == Operation::SEND) {
    event.confirmed = this->protocol_.confirmed(); event.ack = this->protocol_.ack(); event.cached = event.ack == 4;
    if (!event.confirmed) { event.status = "invalid_ack"; event.phase = "failed"; }
  }
  if (event.status == "ok" && operation == Operation::POLL) this->publish_measurement_();
  const uint32_t now = millis();
  if (event.status != "ok" && event.status != "cancelled" && event.status != "unsupported_capture") {
    this->invalidate_(); this->failures_ = std::min<uint32_t>(this->failures_ + 1, 9);
  }
  if (operation == Operation::POLL || (event.status != "ok" && event.status != "cancelled" && event.status != "unsupported_capture"))
    this->next_poll_ = now + (this->failures_ ? std::min<uint32_t>(5000u << (this->failures_ - 1), 900000) : this->get_update_interval());
  else if (!this->sample_valid_ && event.status == "ok")
    this->next_poll_ = now + 1000;
  // Successful control traffic does not postpone an already scheduled sample.
  if (!this->active_id_.empty()) this->completed_[this->completed_index_++ % this->completed_.size()] = this->active_id_;
  this->active_ = Operation::NONE; this->active_id_.clear(); this->protocol_.reset();
  this->writing_ = this->reading_ = false; this->inbox_size_ = 0;
  this->link_ = Link::CLOSING; this->close_deadline_ = now + 10000; this->parent()->disconnect();
  // Clear ownership before callbacks so a user's on_event automation can queue its next operation.
  this->emit_(event);
}

void MeizuBLE::reject_(const std::string &id, Operation op, const char *reason) {
  if (id.empty() || id.size() > 64) return;
  MeizuEvent event; event.request_id = id; event.operation = operation_name(op); event.status = reason;
  event.phase = std::strcmp(reason, "cancelled") ? "failed" : "cancelled"; this->emit_(event);
}

void MeizuBLE::enqueue_(const uint8_t *data, size_t length) {
  if (length < 4) return;
  if (length > 20 || this->inbox_size_ == this->inbox_.size()) { this->finish_("receive_overflow"); return; }
  auto &frame = this->inbox_[(this->inbox_head_ + this->inbox_size_) % this->inbox_.size()];
  std::copy(data, data + length, frame.bytes.begin()); frame.length = length; this->inbox_size_++;
}

void MeizuBLE::loop() {
  const uint32_t now = millis();
  if (this->sample_valid_ && uint32_t(now - this->sample_at_) > 2 * this->get_update_interval() + 90000) this->invalidate_();
  if (uint32_t(now - this->last_age_publish_) >= 30000) {
    publish(this->sample_age_sensor_, this->sample_valid_ ? (now - this->sample_at_) / 1000.0f : NAN); this->last_age_publish_ = now;
  }
  if (this->queued_ != Operation::NONE && reached(now, this->pending_deadline_)) {
    auto id = this->pending_id_; auto op = this->queued_; this->queued_ = Operation::NONE; this->pending_id_.clear();
    this->completed_[this->completed_index_++ % this->completed_.size()] = id; this->reject_(id, op, "queue_timeout");
  }
  if (this->link_ == Link::CLOSING) {
    if (this->parent()->state() == esp32_ble_tracker::ClientState::IDLE) this->link_ = Link::IDLE;
    else { if (reached(now, this->close_deadline_)) { this->parent()->disconnect(); this->close_deadline_ = now + 10000; } return; }
  }
  if (this->link_ == Link::IDLE) {
    if (this->queued_ != Operation::NONE) this->start_(this->queued_);
    else if (reached(now, this->next_poll_)) this->start_(Operation::POLL);
    else return;
  }
  if (this->link_ == Link::CONNECTING || this->link_ == Link::SUBSCRIBING) {
    if (reached(now, this->start_deadline_)) { this->finish_("connect_timeout"); return; }
    if (this->link_ == Link::CONNECTING && !this->connect_started_ && this->parent()->get_gattc_if() != ESP_GATT_IF_NONE &&
        this->parent()->state() == esp32_ble_tracker::ClientState::IDLE) {
      this->connect_started_ = true; this->parent()->connect();
    }
    return;
  }
  if (this->link_ != Link::ACTIVE) return;
  if (this->writing_ && reached(now, this->write_deadline_)) { this->finish_("write_timeout"); return; }
  if (this->reading_ && reached(now, this->read_deadline_)) { this->finish_("read_timeout"); return; }
  this->protocol_.tick(now);
  for (unsigned n = 0; n < 4 && this->inbox_size_ && !this->writing_ && !this->protocol_.has_write(); n++) {
    auto &frame = this->inbox_[this->inbox_head_]; auto phase = this->protocol_.phase(); auto received = this->protocol_.received();
    this->protocol_.receive(frame.bytes.data(), frame.length, now);
    this->inbox_head_ = (this->inbox_head_ + 1) % this->inbox_.size(); this->inbox_size_--;
    if (this->active_ == Operation::LEARN && ((phase == Phase::READY && this->protocol_.phase() == Phase::CAPTURE) ||
        received != this->protocol_.received() || (phase == Phase::CAPTURE && this->protocol_.expected() > 0 && received == 0))) {
      MeizuEvent event; event.request_id = this->active_id_; event.operation = "learn";
      event.phase = phase == Phase::READY ? "ready" : "receiving"; event.timeout_ms = this->learning_timeout_;
      event.received = this->protocol_.received(); event.length = this->protocol_.expected(); this->emit_(event);
    }
  }
  if (this->protocol_.done()) { this->finish_(); return; }
  if (!this->writing_ && !this->reading_ && this->protocol_.has_write()) {
    auto &frame = this->protocol_.frame();
    this->writing_ = true; this->write_deadline_ = now + 5000;
    auto result = esp_ble_gattc_write_char(this->parent()->get_gattc_if(), this->parent()->get_conn_id(), this->characteristic_,
        frame.length, const_cast<uint8_t *>(frame.bytes.data()), ESP_GATT_WRITE_TYPE_RSP, ESP_GATT_AUTH_REQ_NONE);
    if (result != ESP_OK) { this->writing_ = false; this->protocol_.write_complete(false, now); }
  } else if (!this->writing_ && !this->reading_ && (this->protocol_.needs_read() ||
      (uint32_t(now - this->last_fallback_read_) >= 250 && (this->protocol_.phase() == Phase::READY ||
       this->protocol_.phase() == Phase::CACHE || this->protocol_.phase() == Phase::UPLOAD_ACK)))) {
    // Reading the last reply recovers a lost notification without retransmitting an IR command.
    this->last_fallback_read_ = now;
    this->reading_ = true; this->read_deadline_ = now + 3000;
    if (esp_ble_gattc_read_char(this->parent()->get_gattc_if(), this->parent()->get_conn_id(), this->characteristic_, ESP_GATT_AUTH_REQ_NONE) != ESP_OK) {
      this->reading_ = false; this->finish_("read_failed");
    }
  }
}

void MeizuBLE::gattc_event_handler(esp_gattc_cb_event_t event, esp_gatt_if_t interface, esp_ble_gattc_cb_param_t *param) {
  if (interface != this->parent()->get_gattc_if()) return;
  switch (event) {
    case ESP_GATTC_OPEN_EVT:
      if (this->link_ == Link::CONNECTING && param->open.status != ESP_GATT_OK) this->finish_("connect_failed");
      break;
    case ESP_GATTC_SEARCH_CMPL_EVT: {
      if (this->link_ != Link::CONNECTING || param->search_cmpl.conn_id != this->parent()->get_conn_id()) return;
      auto *characteristic = this->parent()->get_characteristic(0x16f0, 0x16f2);
      if (param->search_cmpl.status != ESP_GATT_OK || characteristic == nullptr) { this->finish_("characteristic_missing"); return; }
      this->characteristic_ = characteristic->handle;
      if (this->active_ == Operation::POLL) { this->connected_(); return; }
      auto *descriptor = this->parent()->get_descriptor(0x16f0, 0x16f2, 0x2902);
      if (descriptor == nullptr) { this->finish_("subscribe_failed"); return; }
      this->descriptor_ = descriptor->handle; this->node_state = esp32_ble_tracker::ClientState::CONNECTED;
      this->link_ = Link::SUBSCRIBING;
      if (this->parent()->register_for_notify(this->characteristic_) != ESP_OK) this->finish_("subscribe_failed");
      break;
    }
    case ESP_GATTC_REG_FOR_NOTIFY_EVT:
      if (this->link_ == Link::SUBSCRIBING && param->reg_for_notify.handle == this->characteristic_ && param->reg_for_notify.status != ESP_GATT_OK)
        this->finish_("subscribe_failed");
      break;
    case ESP_GATTC_WRITE_DESCR_EVT:
      if (this->link_ == Link::SUBSCRIBING && param->write.conn_id == this->parent()->get_conn_id() && param->write.handle == this->descriptor_) {
        if (param->write.status == ESP_GATT_OK) this->connected_(); else this->finish_("subscribe_failed");
      }
      break;
    case ESP_GATTC_WRITE_CHAR_EVT:
      if (this->link_ == Link::ACTIVE && this->writing_ && param->write.conn_id == this->parent()->get_conn_id() && param->write.handle == this->characteristic_) {
        this->writing_ = false; this->protocol_.write_complete(param->write.status == ESP_GATT_OK, millis());
      }
      break;
    case ESP_GATTC_READ_CHAR_EVT:
      if (this->link_ == Link::ACTIVE && this->reading_ && param->read.conn_id == this->parent()->get_conn_id() && param->read.handle == this->characteristic_) {
        this->reading_ = false;
        if (param->read.status != ESP_GATT_OK) this->finish_("read_failed");
        else this->enqueue_(param->read.value, param->read.value_len);
      }
      break;
    case ESP_GATTC_NOTIFY_EVT:
      if (this->link_ == Link::ACTIVE && param->notify.conn_id == this->parent()->get_conn_id() && param->notify.handle == this->characteristic_)
        this->enqueue_(param->notify.value, param->notify.value_len);
      break;
    case ESP_GATTC_DISCONNECT_EVT:
      if (std::memcmp(param->disconnect.remote_bda, this->parent()->get_remote_bda(), 6) != 0) return;
      if (this->active_ != Operation::NONE) this->finish_("disconnected");
      this->link_ = Link::CLOSING;
      break;
    default: break;
  }
}

void MeizuBLE::gap_event_handler(esp_gap_ble_cb_event_t event, esp_ble_gap_cb_param_t *param) {
  if (event == ESP_GAP_BLE_READ_RSSI_COMPLETE_EVT && this->link_ == Link::ACTIVE && param->read_rssi_cmpl.status == ESP_BT_STATUS_SUCCESS &&
      std::memcmp(param->read_rssi_cmpl.remote_addr, this->parent()->get_remote_bda(), 6) == 0) {
    this->rssi_ = param->read_rssi_cmpl.rssi; this->rssi_valid_ = true;
  }
}

void MeizuBLE::status(const std::string &request_id) {
  if (request_id.empty() || request_id.size() > 64) return;
  MeizuEvent event; event.request_id = request_id; event.operation = "status"; event.phase = "complete"; event.status = "ok"; this->emit_(event);
}

void MeizuBLE::api_command_(std::string id, std::string operation, std::string key, std::string code, int32_t timeout, std::string device) {
  if (operation == "status") this->status(id);
  else if (operation == "send") this->send(id, key, code);
  else if (operation == "learn") this->learn(id, device, timeout);
  else if (operation == "cancel") this->cancel(id);
}

void MeizuBLE::emit_(const MeizuEvent &event) {
#ifdef USE_MEIZU_BLE_API
  if (!this->api_service_.empty()) {
    std::map<std::string, std::string> data{{"protocol", "meizu_ble_v1"}, {"source", this->api_service_}, {"address", this->parent()->address_str()},
      {"request_id", event.request_id}, {"operation", event.operation}, {"phase", event.phase}, {"status", event.status},
      {"confirmed", event.confirmed ? "1" : "0"}, {"cached", event.cached ? "1" : "0"}, {"ack", std::to_string(event.ack)},
      {"received", std::to_string(event.received)}, {"length", std::to_string(event.length)}, {"timeout_ms", std::to_string(event.timeout_ms)},
      {"version", "0.1.0-esphome"}, {"update_interval", std::to_string(this->get_update_interval() / 60000)},
      {"available", this->sample_valid_ ? "1" : "0"}, {"measurement_age_ms", this->sample_valid_ ? std::to_string(millis() - this->sample_at_) : "-1"}};
    if (this->sample_valid_) {
      data["temperature"] = std::to_string(this->temperature_); data["humidity"] = std::to_string(this->humidity_);
      data["voltage"] = std::to_string(this->voltage_); data["battery"] = std::to_string(this->battery_);
      if (this->rssi_valid_) data["rssi"] = std::to_string(this->rssi_);
    }
    if (!event.raw.empty()) { data["raw"] = event.raw; data["key"] = event.key; data["ir_code"] = event.ir_code; data["carrier_hz"] = std::to_string(event.carrier_hz); }
    this->fire_homeassistant_event("esphome.meizu_ble", data);
  }
#endif
  this->event_callback_.call(event);
}
}  // namespace esphome::meizu_ble
