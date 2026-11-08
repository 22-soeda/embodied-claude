#pragma once

#include "settings.h"

// First-time setup, after PetitOnes' QR + captive portal: the body opens its
// own Wi-Fi with a random password, shows it as a QR code, and serves a form
// at http://192.168.4.1. Saving reboots into the new settings. Never returns.
[[noreturn]] void runSetupPortal(BodySettings& settings);

// USB-serial alternative for the same settings, one command per line:
//   show | wifi <1-3> <ssid> [password] | url <ws-url> | token <t> |
//   name <n> | color <rrggbb> | volume <0-100> | portal | reboot | help
// Call every loop. Returns true when a setting was changed (and saved).
bool handleSerialCommands(BodySettings& settings);
