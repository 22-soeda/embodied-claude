#include "debug_status.h"

#include <WebServer.h>

namespace {
WebServer status_server(80);
StatusWriter status_writer = nullptr;
bool is_started = false;
}  // namespace

void beginDebugStatus(StatusWriter write_status_json) {
  status_writer = write_status_json;
  status_server.on("/status", HTTP_GET, []() {
    status_server.send(200, "application/json", status_writer ? status_writer() : String("{}"));
  });
  status_server.begin();
  is_started = true;
}

void updateDebugStatus() {
  if (is_started) status_server.handleClient();
}
