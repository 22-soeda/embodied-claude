#pragma once

#include <Arduino.h>

// A read-only window into the running body for development: GET /status on
// port 80 returns JSON (Wi-Fi, link, battery, senses, heap), so the state can
// be checked with curl over the LAN without a USB cable.
using StatusWriter = String (*)();

void beginDebugStatus(StatusWriter write_status_json);
void updateDebugStatus();
