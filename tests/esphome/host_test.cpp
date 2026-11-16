#include "meizu_ble.h"
#include <cassert>
#include <iostream>
#include <sstream>
#include <vector>

using namespace esphome::meizu_ble;
namespace esphome { uint32_t test_now = 0; }
namespace esphome::api { std::vector<std::map<std::string, std::string>> test_events; }
std::vector<std::vector<uint8_t>> writes;
unsigned reads = 0;
int write_error = 0;
esp_err_t esp_ble_gattc_write_char(int, uint16_t, uint16_t, uint16_t length, uint8_t *data, int, int) {
  writes.emplace_back(data, data + length); return write_error;
}
esp_err_t esp_ble_gattc_read_char(int, uint16_t, uint16_t, int) { reads++; return ESP_OK; }
std::vector<uint8_t> decode(const std::string &text) {
  std::vector<uint8_t> out(text.size() / 2); size_t length = 0;
  assert(parse_hex(text, out.data(), out.size(), length)); return out;
}
std::vector<uint8_t> frame(uint8_t seq, uint8_t cmd, std::vector<uint8_t> payload = {}) {
  std::vector<uint8_t> out{0x55, uint8_t(3 + payload.size()), seq, cmd}; out.insert(out.end(), payload.begin(), payload.end()); return out;
}
void deliver(Protocol &protocol, const std::vector<uint8_t> &bytes) { protocol.receive(bytes.data(), bytes.size(), esphome::test_now); }
void ack(Protocol &p) { p.write_complete(true, esphome::test_now); }

void protocol_tests(const std::vector<uint8_t> &raw) {
  uint8_t key[9] = {30}; uint8_t code[30]{};
  Protocol p;
  assert(p.start_send(1,0,key,9,code,30)); assert(p.frame().bytes[3] == 3); ack(p);
  deliver(p,frame(2,4,{1})); assert(!p.done()); deliver(p,frame(1,4,{0}));
  std::vector<size_t> sizes;
  while (p.has_write()) { sizes.push_back(p.frame().length); ack(p); }
  assert((sizes == std::vector<size_t>{15,20,20})); assert(!p.confirmed());
  deliver(p,frame(1,1,{2})); assert(p.has_write() && p.frame().bytes[4] == 2); ack(p);
  deliver(p,frame(1,2)); assert(p.done() && p.confirmed() && p.ack() == 2);
  assert(p.start_send(3,0,key,9,nullptr,0)); ack(p); deliver(p,frame(3,4,{0})); assert(p.done() && !p.confirmed());
  assert(p.start_send(4,0,key,9,nullptr,0)); ack(p); deliver(p,frame(4,4,{1})); assert(p.done() && p.ack() == 4);
  assert(p.start_send(5,0,key,9,nullptr,0)); ack(p); deliver(p,frame(5,4,{2})); assert(p.done() && !p.confirmed());
  assert(p.start_send(6,0,key,9,nullptr,0)); p.write_complete(false,0); assert(p.done() && !p.confirmed());
  assert(p.start_send(7,0,key,9,nullptr,0)); ack(p); p.tick(45000); assert(p.done() && !p.has_write());

  assert(p.start_learn(8,0,15000)); ack(p); deliver(p,frame(8,9,{0,1,10,0})); assert(p.phase() == Phase::READY);
  deliver(p,frame(8,7)); assert(p.phase() == Phase::CAPTURE);
  deliver(p,frame(8,9,{0,uint8_t(1+(raw.size()+14)/15),uint8_t(raw.size()),uint8_t(raw.size()>>8)}));
  deliver(p,frame(8,9,{2,0})); assert(p.frame().bytes[3] == 0x0a && p.frame().bytes[4] == 1); ack(p);
  for (size_t offset=0,index=1; offset<raw.size(); offset+=15,index++) {
    std::vector<uint8_t> payload{uint8_t(index)}; payload.insert(payload.end(),raw.begin()+offset,raw.begin()+std::min(raw.size(),offset+15));
    deliver(p,frame(8,9,payload));
  }
  assert(p.frame().bytes[3] == 0x0b && !p.done()); ack(p); assert(p.frame().bytes[3] == 6); ack(p);
  assert(p.done() && std::string(p.status()) == "ok" && p.received() == raw.size());
  assert(p.start_learn(9,0,1000)); p.cancel(1); ack(p); assert(p.frame().bytes[3] == 6); ack(p);
  assert(p.done() && std::string(p.status()) == "cancelled" && !p.received());
  assert(p.start_learn(10,0,1000)); ack(p); deliver(p,frame(10,7)); esphome::test_now=900;
  deliver(p,frame(10,7)); p.tick(1000); assert(p.frame().bytes[3] == 6); ack(p); assert(p.done() && !p.received());
  assert(p.start_learn(11,0xfffffff0,1000)); ack(p); p.tick(0x1f30); assert(p.frame().bytes[3] == 6); ack(p); assert(p.done());
  esphome::test_now=0;
  assert(p.start_poll(12,0)); ack(p); deliver(p,frame(12,0x11,{0,0,0,0})); ack(p); deliver(p,frame(13,0x10,{30}));
  assert(p.done() && p.temperature() == 0 && p.humidity() == 0 && p.voltage() == 30);
}

struct Fixture {
  esphome::ble_client::BLEClient parent;
  MeizuBLE node;
  std::vector<MeizuEvent> events;
  Fixture() {
    esphome::test_now=0; writes.clear(); reads=0; write_error=0; esphome::api::test_events.clear();
    node.set_ble_client_parent(&parent); node.set_api_service("meizu_puck");
    node.add_on_event_callback([this](const MeizuEvent &event){events.push_back(event);}); node.setup();
  }
  void search() {
    parent.current=esphome::esp32_ble_tracker::ClientState::ESTABLISHED;
    esp_ble_gattc_cb_param_t p; node.gattc_event_handler(ESP_GATTC_SEARCH_CMPL_EVT,1,&p);
  }
  void subscribed() { esp_ble_gattc_cb_param_t p; p.write.handle=12; node.gattc_event_handler(ESP_GATTC_WRITE_DESCR_EVT,1,&p); }
  void written(int status=0,uint16_t connection=7) {
    esp_ble_gattc_cb_param_t p; p.write.status=status; p.write.conn_id=connection;
    node.gattc_event_handler(ESP_GATTC_WRITE_CHAR_EVT,1,&p);
  }
  void notify(const std::vector<uint8_t> &bytes) {
    esp_ble_gattc_cb_param_t p; p.notify.value=const_cast<uint8_t*>(bytes.data()); p.notify.value_len=bytes.size();
    node.gattc_event_handler(ESP_GATTC_NOTIFY_EVT,1,&p);
  }
  void read(const std::vector<uint8_t> &bytes) {
    esp_ble_gattc_cb_param_t p; p.read.value=const_cast<uint8_t*>(bytes.data()); p.read.value_len=bytes.size();
    node.gattc_event_handler(ESP_GATTC_READ_CHAR_EVT,1,&p);
  }
};

void poll_backoff_tests() {
  for (uint32_t start : {1000u, 0xffff0000u}) {
    Fixture f;
    f.node.set_update_interval(60000);
    uint32_t due = start;
    // Exercise every backoff step, its cap, and millis() wrapping.
    const uint32_t delays[] = {5000, 10000, 20000, 40000, 80000, 160000, 320000, 640000, 900000, 900000};
    for (unsigned i = 0; i < 10; i++) {
      // First poll is still scheduled by setup(); move the clock without polling
      // for the wrap case, then explicitly request this initial sample.
      esphome::test_now = due;
      if (i == 0) f.node.update();
      f.node.loop();
      assert(f.parent.connections == int(i + 1));
      esp_ble_gattc_cb_param_t failure;
      failure.open.status = 1;
      f.node.gattc_event_handler(ESP_GATTC_OPEN_EVT, 1, &failure);
      assert(f.events.back().operation == "poll" && f.events.back().status == "connect_failed");
      f.parent.current = esphome::esp32_ble_tracker::ClientState::IDLE;
      const int connections = f.parent.connections;
      for (uint32_t elapsed = 60000; elapsed < delays[i]; elapsed += 60000) {
        esphome::test_now = due + elapsed;
        f.node.update();  // What PollingComponent's one-minute scheduler invokes.
        f.node.loop();
        assert(f.parent.connections == connections);
      }
      esphome::test_now = due + delays[i] - 1;
      f.node.loop();
      assert(f.parent.connections == connections);
      due += delays[i];
    }
    // At the actual deadline a new poll starts, and a real sample clears backoff.
    esphome::test_now = due;
    f.node.loop();
    assert(f.parent.connections == 11);
    f.search(); f.node.loop();
    uint8_t seq = writes.back()[2]; f.written(); f.node.loop();
    f.read(frame(seq, 0x11, {0, 0, 0, 0})); f.node.loop();
    seq = writes.back()[2]; f.written(); f.node.loop();
    f.read(frame(seq, 0x10, {30})); f.node.loop();
    assert(f.events.back().status == "ok");
    f.parent.current = esphome::esp32_ble_tracker::ClientState::IDLE;
    esphome::test_now = due + 1;
    f.node.update(); f.node.loop();
    assert(f.parent.connections == 12);
  }
}

void runtime_tests(const std::vector<uint8_t> &raw,const std::string &expected_code,const std::string &expected_key) {
  const std::string key="1e001c000000000000";
  {
    Fixture f; f.node.send("send-one",key,""); f.node.loop(); assert(f.parent.connections == 1 && writes.empty());
    f.search(); assert(writes.empty()); f.subscribed(); f.node.loop(); assert(writes.size() == 1);
    uint8_t seq=writes.back()[2]; f.notify(frame(seq,4,{1})); f.node.loop(); assert(f.events.empty());
    f.written(); f.node.loop(); assert(f.events.size() == 1 && f.events.back().confirmed && f.events.back().cached);
    size_t count=writes.size(); f.node.send("send-one",key,""); f.node.loop(); assert(writes.size() == count);
    assert(f.events.back().status == "duplicate_request");
  }
  {
    Fixture f; f.node.send("wrong-connection",key,""); f.node.loop(); f.search(); f.subscribed(); f.node.loop();
    f.written(0,99); f.node.loop(); assert(f.events.empty()); esphome::test_now=5001; f.node.loop();
    assert(f.events.back().status == "write_timeout" && !f.events.back().confirmed && writes.size() == 1);
  }
  {
    Fixture f; f.node.send("read-fallback",key,""); f.node.loop(); f.search(); f.subscribed(); f.node.loop();
    uint8_t seq=writes.back()[2]; f.written(); esphome::test_now=251; f.node.loop();
    assert(reads == 1 && writes.size() == 1);
    f.read(frame(seq,4,{1})); f.node.loop(); assert(f.events.back().confirmed && writes.size() == 1);
  }
  for (bool supported : {true, false}) {
    auto capture = raw;
    if (!supported) capture[1] ^= 1;
    Fixture f; f.node.learn("capture","tv",15); f.node.loop(); f.search(); f.subscribed(); f.node.loop();
    assert(writes.back()[3] == 5); uint8_t seq=writes.back()[2]; f.written();
    f.notify(frame(seq,7)); f.node.loop(); assert(f.events.back().phase == "ready");
    f.notify(frame(seq,9,{0,uint8_t(1+(capture.size()+14)/15),uint8_t(capture.size()),uint8_t(capture.size()>>8)})); f.node.loop();
    for(size_t offset=0,index=1;offset<capture.size();offset+=15,index++) {
      std::vector<uint8_t> payload{uint8_t(index)}; payload.insert(payload.end(),capture.begin()+offset,capture.begin()+std::min(capture.size(),offset+15));
      f.notify(frame(seq,9,payload)); f.node.loop();
    }
    assert(writes.back()[3] == 0x0b); f.written(); f.node.loop(); assert(writes.back()[3] == 6);
    f.written(); f.node.loop();
    if (supported) {
      assert(f.events.back().phase == "complete");
      assert(f.events.back().ir_code == expected_code && f.events.back().key == expected_key);
    } else {
      assert(f.events.back().phase == "failed" && f.events.back().status == "unsupported_capture");
      assert(f.events.back().ir_code.empty() && f.events.back().key.empty() && !f.events.back().confirmed);
    }
    assert(f.events.back().length == capture.size() && f.events.back().received == capture.size());
    assert(esphome::api::test_events.back().at("raw") == hex_string(capture.data(),capture.size()));
    for(auto &p:writes) assert(p[3] != 3 && p[3] != 0);
  }
  {
    Fixture f; f.node.learn("cancel-before-ready","tv",15); f.node.loop(); f.node.cancel("cancel-before-ready");
    assert(f.events.back().phase == "cancelled" && writes.empty());
  }
  {
    Fixture f; f.node.send("expired",key,""); esphome::test_now=15001; f.node.loop();
    assert(f.events.back().status == "queue_timeout" && writes.empty());
  }
  {
    Fixture f; f.parent.has_descriptor=false; f.node.send("no-cccd",key,""); f.node.loop(); f.search();
    assert(f.events.back().status == "subscribe_failed" && writes.empty());
  }
  {
    Fixture f; esphome::sensor::Sensor temperature,humidity; esphome::binary_sensor::BinarySensor available;
    f.node.set_temperature_sensor(&temperature); f.node.set_humidity_sensor(&humidity); f.node.set_available_sensor(&available);
    esphome::test_now=1001; f.node.loop(); f.search(); f.node.loop(); assert(writes.back()[3] == 0x11);
    uint8_t seq=writes.back()[2]; f.written(); f.node.loop(); assert(reads == 1);
    f.read(frame(seq,0x11,{0,0,0,0})); f.node.loop(); assert(writes.back()[3] == 0x10);
    seq=writes.back()[2]; f.written(); f.node.loop(); f.read(frame(seq,0x10,{30})); f.node.loop();
    assert(temperature.state == 0 && humidity.state == 0 && available.state);
    f.parent.current=esphome::esp32_ble_tracker::ClientState::IDLE; esphome::test_now=300000;
    f.node.send("control-near-poll",key,""); f.node.loop(); f.search(); f.subscribed(); f.node.loop();
    seq=writes.back()[2]; f.written(); f.notify(frame(seq,4,{1})); f.node.loop();
    f.parent.current=esphome::esp32_ble_tracker::ClientState::IDLE; esphome::test_now=301002; f.node.loop();
    assert(f.parent.connections == 3);  // A successful send must not starve the five-minute sensor poll.
    esphome::test_now=691002; f.node.loop(); assert(std::isnan(temperature.state) && !available.state);
  }
}

int main(int argc,char **argv) {
  if(argc>1 && std::string(argv[1])=="poll-backoff") {
    poll_backoff_tests();
    std::cout << "Periodic polling preserves failure backoff\n";
  } else if(argc>1 && std::string(argv[1])=="codec") {
    std::string raw_hex,context_hex,digest_hex;
    while(std::cin>>raw_hex>>context_hex>>digest_hex) {
      auto raw=decode(raw_hex), context=decode(context_hex), digest=decode(digest_hex);
      std::array<uint8_t,MAX_IR_CODE> code{}; size_t length=0; uint32_t frequency=0; std::array<uint8_t,9> key{};
      bool ok=convert_capture(raw.data(),raw.size(),code,length,frequency) &&
        make_cache_key(length,digest.data(),std::string(context.begin(),context.end()),key);
      if(ok) std::cout<<hex_string(code.data(),length)<<" "<<hex_string(key.data(),key.size())<<" "<<frequency<<"\n";
      else std::cout<<"error\n";
    }
  } else {
    std::string raw_hex,expected_code,expected_key; std::cin>>raw_hex>>expected_code>>expected_key;
    auto raw=decode(raw_hex); protocol_tests(raw); runtime_tests(raw,expected_code,expected_key);
    std::cout<<"Protocol and ESPHome GATT lifecycle cases passed\n";
  }
}
