#include "wifi_keeper.h"

#include <WiFi.h>

void WifiKeeper::begin(const BodySettings& settings) {
  settings_ = &settings;
  WiFi.mode(WIFI_STA);
  WiFi.setAutoReconnect(false);  // we cycle through the slots ourselves
  WiFi.setSleep(false);          // modem sleep adds latency to every voice chunk
  WiFi.setHostname(settings.body_name.c_str());
  startNextAttempt();
}

void WifiKeeper::update() {
  if (settings_ == nullptr) return;

  if (WiFi.status() == WL_CONNECTED) {
    is_attempting_ = false;
    return;
  }

  const bool attempt_timed_out =
      is_attempting_ && millis() - attempt_started_ms_ > kAttemptTimeoutMs;
  if (!is_attempting_ || attempt_timed_out) {
    startNextAttempt();
  }
}

bool WifiKeeper::isConnected() const {
  return WiFi.status() == WL_CONNECTED;
}

String WifiKeeper::connectedSsid() const {
  return isConnected() ? WiFi.SSID() : String();
}

void WifiKeeper::startNextAttempt() {
  for (int tried = 0; tried < BodySettings::kWifiSlotCount; ++tried) {
    const int slot = next_slot_;
    next_slot_ = (next_slot_ + 1) % BodySettings::kWifiSlotCount;
    if (settings_->wifi_ssid[slot].length() == 0) continue;

    Serial.printf("[wifi] trying slot %d: %s\n", slot + 1,
                  settings_->wifi_ssid[slot].c_str());
    WiFi.disconnect();
    WiFi.begin(settings_->wifi_ssid[slot].c_str(),
               settings_->wifi_password[slot].c_str());
    is_attempting_ = true;
    attempt_started_ms_ = millis();
    return;
  }
  is_attempting_ = false;  // no slot configured
}
