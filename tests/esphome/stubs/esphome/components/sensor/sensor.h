#pragma once
namespace esphome::sensor {
class Sensor {
 public:
  float state{0};
  void publish_state(float value) { state = value; }
};
}
