#pragma once

#include <Arduino.h>
#include <ArduinoJson.h>

#include "settings.h"

// The one line between the body and the head (m5-link on the desktop).
//
// The body dials out, the way the PetitOnes cloud version does, so it works
// from any Wi-Fi that reaches the head: the home LAN with ws://, or the
// internet through Tailscale Funnel with wss://.
//
//   text frames   : JSON with a "t" field (see docs/m5-body-protocol.ja.md)
//   binary frames : 1 byte BinaryKind + payload
enum class BinaryKind : uint8_t {
  Audio = 0x01,  // PCM 16-bit little endian, mono, 16 kHz
  Jpeg = 0x02,
};

class BrainLink {
 public:
  using ConnectedHandler = void (*)();
  using MessageHandler = void (*)(JsonDocument& message);
  using AudioHandler = void (*)(const uint8_t* pcm, size_t length);

  void begin(const BodySettings& settings, ConnectedHandler on_connected,
             MessageHandler on_message, AudioHandler on_audio);
  void update();

  bool isConnected() const { return is_connected_; }
  // How long the line has been down; 0 while connected.
  uint32_t msSinceLost() const;

  bool sendJson(const JsonDocument& message);
  bool sendBinary(BinaryKind kind, const uint8_t* data, size_t length);

 private:
  static void onSocketEvent(int type, uint8_t* payload, size_t length);
  void handleText(uint8_t* payload, size_t length);
  void handleBinary(uint8_t* payload, size_t length);

  ConnectedHandler on_connected_ = nullptr;
  MessageHandler on_message_ = nullptr;
  AudioHandler on_audio_ = nullptr;
  bool is_connected_ = false;
  uint32_t lost_since_ms_ = 0;
};

extern BrainLink brainLink;
