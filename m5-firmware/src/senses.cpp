#include "senses.h"

#include <M5Unified.h>

#include "vendor/LTR5XX.h"

Senses senses;

namespace {
LTR5XX proximity_sensor;

// Movement thresholds, from the CoreS3's BMI270 at rest on a desk.
const float kMovingGyroDps = 80;      // turning faster than this = being handled
const float kMovingAccelDeltaG = 0.3f;
const uint32_t kStillBeforePickupMs = 5000;
const uint32_t kPickupEventGapMs = 10000;
const float kShakePeakG = 2.0f;
const int kShakePeaksInWindow = 3;
const uint32_t kShakeWindowMs = 1000;
const uint32_t kShakeEventGapMs = 5000;

// Proximity counts (0..2047) with hysteresis; a hand 5 cm away reads ~300+.
const uint16_t kNearAbove = 300;
const uint16_t kFarBelow = 120;
const uint32_t kProximityEventGapMs = 3000;

const int kBatteryLowPercent = 15;
}  // namespace

const char* bodyEventName(BodyEvent event) {
  switch (event) {
    case BodyEvent::PickedUp: return "picked_up";
    case BodyEvent::Shaken: return "shaken";
    case BodyEvent::Near: return "near";
    case BodyEvent::Far: return "far";
    case BodyEvent::BatteryLow: return "battery_low";
    default: return "none";
  }
}

void Senses::begin() {
  // Same settings as PetitOnes' m5-petit-firmware (initSensors).
  Ltr5xx_Init_Basic_Para params = LTR5XX_BASE_PARA_CONFIG_DEFAULT;
  params.ps_led_pulse_freq = LTR5XX_LED_PULSE_FREQ_40KHZ;
  params.ps_measurement_rate = LTR5XX_PS_MEASUREMENT_RATE_50MS;
  params.als_gain = LTR5XX_ALS_GAIN_48X;
  proximity_ready_ = proximity_sensor.begin(&params);
  if (proximity_ready_) {
    proximity_sensor.setPsMode(LTR5XX_PS_ACTIVE_MODE);
    proximity_sensor.setAlsMode(LTR5XX_ALS_ACTIVE_MODE);
  }
  Serial.printf("[senses] imu %s, proximity %s\n", M5.Imu.isEnabled() ? "ok" : "missing",
                proximity_ready_ ? "ok" : "missing");
  still_since_ms_ = millis();
}

BodyEvent Senses::update() {
  BodyEvent event = checkMotion();
  if (event == BodyEvent::None) event = checkProximity();
  if (event == BodyEvent::None) event = checkBattery();
  return event;
}

BodyEvent Senses::checkMotion() {
  if (!M5.Imu.update()) return BodyEvent::None;
  const auto data = M5.Imu.getImuData();
  const float accel_g = sqrtf(data.accel.x * data.accel.x + data.accel.y * data.accel.y +
                              data.accel.z * data.accel.z);
  const float gyro_dps = sqrtf(data.gyro.x * data.gyro.x + data.gyro.y * data.gyro.y +
                               data.gyro.z * data.gyro.z);
  const uint32_t now = millis();

  if (accel_g > kShakePeakG) {
    if (now - shake_window_started_ms_ > kShakeWindowMs) {
      shake_window_started_ms_ = now;
      shake_peaks_ = 0;
    }
    ++shake_peaks_;
    if (shake_peaks_ >= kShakePeaksInWindow && now - last_shake_event_ms_ > kShakeEventGapMs) {
      last_shake_event_ms_ = now;
      shake_peaks_ = 0;
      return BodyEvent::Shaken;
    }
  }

  const bool is_moving = gyro_dps > kMovingGyroDps || fabsf(accel_g - 1.0f) > kMovingAccelDeltaG;
  if (!is_moving) {
    if (still_since_ms_ == 0) still_since_ms_ = now;
    return BodyEvent::None;
  }

  const bool was_resting = still_since_ms_ != 0 && now - still_since_ms_ > kStillBeforePickupMs;
  still_since_ms_ = 0;
  if (was_resting && now - last_pickup_event_ms_ > kPickupEventGapMs) {
    last_pickup_event_ms_ = now;
    return BodyEvent::PickedUp;
  }
  return BodyEvent::None;
}

BodyEvent Senses::checkProximity() {
  if (!proximity_ready_) return BodyEvent::None;
  const uint32_t now = millis();
  if (now - last_proximity_read_ms_ < 100) return BodyEvent::None;
  last_proximity_read_ms_ = now;

  proximity_ = proximity_sensor.getPsValue();
  ambient_light_ = proximity_sensor.getAlsValue();

  const bool became_near = !is_near_ && proximity_ > kNearAbove;
  const bool became_far = is_near_ && proximity_ < kFarBelow;
  if (!became_near && !became_far) return BodyEvent::None;

  is_near_ = became_near;
  if (now - last_proximity_event_ms_ < kProximityEventGapMs) return BodyEvent::None;
  last_proximity_event_ms_ = now;
  return became_near ? BodyEvent::Near : BodyEvent::Far;
}

BodyEvent Senses::checkBattery() {
  const uint32_t now = millis();
  if (last_battery_read_ms_ != 0 && now - last_battery_read_ms_ < 5000) return BodyEvent::None;
  last_battery_read_ms_ = now;

  battery_percent_ = M5.Power.getBatteryLevel();
  is_charging_ = M5.Power.isCharging() == m5::Power_Class::is_charging;

  if (is_charging_ || battery_percent_ > kBatteryLowPercent + 5) battery_low_reported_ = false;
  const bool is_low = battery_percent_ >= 0 && battery_percent_ <= kBatteryLowPercent && !is_charging_;
  if (is_low && !battery_low_reported_) {
    battery_low_reported_ = true;
    return BodyEvent::BatteryLow;
  }
  return BodyEvent::None;
}
