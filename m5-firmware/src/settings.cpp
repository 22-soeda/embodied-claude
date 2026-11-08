#include "settings.h"

#include <Preferences.h>

namespace {
const char* kNamespace = "body";

String slotKey(const char* prefix, int slot) {
  return String(prefix) + String(slot);
}
}  // namespace

void BodySettings::load() {
  Preferences prefs;
  prefs.begin(kNamespace, /* readOnly = */ true);
  for (int slot = 0; slot < kWifiSlotCount; ++slot) {
    wifi_ssid[slot] = prefs.getString(slotKey("ssid", slot).c_str(), "");
    wifi_password[slot] = prefs.getString(slotKey("pass", slot).c_str(), "");
  }
  link_url = prefs.getString("link_url", "");
  link_token = prefs.getString("link_token", "");
  body_name = prefs.getString("name", body_name);
  face_rgb = prefs.getUInt("face_rgb", face_rgb);
  volume_percent = prefs.getUChar("volume", volume_percent);
  prefs.end();
}

void BodySettings::save() const {
  Preferences prefs;
  prefs.begin(kNamespace, /* readOnly = */ false);
  for (int slot = 0; slot < kWifiSlotCount; ++slot) {
    prefs.putString(slotKey("ssid", slot).c_str(), wifi_ssid[slot]);
    prefs.putString(slotKey("pass", slot).c_str(), wifi_password[slot]);
  }
  prefs.putString("link_url", link_url);
  prefs.putString("link_token", link_token);
  prefs.putString("name", body_name);
  prefs.putUInt("face_rgb", face_rgb);
  prefs.putUChar("volume", volume_percent);
  prefs.end();
}

bool BodySettings::hasAnyWifi() const {
  for (int slot = 0; slot < kWifiSlotCount; ++slot) {
    if (wifi_ssid[slot].length() > 0) return true;
  }
  return false;
}

bool BodySettings::hasLink() const {
  return link_url.startsWith("ws://") || link_url.startsWith("wss://");
}
