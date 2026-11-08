#pragma once

#include <Arduino.h>

// What the body feels without being asked: being picked up or shaken,
// something coming close to the screen, the battery running low. The head
// hears about these as events; the readings go out in the periodic state.
enum class BodyEvent { None, PickedUp, Shaken, Near, Far, BatteryLow };

const char* bodyEventName(BodyEvent event);

class Senses {
 public:
  void begin();
  // Returns at most one new event per call.
  BodyEvent update();

  int batteryPercent() const { return battery_percent_; }
  bool isCharging() const { return is_charging_; }
  uint16_t proximity() const { return proximity_; }
  uint16_t ambientLight() const { return ambient_light_; }
  bool isSomethingNear() const { return is_near_; }

 private:
  BodyEvent checkMotion();
  BodyEvent checkProximity();
  BodyEvent checkBattery();

  bool proximity_ready_ = false;
  uint16_t proximity_ = 0;
  uint16_t ambient_light_ = 0;
  bool is_near_ = false;
  uint32_t last_proximity_event_ms_ = 0;

  uint32_t still_since_ms_ = 0;
  uint32_t last_pickup_event_ms_ = 0;
  uint32_t last_shake_event_ms_ = 0;
  uint32_t shake_window_started_ms_ = 0;
  int shake_peaks_ = 0;

  int battery_percent_ = -1;
  bool is_charging_ = false;
  bool battery_low_reported_ = false;
  uint32_t last_battery_read_ms_ = 0;
  uint32_t last_proximity_read_ms_ = 0;
};

extern Senses senses;
