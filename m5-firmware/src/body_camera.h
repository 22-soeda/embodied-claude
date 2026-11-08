#pragma once

#include <Arduino.h>

// The GC0308 camera under the CoreS3 screen. It only gives RGB565, so each
// snapshot is converted to JPEG on the device before it goes to the head.
bool beginCamera();
bool isCameraReady();

// On success *jpeg is a malloc'd buffer the caller must free().
bool captureJpeg(uint8_t** jpeg, size_t* length);
