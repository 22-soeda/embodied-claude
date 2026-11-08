#include "face.h"

#include <M5Unified.h>

Face face;

namespace {
M5Canvas canvas(&M5.Display);

const int kScreenWidth = 320;
const int kScreenHeight = 240;
const int kCaptionTop = 176;  // three lines of 16 px Japanese text below this

uint16_t rgbTo565(uint32_t rgb) {
  return M5.Display.color888((rgb >> 16) & 0xFF, (rgb >> 8) & 0xFF, rgb & 0xFF);
}

// A heart whose widest point is `size` pixels across, centered on (x, y).
void fillHeart(int x, int y, int size, uint16_t color) {
  const int lobe = size / 4;
  canvas.fillCircle(x - lobe, y - lobe / 2, lobe + 1, color);
  canvas.fillCircle(x + lobe, y - lobe / 2, lobe + 1, color);
  canvas.fillTriangle(x - size / 2, y, x + size / 2, y, x, y + size / 2, color);
}
}  // namespace

Mood moodFromName(const char* name) {
  if (name == nullptr) return Mood::Neutral;
  if (strcmp(name, "happy") == 0) return Mood::Happy;
  if (strcmp(name, "sad") == 0) return Mood::Sad;
  if (strcmp(name, "surprised") == 0) return Mood::Surprised;
  if (strcmp(name, "thinking") == 0) return Mood::Thinking;
  if (strcmp(name, "sleepy") == 0) return Mood::Sleepy;
  if (strcmp(name, "love") == 0) return Mood::Love;
  return Mood::Neutral;
}

void Face::begin(uint32_t face_rgb) {
  background_color_ = rgbTo565(0xFFFFFF);
  caption_color_ = rgbTo565(0x333333);
  setFaceColor(face_rgb);

  canvas.setPsram(true);
  canvas.setColorDepth(16);
  canvas.createSprite(kScreenWidth, kScreenHeight);
  next_blink_ms_ = millis() + random(2000, 5000);
  applyBrightness();
}

void Face::setFaceColor(uint32_t face_rgb) {
  face_color_ = rgbTo565(face_rgb);
}

void Face::showMood(Mood mood, uint32_t duration_ms) {
  mood_ = mood;
  mood_until_ms_ = duration_ms == 0 ? 0 : millis() + duration_ms;
}

void Face::lookAt(int x, int y, uint32_t duration_ms) {
  gaze_x_ = constrain(x, -100, 100);
  gaze_y_ = constrain(y, -100, 100);
  gaze_until_ms_ = millis() + duration_ms;
}

void Face::setActivity(Activity activity) {
  activity_ = activity;
  if (activity != Activity::Speaking) mouth_open_ = 0;
  if (activity != Activity::Listening) listening_level_ = 0;
}

void Face::setMouthOpen(float level) {
  mouth_open_ = constrain(level, 0.0f, 1.0f);
}

void Face::setListeningLevel(float level) {
  listening_level_ = constrain(level, 0.0f, 1.0f);
}

void Face::setStatusBadge(const char* text) {
  badge_ = text;
}

void Face::showCaption(const String& text, uint32_t duration_ms) {
  caption_ = text;
  caption_until_ms_ = millis() + duration_ms;
}

void Face::setAsleep(bool asleep) {
  if (asleep == is_asleep_) return;
  is_asleep_ = asleep;
  dimmed_for_sleep_ = false;
  if (asleep) {
    fell_asleep_ms_ = millis();
  } else {
    blink_started_ms_ = millis();  // wake with a blink
  }
  applyBrightness();
}

void Face::setPowerSave(bool on) {
  power_save_ = on;
  applyBrightness();
}

void Face::applyBrightness() {
  // While the eyes are closing the screen stays lit; it dims once asleep (see update()).
  if (is_asleep_ && millis() - fell_asleep_ms_ >= kFallAsleepMs) {
    M5.Display.setBrightness(kAsleepBrightness);
  } else {
    M5.Display.setBrightness(power_save_ ? min(kPowerSaveBrightness, kAwakeBrightness) : kAwakeBrightness);
  }
}

float Face::eyesClosing() const {
  if (!is_asleep_) return 0;
  const uint32_t since = millis() - fell_asleep_ms_;
  return since >= kFallAsleepMs ? 1.0f : static_cast<float>(since) / kFallAsleepMs;
}

void Face::reflexBlink() {
  blink_started_ms_ = millis();
}

void Face::update() {
  const uint32_t now = millis();
  const bool dozing = is_asleep_ && now - fell_asleep_ms_ < kFallAsleepMs;
  const uint32_t frames_per_second = is_asleep_ && !dozing ? kAsleepFramesPerSecond
                                     : power_save_         ? kPowerSaveFramesPerSecond
                                                           : kFramesPerSecond;
  if (now - last_frame_ms_ < 1000 / frames_per_second) return;
  last_frame_ms_ = now;
  if (is_asleep_ && !dozing && !dimmed_for_sleep_) {  // the eyes have closed: dim now
    applyBrightness();
    dimmed_for_sleep_ = true;
  }

  if (now >= next_blink_ms_) {
    blink_started_ms_ = now;
    next_blink_ms_ = now + random(2000, 6000);
  }
  if (gaze_until_ms_ != 0 && now > gaze_until_ms_) {
    gaze_x_ = 0;
    gaze_y_ = 0;
    gaze_until_ms_ = 0;
  }
  if (caption_until_ms_ != 0 && now > caption_until_ms_) {
    caption_ = "";
    caption_until_ms_ = 0;
  }
  drawFrame();
}

Mood Face::currentMood() const {
  if (is_asleep_ && eyesClosing() >= 1.0f) return Mood::Sleepy;
  if (is_asleep_) return Mood::Neutral;  // eyes closing (see drawEyes)
  if (mood_until_ms_ != 0 && millis() > mood_until_ms_) return Mood::Neutral;
  return mood_;
}

void Face::drawFrame() {
  canvas.fillSprite(background_color_);

  // A slow idle sway, so the face never looks frozen.
  const float t = millis() * 0.0015f;
  const bool has_caption = caption_.length() > 0;
  const int center_x = 160 + static_cast<int>(sinf(t) * 4);
  const int center_y = (has_caption ? 92 : 112) + static_cast<int>(cosf(t * 0.7f) * 3);

  drawEyes(center_x, center_y);
  drawMouth(center_x, center_y);
  if (activity_ == Activity::Thinking) drawThinkingDots(center_x, center_y);
  if (is_asleep_ && eyesClosing() >= 1.0f) drawSleepZ(center_x, center_y);
  if (activity_ == Activity::Listening) drawListeningBar();
  drawBadge();
  if (has_caption) drawCaption();

  canvas.pushSprite(0, 0);
}

void Face::drawEyes(int center_x, int center_y) {
  const Mood mood = currentMood();

  // Gaze: where the head looks, or a gentle side-to-side while listening.
  int gaze_px_x = gaze_x_ * 14 / 100;
  int gaze_px_y = gaze_y_ * 10 / 100;
  if (activity_ == Activity::Listening) {
    gaze_px_x = static_cast<int>(sinf(millis() * 0.004f) * 10);
  } else if (mood == Mood::Thinking || activity_ == Activity::Thinking) {
    gaze_px_x = -10;
    gaze_px_y = -9;
  }

  const int eye_y = center_y - 22 + gaze_px_y;
  const int eye_xs[2] = {center_x - 62 + gaze_px_x, center_x + 62 + gaze_px_x};

  // 0 = open, 1 = closed, along a triangle over the blink duration.
  float blink = 0;
  const uint32_t since_blink = millis() - blink_started_ms_;
  if (since_blink < kBlinkDurationMs) {
    const float phase = static_cast<float>(since_blink) / kBlinkDurationMs;
    blink = phase < 0.5f ? phase * 2 : (1 - phase) * 2;
  }
  if (is_asleep_) blink = max(blink, eyesClosing());  // falling asleep: the eyes close slowly

  for (int eye_x : eye_xs) {
    switch (mood) {
      case Mood::Happy:
        canvas.fillArc(eye_x, eye_y + 10, 20, 13, 200, 340, face_color_);
        break;
      case Mood::Sleepy:
        canvas.fillRoundRect(eye_x - 18, eye_y, 36, 6, 3, face_color_);
        break;
      case Mood::Love:
        fillHeart(eye_x, eye_y, 40, rgbTo565(0xE0507A));
        break;
      case Mood::Surprised: {
        const int radius = static_cast<int>(20 * (1 - blink * 0.9f)) + 2;
        canvas.fillEllipse(eye_x, eye_y, 20, radius, face_color_);
        canvas.fillCircle(eye_x + 6, eye_y - 7, 5, background_color_);
        break;
      }
      default: {
        const int height = static_cast<int>(26 * (1 - blink * 0.88f)) + 2;
        canvas.fillEllipse(eye_x, eye_y, 16, height, face_color_);
        if (height > 12) canvas.fillCircle(eye_x + 5, eye_y - 9, 4, background_color_);
        if (mood == Mood::Sad) {
          // A drooping lid: the outer upper corner is cut away.
          const int outer = eye_x < center_x ? -1 : 1;
          canvas.fillTriangle(eye_x - 22 * outer, eye_y - 30, eye_x + 24 * outer, eye_y - 30,
                              eye_x + 24 * outer, eye_y - 4, background_color_);
        }
        break;
      }
    }
  }
}

void Face::drawMouth(int center_x, int center_y) {
  const int mouth_y = center_y + 34;

  if (activity_ == Activity::Speaking) {
    const int height = 3 + static_cast<int>(mouth_open_ * 20);
    const int width = 14 + static_cast<int>(mouth_open_ * 6);
    canvas.fillEllipse(center_x, mouth_y + 8, width, height, face_color_);
    return;
  }

  switch (currentMood()) {
    case Mood::Happy:
    case Mood::Love:
      canvas.fillArc(center_x, mouth_y - 6, 26, 19, 20, 160, face_color_);
      break;
    case Mood::Sad:
      canvas.fillArc(center_x, mouth_y + 22, 20, 15, 215, 325, face_color_);
      break;
    case Mood::Surprised:
      canvas.fillEllipse(center_x, mouth_y + 10, 9, 12, face_color_);
      break;
    case Mood::Thinking:
    case Mood::Sleepy:
      canvas.fillRoundRect(center_x - 12, mouth_y + 6, 24, 5, 2, face_color_);
      break;
    default:
      canvas.fillArc(center_x, mouth_y - 2, 18, 13, 30, 150, face_color_);
      break;
  }
}

void Face::drawThinkingDots(int center_x, int center_y) {
  const int step = (millis() / 350) % 4;
  for (int dot = 0; dot < 3; ++dot) {
    const bool is_lit = dot < step;
    canvas.fillCircle(center_x + 92 + dot * 14, center_y - 74, 5,
                      is_lit ? face_color_ : rgbTo565(0xDDDDDD));
  }
}

void Face::drawSleepZ(int center_x, int center_y) {
  const int rise = (millis() / 60) % 40;
  canvas.setFont(&fonts::FreeSansBold12pt7b);
  canvas.setTextColor(face_color_);
  canvas.drawString("z", center_x + 92, center_y - 50 - rise);
  canvas.drawString("Z", center_x + 108, center_y - 70 - rise / 2);
}

void Face::drawListeningBar() {
  const int width = static_cast<int>(listening_level_ * 200);
  canvas.fillRoundRect(60, 222, 200, 8, 4, rgbTo565(0xE6E6E6));
  canvas.fillRoundRect(60, 222, max(width, 8), 8, 4, face_color_);
}

void Face::drawBadge() {
  if (badge_ == nullptr) return;
  canvas.setFont(&fonts::Font2);
  const int width = canvas.textWidth(badge_) + 12;
  canvas.fillRoundRect(kScreenWidth - width - 4, 4, width, 20, 6, face_color_);
  canvas.setTextColor(background_color_);
  canvas.drawString(badge_, kScreenWidth - width + 2, 6);
}

void Face::drawCaption() {
  const int kMarginX = 8;
  const int kLineHeight = 20;
  const int kMaxLines = 3;
  const int max_width = kScreenWidth - kMarginX * 2;

  canvas.fillRect(0, kCaptionTop, kScreenWidth, kScreenHeight - kCaptionTop,
                  rgbTo565(0xF4F4F4));
  canvas.setFont(&fonts::lgfxJapanGothic_16);
  canvas.setTextColor(caption_color_);

  // Wrap by measured width, one UTF-8 character at a time: Japanese has no
  // spaces to break on. Text past the third line is dropped.
  String line;
  int line_index = 0;
  for (size_t i = 0; i < caption_.length() && line_index < kMaxLines;) {
    size_t char_end = i + 1;
    while (char_end < caption_.length() && (caption_[char_end] & 0xC0) == 0x80) ++char_end;
    const String next_char = caption_.substring(i, char_end);
    i = char_end;

    if (next_char == "\n" || canvas.textWidth(line + next_char) > max_width) {
      canvas.drawString(line, kMarginX, kCaptionTop + 6 + line_index * kLineHeight);
      ++line_index;
      line = next_char == "\n" ? "" : next_char;
    } else {
      line += next_char;
    }
  }
  if (line_index < kMaxLines && line.length() > 0) {
    canvas.drawString(line, kMarginX, kCaptionTop + 6 + line_index * kLineHeight);
  }
}
