#include "body_camera.h"

#include <M5Unified.h>
#include <esp_camera.h>
#include <img_converters.h>

namespace {
bool camera_ready = false;

// Pin map of the CoreS3 camera, from m5stack/M5CoreS3 (MIT), utility/GC0308.cpp.
camera_config_t cores3CameraConfig() {
  camera_config_t config = {};
  config.pin_pwdn = -1;
  config.pin_reset = -1;
  config.pin_xclk = -1;  // the GC0308 module has its own clock
  config.pin_sccb_sda = 12;
  config.pin_sccb_scl = 11;
  config.pin_d7 = 47;
  config.pin_d6 = 48;
  config.pin_d5 = 16;
  config.pin_d4 = 15;
  config.pin_d3 = 42;
  config.pin_d2 = 41;
  config.pin_d1 = 40;
  config.pin_d0 = 39;
  config.pin_vsync = 46;
  config.pin_href = 38;
  config.pin_pclk = 45;
  config.xclk_freq_hz = 20000000;
  config.ledc_timer = LEDC_TIMER_0;
  config.ledc_channel = LEDC_CHANNEL_0;
  config.pixel_format = PIXFORMAT_RGB565;
  config.frame_size = FRAMESIZE_QVGA;
  config.jpeg_quality = 0;
  config.fb_count = 2;
  config.fb_location = CAMERA_FB_IN_PSRAM;
  config.grab_mode = CAMERA_GRAB_WHEN_EMPTY;
  config.sccb_i2c_port = -1;
  return config;
}
}  // namespace

bool beginCamera() {
  // The camera's SCCB bus is the same pair of pins as the internal I2C, so the
  // M5Unified driver must let go of it first (as M5CoreS3 does).
  M5.In_I2C.release();
  camera_config_t config = cores3CameraConfig();
  for (int attempt = 0; attempt < 3 && !camera_ready; ++attempt) {
    camera_ready = esp_camera_init(&config) == ESP_OK;
    if (!camera_ready) delay(300);
  }
  Serial.printf("[camera] %s\n", camera_ready ? "ready" : "not available");
  return camera_ready;
}

bool isCameraReady() {
  return camera_ready;
}

bool captureJpeg(uint8_t** jpeg, size_t* length) {
  if (!camera_ready) return false;

  // The two frame buffers may hold frames from before the request: skip them.
  for (int stale = 0; stale < 2; ++stale) {
    camera_fb_t* old_frame = esp_camera_fb_get();
    if (old_frame) esp_camera_fb_return(old_frame);
  }

  camera_fb_t* frame = esp_camera_fb_get();
  if (frame == nullptr) return false;
  const bool converted = frame2jpg(frame, /* quality = */ 60, jpeg, length);
  esp_camera_fb_return(frame);
  return converted;
}
