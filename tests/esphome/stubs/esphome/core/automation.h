#pragma once
#include "component.h"
namespace esphome {
template<typename... Ts> class Trigger {
 public:
  void trigger(Ts...) {}
};
}
