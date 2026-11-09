// The body of an embodied-claude agent on an M5Stack CoreS3.
//
// The head (Claude, with its memory) lives on the desktop in m5-link. This
// firmware is everything that has to happen without waiting for it: drawing
// the face, blinking when poked, hearing when someone has finished speaking,
// playing the reply, noticing being picked up. The body dials out to the head
// and keeps one WebSocket open; see docs/m5-body-protocol.ja.md.
//
// Touch:
//   tap        talk once (tap again to stop listening or speaking)
//   long press talk back and forth until a turn ends in silence
//   swipe      "みてみて": take a picture and show it to the head
//   asleep     three quick taps wake it; anything else is only noted
//
// Sleep, as PetitOnes' bodies sleep: the head puts the body to sleep (and
// wakes it), or the body sleeps by itself when the battery reaches 10 %.
// Falling asleep, the eyes close, a soft sound plays and the screen dims; the
// body neither listens nor speaks until it is woken by the head or by three
// quick taps. A body that has lost its head for a minute also shows its
// sleeping face, but wakes as soon as the head is back.
//
// Structure and many details follow PetitOnes/m5-petit-firmware (MIT): the
// procedural face, Wi-Fi slots tried in order, the shared mic/speaker port,
// the QR setup portal, and the status label in the corner.

#include <M5Unified.h>
#include <WiFi.h>

#include "body_camera.h"
#include "brain_link.h"
#include "debug_status.h"
#include "face.h"
#include "senses.h"
#include "settings.h"
#include "setup_portal.h"
#include "voice.h"
#include "wifi_keeper.h"

namespace {
const char* kFirmwareVersion = "0.3.0";

const uint32_t kSleepAfterHeadLostMs = 60 * 1000;
const uint32_t kThinkingGiveUpMs = 90 * 1000;
const uint32_t kStateReportIntervalMs = 30 * 1000;
const uint32_t kCaptionAfterSpeechMs = 4000;
const int kSleepBatteryPercent = 10;      // PetitOnes: asleep at 10 % or less, not charging
const uint32_t kBatteryCheckIntervalMs = 30 * 1000;
const int kWakeTaps = 3;                    // PetitOnes: three taps wake a sleeping body
const uint32_t kWakeTapGapMs = 1000;        // each within a second of the last

BodySettings settings;
WifiKeeper wifi_keeper;

enum class SleepReason { Awake, Head, Battery };

bool is_conversation_loop = false;  // listen again after each reply
SleepReason sleep_reason = SleepReason::Awake;
int taps_while_asleep = 0;
uint32_t last_tap_while_asleep_ms = 0;
uint32_t last_battery_check_ms = 0;
bool skipping_speech_while_asleep = false;
uint32_t thinking_since_ms = 0;
uint32_t last_state_report_ms = 0;
uint32_t last_serial_heartbeat_ms = 0;
String last_spoken_text;

// ------------------------------------------------------------ sending

void sendType(const char* type) {
  JsonDocument message;
  message["t"] = type;
  brainLink.sendJson(message);
}

void sendEvent(const char* kind, const char* reason = nullptr) {
  JsonDocument message;
  message["t"] = "event";
  message["kind"] = kind;
  if (reason) message["reason"] = reason;
  brainLink.sendJson(message);
}

const char* sleepReasonName() {
  switch (sleep_reason) {
    case SleepReason::Head: return "head";
    case SleepReason::Battery: return "battery";
    default: return brainLink.isConnected() ? nullptr : "no_head";
  }
}

void sendState() {
  JsonDocument message;
  message["t"] = "state";
  message["battery"] = senses.batteryPercent();
  message["charging"] = senses.isCharging();
  message["near"] = senses.isSomethingNear();
  message["proximity"] = senses.proximity();
  message["light"] = senses.ambientLight();
  message["rssi"] = WiFi.RSSI();
  message["ssid"] = wifi_keeper.connectedSsid();
  message["uptime_s"] = millis() / 1000;
  message["asleep"] = sleep_reason != SleepReason::Awake;
  message["sleep_reason"] = sleepReasonName();
  message["power_save"] = face.isPowerSave();
  brainLink.sendJson(message);
  last_state_report_ms = millis();
}

void sendMicChunk(const int16_t* samples, size_t count) {
  brainLink.sendBinary(BinaryKind::Audio, reinterpret_cast<const uint8_t*>(samples),
                       count * sizeof(int16_t));
}

// ------------------------------------------------------------ being awake

bool isAsleep() {
  if (sleep_reason != SleepReason::Awake) return true;
  return !brainLink.isConnected() && brainLink.msSinceLost() > kSleepAfterHeadLostMs;
}

void cancelListening();

void goToSleep(SleepReason reason) {
  if (sleep_reason != SleepReason::Awake) return;
  if (voice.isListening()) cancelListening();
  if (voice.isSpeaking()) {
    voice.stopSpeaking();
    sendType("speech_interrupted");
  }
  is_conversation_loop = false;
  face.setActivity(Activity::None);
  sleep_reason = reason;
  taps_while_asleep = 0;
  voice.playChime(Chime::FallAsleep);
  sendEvent("slept", sleepReasonName());
  sendState();
}

void wakeUp(const char* reason) {
  if (sleep_reason == SleepReason::Awake) return;
  sleep_reason = SleepReason::Awake;
  taps_while_asleep = 0;
  voice.playChime(Chime::WakeUp);
  sendEvent("woke", reason);
  sendState();
}

void sleepIfBatteryLow() {
  if (millis() - last_battery_check_ms < kBatteryCheckIntervalMs) return;
  last_battery_check_ms = millis();
  const int percent = senses.batteryPercent();
  // 0 is left out as PetitOnes do: the gauge can read 0 for a moment on USB.
  if (percent > 0 && percent <= kSleepBatteryPercent && !senses.isCharging()) {
    goToSleep(SleepReason::Battery);
  }
}

const char* statusBadge() {
  if (!wifi_keeper.isConnected()) return "NO WIFI";
  if (!brainLink.isConnected()) return "NO BRAIN";
  if (senses.batteryPercent() >= 0 && senses.batteryPercent() <= 15 && !senses.isCharging()) {
    return "LOW BATTERY";
  }
  switch (face.activity()) {
    case Activity::Listening: return is_conversation_loop ? "LISTEN LOOP" : "LISTEN";
    case Activity::Thinking: return "THINK";
    case Activity::Looking: return "LOOK";
    default: return nullptr;
  }
}

// ------------------------------------------------------------ conversation

void startListening(bool keep_looping) {
  if (!brainLink.isConnected()) {
    face.showMood(Mood::Sad, 1500);  // no head to hear it
    return;
  }
  is_conversation_loop = keep_looping;
  voice.startListening(sendMicChunk);
  face.setActivity(Activity::Listening);

  JsonDocument message;
  message["t"] = "listen_start";
  message["loop"] = keep_looping;
  brainLink.sendJson(message);
}

void sendListenEnd(const char* reason) {
  JsonDocument message;
  message["t"] = "listen_end";
  message["reason"] = reason;
  brainLink.sendJson(message);
}

void finishListening(ListenEnd end) {
  if (end == ListenEnd::NoSpeech) {
    sendListenEnd("no_speech");
    is_conversation_loop = false;
    face.setActivity(Activity::None);
    return;
  }
  sendListenEnd(end == ListenEnd::MaxLength ? "max_length" : "silence");
  face.setActivity(Activity::Thinking);
  thinking_since_ms = millis();
}

void cancelListening() {
  voice.stopListening();
  is_conversation_loop = false;
  sendListenEnd("cancelled");
  face.setActivity(Activity::None);
}

void onSpeechFinished() {
  sendType("spoken");
  face.showCaption(last_spoken_text, kCaptionAfterSpeechMs);
  face.setActivity(Activity::None);
  if (is_conversation_loop) startListening(true);
}

void takeSnapshot(const char* reason, const char* request_id) {
  if (voice.isListening()) cancelListening();
  face.setActivity(Activity::Looking);
  face.update();  // show LOOK before the capture blocks for a moment

  uint8_t* jpeg = nullptr;
  size_t jpeg_length = 0;
  JsonDocument message;
  message["id"] = request_id;
  message["reason"] = reason;
  if (captureJpeg(&jpeg, &jpeg_length)) {
    message["t"] = "snapshot";
    message["bytes"] = jpeg_length;
    brainLink.sendJson(message);
    brainLink.sendBinary(BinaryKind::Jpeg, jpeg, jpeg_length);
    free(jpeg);
  } else {
    message["t"] = "snapshot_failed";
    brainLink.sendJson(message);
  }
  face.setActivity(Activity::None);
}

// ------------------------------------------------------------ from the head

void onHeadConnected() {
  JsonDocument message;
  message["t"] = "hello";
  message["name"] = settings.body_name;
  message["fw"] = kFirmwareVersion;
  message["camera"] = isCameraReady();
  brainLink.sendJson(message);
  sendState();
}

void applyConfig(JsonDocument& message) {
  if (message["color"].is<const char*>()) {
    settings.face_rgb = strtoul(message["color"].as<const char*>(), nullptr, 16);
    face.setFaceColor(settings.face_rgb);
  }
  if (message["volume"].is<int>()) {
    settings.volume_percent = constrain(message["volume"].as<int>(), 0, 100);
    voice.setVolume(settings.volume_percent);
  }
  if (message["name"].is<const char*>()) settings.body_name = message["name"].as<const char*>();
  settings.save();
}

void onHeadMessage(JsonDocument& message) {
  const char* type = message["t"] | "";

  if (strcmp(type, "face") == 0) {
    face.showMood(moodFromName(message["mood"] | "neutral"), message["ms"] | 0);
  } else if (strcmp(type, "look") == 0) {
    face.lookAt(message["x"] | 0, message["y"] | 0, message["ms"] | 3000);
  } else if (strcmp(type, "speak_start") == 0) {
    if (isAsleep()) {  // no sound while asleep; tell the head it is done at speak_end
      skipping_speech_while_asleep = true;
      return;
    }
    if (voice.isListening()) voice.stopListening();
    if (voice.beginSpeech(message["bytes"] | 0, message["rate"] | 16000)) {
      last_spoken_text = message["text"] | "";
      face.showCaption(last_spoken_text, 120000);
      face.setActivity(Activity::Speaking);
    }
  } else if (strcmp(type, "speak_end") == 0) {
    if (skipping_speech_while_asleep) {
      skipping_speech_while_asleep = false;
      sendType("spoken");
      return;
    }
    voice.finishSpeech();
  } else if (strcmp(type, "caption") == 0) {
    face.showCaption(message["text"] | "", message["ms"] | 6000);
  } else if (strcmp(type, "thinking") == 0) {
    const bool is_thinking = message["on"] | true;
    if (is_thinking) {
      face.setActivity(Activity::Thinking);
      thinking_since_ms = millis();
    } else if (face.activity() == Activity::Thinking) {
      face.setActivity(Activity::None);
      if (is_conversation_loop) startListening(true);  // nothing to say; keep listening
    }
  } else if (strcmp(type, "listen") == 0) {
    if (!isAsleep()) startListening(message["loop"] | is_conversation_loop);
  } else if (strcmp(type, "stop_listening") == 0) {
    if (voice.isListening()) cancelListening();
  } else if (strcmp(type, "snapshot") == 0) {
    takeSnapshot("requested", message["id"] | "");
  } else if (strcmp(type, "sleep") == 0) {
    goToSleep(SleepReason::Head);
  } else if (strcmp(type, "wake") == 0) {
    wakeUp("head");
  } else if (strcmp(type, "power_save") == 0) {
    face.setPowerSave(message["on"] | true);
    sendState();
  } else if (strcmp(type, "config") == 0) {
    applyConfig(message);
  }
}

void onHeadAudio(const uint8_t* pcm, size_t length) {
  voice.appendSpeech(pcm, length);
}

// ------------------------------------------------------------ touch

void handleTouchWhileAsleep(const m5::touch_detail_t& touch) {
  if (!touch.wasClicked()) return;  // swipes and long presses do nothing while asleep
  if (sleep_reason == SleepReason::Awake) return;  // only the head is missing; nothing to wake
  const uint32_t now = millis();
  taps_while_asleep = now - last_tap_while_asleep_ms <= kWakeTapGapMs ? taps_while_asleep + 1 : 1;
  last_tap_while_asleep_ms = now;
  if (taps_while_asleep == 1) sendEvent("poked");
  if (taps_while_asleep >= kWakeTaps) wakeUp("touch");
}

void handleTouch() {
  const auto touch = M5.Touch.getDetail();
  if (isAsleep()) {
    handleTouchWhileAsleep(touch);
    return;
  }

  if (touch.wasFlicked()) {
    face.reflexBlink();
    if (brainLink.isConnected()) takeSnapshot("mitemite", "");
    else face.showMood(Mood::Sad, 1500);
    return;
  }
  if (touch.wasHold()) {
    face.reflexBlink();
    startListening(/* keep_looping = */ true);
    return;
  }
  if (!touch.wasClicked()) return;

  face.reflexBlink();
  if (voice.isListening()) {
    cancelListening();
  } else if (voice.isSpeaking()) {
    voice.stopSpeaking();
    is_conversation_loop = false;
    face.setActivity(Activity::None);
    sendType("speech_interrupted");
  } else {
    startListening(/* keep_looping = */ false);
  }
}

// ------------------------------------------------------------ housekeeping

void reactToBodyEvent(BodyEvent event) {
  if (event == BodyEvent::None) return;
  sendEvent(bodyEventName(event));
  if (isAsleep()) return;  // noted, but a sleeping face does not react
  if (event == BodyEvent::PickedUp || event == BodyEvent::Shaken) {
    face.showMood(Mood::Surprised, 1500);
  } else if (event == BodyEvent::Near) {
    face.lookAt(0, -20, 2000);
  }
}

String writeStatusJson() {
  JsonDocument status;
  status["fw"] = kFirmwareVersion;
  status["name"] = settings.body_name;
  status["uptime_s"] = millis() / 1000;
  status["wifi"] = wifi_keeper.connectedSsid();
  status["ip"] = WiFi.localIP().toString();
  status["rssi"] = WiFi.RSSI();
  status["link_url"] = settings.link_url;
  status["link_connected"] = brainLink.isConnected();
  status["asleep"] = isAsleep();
  status["sleep_reason"] = sleepReasonName();
  status["power_save"] = face.isPowerSave();
  status["listening"] = voice.isListening();
  status["speaking"] = voice.isSpeaking();
  status["battery"] = senses.batteryPercent();
  status["charging"] = senses.isCharging();
  // The AXP2101 percentage jumps around; the voltages show whether USB really charges.
  status["battery_mv"] = M5.Power.getBatteryVoltage();
  status["vbus_mv"] = M5.Power.getVBUSVoltage();
  status["proximity"] = senses.proximity();
  status["light"] = senses.ambientLight();
  status["camera"] = isCameraReady();
  status["free_heap"] = ESP.getFreeHeap();
  status["free_psram"] = ESP.getFreePsram();
  String text;
  serializeJson(status, text);
  return text;
}

void printSerialHeartbeat() {
  if (millis() - last_serial_heartbeat_ms < 5000) return;
  last_serial_heartbeat_ms = millis();
  Serial.printf("[body] wifi=%s ip=%s link=%s battery=%d%% asleep=%d heap=%u psram=%u\n",
                wifi_keeper.connectedSsid().c_str(), WiFi.localIP().toString().c_str(),
                brainLink.isConnected() ? "up" : "down", senses.batteryPercent(), isAsleep(),
                ESP.getFreeHeap(), ESP.getFreePsram());
}

bool isScreenHeldAtBoot() {
  for (int i = 0; i < 5; ++i) {
    M5.update();
    if (M5.Touch.getCount() > 0) return true;
    delay(60);
  }
  return false;
}
}  // namespace

void setup() {
  auto config = M5.config();
  config.internal_mic = true;
  config.internal_spk = true;
  M5.begin(config);
  Serial.begin(115200);
  randomSeed(esp_random());

  settings.load();
  face.begin(settings.face_rgb);
  Serial.printf("[body] firmware %s, name %s\n", kFirmwareVersion, settings.body_name.c_str());

  // Hold the screen while powering on to change the settings later.
  if (!settings.hasAnyWifi() || isScreenHeldAtBoot()) runSetupPortal(settings);

  senses.begin();
  beginCamera();  // after the sensors: it borrows the internal I2C pins
  voice.begin(settings.volume_percent);
  wifi_keeper.begin(settings);
  brainLink.begin(settings, onHeadConnected, onHeadMessage, onHeadAudio);
  beginDebugStatus(writeStatusJson);
}

void loop() {
  M5.update();
  if (handleSerialCommands(settings)) {
    face.setFaceColor(settings.face_rgb);
    voice.setVolume(settings.volume_percent);
  }

  wifi_keeper.update();
  brainLink.update();
  updateDebugStatus();
  handleTouch();
  reactToBodyEvent(senses.update());
  sleepIfBatteryLow();

  const ListenEnd listen_end = voice.updateListening();
  if (listen_end != ListenEnd::StillListening) finishListening(listen_end);
  face.setListeningLevel(voice.listeningLevel());

  if (voice.updateSpeaking()) onSpeechFinished();
  face.setMouthOpen(voice.mouthLevel());

  if (face.activity() == Activity::Thinking && millis() - thinking_since_ms > kThinkingGiveUpMs) {
    face.setActivity(Activity::None);
  }

  face.setAsleep(isAsleep());
  face.setStatusBadge(statusBadge());
  face.update();

  if (brainLink.isConnected() && millis() - last_state_report_ms > kStateReportIntervalMs) sendState();
  printSerialHeartbeat();
}
