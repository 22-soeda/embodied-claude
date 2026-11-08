#pragma once

#include <Arduino.h>

// The face drawn on the 320x240 screen. Everything is drawn procedurally into
// one sprite and pushed once per frame, so nothing flickers.
//
// Layers, from what the head decides to what the body does on its own:
//   mood      : set by the head (happy, sad, ...), may expire after a while
//   activity  : what the body is doing right now (listening, speaking, ...)
//   reflexes  : blinking, idle sway, a blink when poked; never wait for the head
//   overlays  : status badge (NO WIFI, ...), caption of what is being said
enum class Mood { Neutral, Happy, Sad, Surprised, Thinking, Sleepy, Love };
enum class Activity { None, Listening, Thinking, Speaking, Looking };

Mood moodFromName(const char* name);

class Face {
 public:
  void begin(uint32_t face_rgb);
  void setFaceColor(uint32_t face_rgb);

  // duration_ms = 0 keeps the mood until the next one.
  void showMood(Mood mood, uint32_t duration_ms);
  // x, y in -100..100 (right, down); the gaze drifts back after duration_ms.
  void lookAt(int x, int y, uint32_t duration_ms);
  void setActivity(Activity activity);
  Activity activity() const { return activity_; }

  void setMouthOpen(float level);       // 0..1, while speaking
  void setListeningLevel(float level);  // 0..1, while listening
  void setStatusBadge(const char* text);  // nullptr clears it
  void showCaption(const String& text, uint32_t duration_ms);
  // Asleep: the eyes close over kFallAsleepMs, then a sleeping face at
  // kAsleepBrightness. Awake again: the eyes open with a blink.
  void setAsleep(bool asleep);
  bool isAsleep() const { return is_asleep_; }
  // Power save (PetitOnes): brightness capped at kPowerSaveBrightness, 10 frames a second.
  void setPowerSave(bool on);
  bool isPowerSave() const { return power_save_; }
  void reflexBlink();

  // Call every loop; draws at most kFramesPerSecond times a second.
  void update();

 private:
  void drawFrame();
  void drawEyes(int center_x, int center_y);
  void drawMouth(int center_x, int center_y);
  void drawThinkingDots(int center_x, int center_y);
  void drawSleepZ(int center_x, int center_y);
  void drawListeningBar();
  void drawBadge();
  void drawCaption();
  Mood currentMood() const;
  float eyesClosing() const;  // 0 = open, 1 = closed, while falling asleep
  void applyBrightness();

  uint16_t face_color_ = 0;
  uint16_t background_color_ = 0;
  uint16_t caption_color_ = 0;

  Mood mood_ = Mood::Neutral;
  uint32_t mood_until_ms_ = 0;
  Activity activity_ = Activity::None;

  int gaze_x_ = 0;
  int gaze_y_ = 0;
  uint32_t gaze_until_ms_ = 0;

  float mouth_open_ = 0;
  float listening_level_ = 0;
  const char* badge_ = nullptr;
  String caption_;
  uint32_t caption_until_ms_ = 0;
  bool is_asleep_ = false;
  uint32_t fell_asleep_ms_ = 0;
  bool dimmed_for_sleep_ = false;
  bool power_save_ = false;

  uint32_t next_blink_ms_ = 0;
  uint32_t blink_started_ms_ = 0;
  uint32_t last_frame_ms_ = 0;

  // Brightness and frame rates as in PetitOnes/m5-petit-firmware.
  static const uint8_t kAwakeBrightness = 80;
  static const uint8_t kPowerSaveBrightness = 40;
  static const uint8_t kAsleepBrightness = 15;
  static const uint32_t kFramesPerSecond = 30;
  static const uint32_t kPowerSaveFramesPerSecond = 10;
  static const uint32_t kAsleepFramesPerSecond = 4;  // only the drifting "z" moves
  static const uint32_t kFallAsleepMs = 1200;
  static const uint32_t kBlinkDurationMs = 160;
};

extern Face face;
