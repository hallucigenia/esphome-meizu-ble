#pragma once
#include "meizu_ble.h"

namespace esphome::meizu_ble {
template<typename... Ts> class SendAction : public Action<Ts...> {
 public:
  explicit SendAction(MeizuBLE *parent) : parent_(parent) {}
  TEMPLATABLE_VALUE(std::string, request_id)
  TEMPLATABLE_VALUE(std::string, key)
  TEMPLATABLE_VALUE(std::string, ir_code)
  void play(Ts... x) override { this->parent_->send(this->request_id_.value(x...), this->key_.value(x...), this->ir_code_.value(x...)); }
 protected:
  MeizuBLE *parent_;
};
template<typename... Ts> class LearnAction : public Action<Ts...> {
 public:
  explicit LearnAction(MeizuBLE *parent) : parent_(parent) {}
  TEMPLATABLE_VALUE(std::string, request_id)
  TEMPLATABLE_VALUE(std::string, device)
  TEMPLATABLE_VALUE(uint32_t, timeout)
  void play(Ts... x) override { this->parent_->learn(this->request_id_.value(x...), this->device_.value(x...), this->timeout_.value(x...)); }
 protected:
  MeizuBLE *parent_;
};
template<typename... Ts> class CancelAction : public Action<Ts...> {
 public:
  explicit CancelAction(MeizuBLE *parent) : parent_(parent) {}
  TEMPLATABLE_VALUE(std::string, request_id)
  void play(Ts... x) override { this->parent_->cancel(this->request_id_.value(x...)); }
 protected:
  MeizuBLE *parent_;
};
}  // namespace esphome::meizu_ble
