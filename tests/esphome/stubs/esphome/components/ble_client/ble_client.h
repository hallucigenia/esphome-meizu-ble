#pragma once
#include "esphome/core/component.h"
#include <cstddef>
#include <cstdint>
#include <cstring>
using esp_gatt_if_t = int;
using esp_err_t = int;
using esp_bd_addr_t = uint8_t[6];
constexpr int ESP_OK = 0, ESP_GATT_OK = 0, ESP_GATT_IF_NONE = -1, ESP_GATT_WRITE_TYPE_RSP = 1, ESP_GATT_AUTH_REQ_NONE = 0;
constexpr int ESP_BT_STATUS_SUCCESS = 0;
enum esp_gattc_cb_event_t { ESP_GATTC_OPEN_EVT, ESP_GATTC_SEARCH_CMPL_EVT, ESP_GATTC_REG_FOR_NOTIFY_EVT,
  ESP_GATTC_WRITE_DESCR_EVT, ESP_GATTC_WRITE_CHAR_EVT, ESP_GATTC_READ_CHAR_EVT, ESP_GATTC_NOTIFY_EVT, ESP_GATTC_DISCONNECT_EVT };
enum esp_gap_ble_cb_event_t { ESP_GAP_BLE_READ_RSSI_COMPLETE_EVT };
struct esp_ble_gattc_cb_param_t {
  struct { int status{0}; } open;
  struct { int status{0}; uint16_t conn_id{7}; } search_cmpl;
  struct { int status{0}; uint16_t handle{10}; } reg_for_notify;
  struct { int status{0}; uint16_t conn_id{7}, handle{10}; } write;
  struct { int status{0}; uint16_t conn_id{7}, handle{10}, value_len{0}; uint8_t *value{nullptr}; } read;
  struct { uint16_t conn_id{7}, handle{10}, value_len{0}; uint8_t *value{nullptr}; } notify;
  struct { esp_bd_addr_t remote_bda{2,0,0,0,0,1}; } disconnect;
};
struct esp_ble_gap_cb_param_t { struct { int status{0}, rssi{-40}; esp_bd_addr_t remote_addr{2,0,0,0,0,1}; } read_rssi_cmpl; };
esp_err_t esp_ble_gattc_write_char(int, uint16_t, uint16_t, uint16_t, uint8_t *, int, int);
esp_err_t esp_ble_gattc_read_char(int, uint16_t, uint16_t, int);
inline esp_err_t esp_ble_gap_read_rssi(uint8_t *) { return ESP_OK; }
namespace esphome::esp32_ble_tracker { enum class ClientState { INIT, IDLE, CONNECTING, CONNECTED, ESTABLISHED, DISCONNECTING }; }
namespace esphome::ble_client {
struct Characteristic { uint16_t handle{10}; };
struct Descriptor { uint16_t handle{12}; };
class BLEClient {
 public:
  void set_auto_connect(bool value) { auto_connect = value; }
  int get_gattc_if() const { return interface; }
  uint16_t get_conn_id() const { return connection; }
  uint8_t *get_remote_bda() { return address; }
  const char *address_str() { return "02:00:00:00:00:01"; }
  esp32_ble_tracker::ClientState state() const { return current; }
  void connect() { current = esp32_ble_tracker::ClientState::CONNECTING; connections++; }
  void disconnect() { if (current != esp32_ble_tracker::ClientState::IDLE) current = esp32_ble_tracker::ClientState::DISCONNECTING; }
  Characteristic *get_characteristic(uint16_t, uint16_t) { return has_characteristic ? &characteristic : nullptr; }
  Descriptor *get_descriptor(uint16_t, uint16_t, uint16_t) { return has_descriptor ? &descriptor : nullptr; }
  int register_for_notify(uint16_t) { subscriptions++; return subscribe_status; }
  int interface{1}, connections{0}, subscriptions{0}, subscribe_status{0};
  uint16_t connection{7};
  bool auto_connect{true}, has_characteristic{true}, has_descriptor{true};
  uint8_t address[6]{2,0,0,0,0,1};
  esp32_ble_tracker::ClientState current{esp32_ble_tracker::ClientState::IDLE};
  Characteristic characteristic;
  Descriptor descriptor;
};
class BLEClientNode {
 public:
  virtual ~BLEClientNode() = default;
  virtual void loop() {}
  virtual void gattc_event_handler(esp_gattc_cb_event_t, esp_gatt_if_t, esp_ble_gattc_cb_param_t *) {}
  virtual void gap_event_handler(esp_gap_ble_cb_event_t, esp_ble_gap_cb_param_t *) {}
  BLEClient *parent() { return parent_; }
  void set_ble_client_parent(BLEClient *parent) { parent_ = parent; }
  esp32_ble_tracker::ClientState node_state{esp32_ble_tracker::ClientState::IDLE};
 protected:
  BLEClient *parent_{nullptr};
};
}
