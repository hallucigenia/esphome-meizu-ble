#pragma once
#include <array>
#include <map>
#include <string>
#include <vector>
namespace esphome::api {
extern std::vector<std::map<std::string, std::string>> test_events;
class CustomAPIDevice {
 public:
  template<typename T, typename... Ts>
  void register_service(void (T::*)(Ts...), const std::string &, const std::array<std::string, sizeof...(Ts)> &) {}
  void fire_homeassistant_event(const std::string &name, const std::map<std::string, std::string> &data) {
    if (name == "esphome.meizu_ble") test_events.push_back(data);
  }
};
}
