#pragma once

#include <Arduino.h>

#include "settings.h"

// Keeps the body on one of up to three known Wi-Fi networks, tried in the
// order they were registered (home first, then a phone's tethering, ...).
// WiFiMulti is not used because it picks the strongest signal, not the order.
// update() never blocks, so the face keeps animating while it searches.
class WifiKeeper {
 public:
  void begin(const BodySettings& settings);
  void update();

  bool isConnected() const;
  String connectedSsid() const;

 private:
  void startNextAttempt();

  const BodySettings* settings_ = nullptr;
  int next_slot_ = 0;
  bool is_attempting_ = false;
  uint32_t attempt_started_ms_ = 0;

  // A home router answers within a few seconds; a phone hotspot that was just
  // switched on can take longer, so this errs on the slow side.
  static const uint32_t kAttemptTimeoutMs = 9000;
};
