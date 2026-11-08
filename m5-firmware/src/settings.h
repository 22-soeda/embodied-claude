#pragma once

#include <Arduino.h>

// What the body needs to know about its surroundings. Kept in NVS
// (namespace "body"), never on the SD card, so Wi-Fi passwords stay inside
// the device. Written by the setup portal or by serial commands.
struct BodySettings {
  static const int kWifiSlotCount = 3;

  // Tried in this order; the first one that connects wins.
  String wifi_ssid[kWifiSlotCount];
  String wifi_password[kWifiSlotCount];

  // Where the head's m5-link listens, e.g. ws://192.168.3.17:8790/body or
  // wss://rysen9-win.example.ts.net/body. The body always dials out.
  String link_url;
  String link_token;  // shared secret, sent as ?token=

  String body_name = "body";
  uint32_t face_rgb = 0x3E6A8A;
  uint8_t volume_percent = 70;

  void load();
  void save() const;

  bool hasAnyWifi() const;
  bool hasLink() const;
};
