#include "brain_link.h"

#include <WebSocketsClient.h>

BrainLink brainLink;

namespace {
WebSocketsClient socket;

struct LinkAddress {
  bool use_tls = false;
  String host;
  uint16_t port = 0;
  String path = "/body";
};

// ws://host:port/path -> parts. Only what the settings can hold: no user info,
// no query (the token is appended separately).
LinkAddress parseLinkUrl(const String& url) {
  LinkAddress address;
  String rest = url;
  if (rest.startsWith("wss://")) {
    address.use_tls = true;
    rest = rest.substring(6);
  } else if (rest.startsWith("ws://")) {
    rest = rest.substring(5);
  }

  const int path_start = rest.indexOf('/');
  String host_port = path_start >= 0 ? rest.substring(0, path_start) : rest;
  if (path_start >= 0) address.path = rest.substring(path_start);

  const int colon = host_port.indexOf(':');
  if (colon >= 0) {
    address.host = host_port.substring(0, colon);
    address.port = host_port.substring(colon + 1).toInt();
  } else {
    address.host = host_port;
  }
  if (address.port == 0) address.port = address.use_tls ? 443 : 80;
  return address;
}
}  // namespace

void BrainLink::begin(const BodySettings& settings,
                      ConnectedHandler on_connected, MessageHandler on_message,
                      AudioHandler on_audio) {
  on_connected_ = on_connected;
  on_message_ = on_message;
  on_audio_ = on_audio;
  lost_since_ms_ = millis();

  if (!settings.hasLink()) {
    Serial.println("[link] no link_url set; use the setup portal or `url ...`");
    return;
  }

  const LinkAddress address = parseLinkUrl(settings.link_url);
  const String path_with_token = address.path + "?token=" + settings.link_token;
  Serial.printf("[link] %s %s:%u%s\n", address.use_tls ? "wss" : "ws",
                address.host.c_str(), address.port, address.path.c_str());

  if (address.use_tls) {
    // No certificate pinning yet: the token is what keeps strangers out.
    socket.beginSSL(address.host.c_str(), address.port, path_with_token.c_str());
  } else {
    socket.begin(address.host.c_str(), address.port, path_with_token.c_str());
  }
  socket.onEvent([](WStype_t type, uint8_t* payload, size_t length) {
    BrainLink::onSocketEvent(static_cast<int>(type), payload, length);
  });
  socket.setReconnectInterval(3000);
  // A ping every 10 s; two missed pongs and the line counts as gone.
  socket.enableHeartbeat(10000, 4000, 2);
}

void BrainLink::update() {
  socket.loop();
}

uint32_t BrainLink::msSinceLost() const {
  return is_connected_ ? 0 : millis() - lost_since_ms_;
}

bool BrainLink::sendJson(const JsonDocument& message) {
  if (!is_connected_) return false;
  String text;
  serializeJson(message, text);
  return socket.sendTXT(text);
}

bool BrainLink::sendBinary(BinaryKind kind, const uint8_t* data, size_t length) {
  if (!is_connected_) return false;
  uint8_t* frame = static_cast<uint8_t*>(malloc(length + 1));
  if (frame == nullptr) return false;
  frame[0] = static_cast<uint8_t>(kind);
  memcpy(frame + 1, data, length);
  const bool sent = socket.sendBIN(frame, length + 1);
  free(frame);
  return sent;
}

void BrainLink::onSocketEvent(int type, uint8_t* payload, size_t length) {
  switch (static_cast<WStype_t>(type)) {
    case WStype_CONNECTED:
      Serial.println("[link] connected");
      brainLink.is_connected_ = true;
      if (brainLink.on_connected_) brainLink.on_connected_();
      break;
    case WStype_DISCONNECTED:
      if (brainLink.is_connected_) {
        Serial.println("[link] disconnected");
        brainLink.lost_since_ms_ = millis();
      }
      brainLink.is_connected_ = false;
      break;
    case WStype_TEXT:
      brainLink.handleText(payload, length);
      break;
    case WStype_BIN:
      brainLink.handleBinary(payload, length);
      break;
    default:
      break;
  }
}

void BrainLink::handleText(uint8_t* payload, size_t length) {
  JsonDocument message;
  if (deserializeJson(message, payload, length) != DeserializationError::Ok) {
    Serial.println("[link] dropped a text frame that is not JSON");
    return;
  }
  if (on_message_) on_message_(message);
}

void BrainLink::handleBinary(uint8_t* payload, size_t length) {
  if (length < 2) return;
  const BinaryKind kind = static_cast<BinaryKind>(payload[0]);
  if (kind == BinaryKind::Audio && on_audio_) {
    on_audio_(payload + 1, length - 1);
  }
}
