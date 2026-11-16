#pragma once
#include <cmath>
#include <cstdint>
#include <functional>
#include <vector>
namespace esphome {
extern uint32_t test_now;
inline uint32_t millis() { return test_now; }
namespace setup_priority { constexpr float DATA = 600; }
class Component {
 public:
  virtual ~Component() = default;
  virtual void setup() {}
  virtual void loop() {}
  virtual void dump_config() {}
  virtual float get_setup_priority() const { return 0; }
};
class PollingComponent : public Component {
 public:
  virtual void update() {}
  uint32_t get_update_interval() const { return interval_; }
  void set_update_interval(uint32_t value) { interval_ = value; }
 protected:
  uint32_t interval_{300000};
};
template<typename> class CallbackManager;
template<typename... Ts> class CallbackManager<void(Ts...)> {
 public:
  void add(std::function<void(Ts...)> &&fn) { callbacks_.push_back(std::move(fn)); }
  void call(Ts... values) { for (auto &fn : callbacks_) fn(values...); }
 protected:
  std::vector<std::function<void(Ts...)>> callbacks_;
};
}
