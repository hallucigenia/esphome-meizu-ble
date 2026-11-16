#pragma once
namespace esphome::binary_sensor {
class BinarySensor {
 public:
  bool state{false};
  void publish_state(bool value) { state = value; }
};
}
