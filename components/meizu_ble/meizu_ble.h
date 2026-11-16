#pragma once
#include "protocol.h"
#include "esphome/core/component.h"
#include "esphome/core/automation.h"
#include "esphome/components/ble_client/ble_client.h"
#include "esphome/components/sensor/sensor.h"
#include "esphome/components/binary_sensor/binary_sensor.h"
#ifdef USE_MEIZU_BLE_API
#include "esphome/components/api/custom_api_device.h"
#endif

namespace esphome::meizu_ble {

struct MeizuEvent {
  std::string request_id, operation, phase, status, raw, key, ir_code;
  bool confirmed{false}, cached{false};
  uint8_t ack{0};
  uint32_t received{0}, length{0}, timeout_ms{0}, carrier_hz{0};
};

class MeizuBLE : public PollingComponent, public ble_client::BLEClientNode
#ifdef USE_MEIZU_BLE_API
    , public api::CustomAPIDevice
#endif
{
 public:
  void setup() override;
  void dump_config() override;
  void loop() override;
  void update() override;
  float get_setup_priority() const override { return setup_priority::DATA; }
  void gattc_event_handler(esp_gattc_cb_event_t event, esp_gatt_if_t interface, esp_ble_gattc_cb_param_t *param) override;
  void gap_event_handler(esp_gap_ble_cb_event_t event, esp_ble_gap_cb_param_t *param) override;
  void send(const std::string &request_id, const std::string &key, const std::string &ir_code);
  void learn(const std::string &request_id, const std::string &device, uint32_t timeout);
  void cancel(const std::string &request_id);
  void status(const std::string &request_id);
  void set_api_service(const std::string &name) { this->api_service_ = name; }
  void set_temperature_sensor(sensor::Sensor *value) { this->temperature_sensor_ = value; }
  void set_humidity_sensor(sensor::Sensor *value) { this->humidity_sensor_ = value; }
  void set_battery_sensor(sensor::Sensor *value) { this->battery_sensor_ = value; }
  void set_voltage_sensor(sensor::Sensor *value) { this->voltage_sensor_ = value; }
  void set_rssi_sensor(sensor::Sensor *value) { this->rssi_sensor_ = value; }
  void set_sample_age_sensor(sensor::Sensor *value) { this->sample_age_sensor_ = value; }
  void set_available_sensor(binary_sensor::BinarySensor *value) { this->available_sensor_ = value; }
  void add_on_event_callback(std::function<void(const MeizuEvent &)> &&callback) { this->event_callback_.add(std::move(callback)); }

 protected:
  enum class Link : uint8_t { IDLE, CONNECTING, SUBSCRIBING, ACTIVE, CLOSING };
  bool accept_(const std::string &request_id, Operation operation);
  void start_(Operation operation);
  void connected_();
  void finish_(const char *reason = nullptr);
  void emit_(const MeizuEvent &event);
  void reject_(const std::string &request_id, Operation operation, const char *reason);
  void enqueue_(const uint8_t *data, size_t length);
  void publish_measurement_();
  void invalidate_();
  void api_command_(std::string request_id, std::string operation, std::string key,
                    std::string ir_code, int32_t timeout, std::string device);
  Protocol protocol_;
  std::array<Frame, 16> inbox_{};
  size_t inbox_head_{0}, inbox_size_{0};
  std::array<uint8_t, 9> request_key_{};
  std::array<uint8_t, MAX_IR_CODE> request_code_{};
  size_t request_code_length_{0};
  std::array<std::string, 8> completed_{};
  size_t completed_index_{0};
  std::string pending_id_, active_id_, device_, api_service_;
  Operation queued_{Operation::NONE}, active_{Operation::NONE};
  Link link_{Link::IDLE};
  uint32_t start_deadline_{0}, pending_deadline_{0}, write_deadline_{0}, read_deadline_{0}, close_deadline_{0};
  uint32_t next_poll_{0}, sample_at_{0}, last_age_publish_{0}, learning_timeout_{15000};
  uint32_t last_fallback_read_{0};
  uint32_t failures_{0};
  uint16_t characteristic_{0}, descriptor_{0};
  uint8_t sequence_{0};
  int rssi_{-127};
  float temperature_{NAN}, humidity_{NAN}, voltage_{NAN}, battery_{NAN};
  bool writing_{false}, reading_{false}, connect_started_{false}, sample_valid_{false}, rssi_valid_{false};
  CallbackManager<void(const MeizuEvent &)> event_callback_;
  sensor::Sensor *temperature_sensor_{nullptr}, *humidity_sensor_{nullptr}, *battery_sensor_{nullptr};
  sensor::Sensor *voltage_sensor_{nullptr}, *rssi_sensor_{nullptr}, *sample_age_sensor_{nullptr};
  binary_sensor::BinarySensor *available_sensor_{nullptr};
};

class MeizuEventTrigger : public Trigger<const MeizuEvent &> {
 public:
  explicit MeizuEventTrigger(MeizuBLE *parent) {
    parent->add_on_event_callback([this](const MeizuEvent &event) { this->trigger(event); });
  }
};
}  // namespace esphome::meizu_ble
