#include "voice.h"

#include <M5Unified.h>
#include <esp_heap_caps.h>
#include <freertos/queue.h>

Voice voice;

namespace {
// 1024 samples = 64 ms at 16 kHz. M5Unified's mic queue holds two requests,
// so the main loop has about one chunk of slack (64 ms) to send a filled
// buffer and hand it back before audio is lost. Drawing a frame takes ~30 ms.
const size_t kChunkSamples = 1024;
const int kChunkBufferCount = 2;
int16_t chunk_buffers[kChunkBufferCount][kChunkSamples];
QueueHandle_t filled_chunks = nullptr;

// Thresholds on the RMS of a chunk (int16 units), tuned on the CoreS3 mic.
const float kMinimumSpeechRms = 350;
const float kSpeechOverNoise = 2.5f;
const uint32_t kEndOfSpeechSilenceMs = 900;
const uint32_t kGiveUpWithoutSpeechMs = 6000;
const uint32_t kMaxUtteranceMs = 15000;
const int kCalibrationChunks = 3;  // ~200 ms of room noise before judging

// Speaking while the audio still arrives. It reaches the body at ~120 KB/s and
// plays at 32 KB/s, so half a second in hand is plenty; before this, the body
// waited for the whole utterance and the caption led the voice by seconds
// (8 s for 14 s of speech at 4 KB frames, 2026-11-09).
const size_t kStartAfterMs = 500;
// Each request to the speaker: no scraps while more is coming, and no more
// than a second at a time so the rest keeps flowing in behind it.
const size_t kMinPieceMs = 100;
const size_t kMaxPieceMs = 1000;
// The mouth opens in proportion to the loudest step heard so far; this floor
// keeps the first quiet steps from opening it fully.
const float kQuietestFullMouthRms = 500;

void onMicBufferReleased(void* /* args */, void* data, size_t /* length */) {
  xQueueSend(filled_chunks, &data, 0);
}

float rmsOf(const int16_t* samples, size_t count) {
  if (count == 0) return 0;
  double sum_of_squares = 0;
  for (size_t i = 0; i < count; ++i) sum_of_squares += static_cast<double>(samples[i]) * samples[i];
  return sqrtf(static_cast<float>(sum_of_squares / count));
}
}  // namespace

void Voice::begin(uint8_t volume_percent) {
  filled_chunks = xQueueCreate(kChunkBufferCount * 2, sizeof(void*));
  M5.Mic.setBufferReleaseCallback(nullptr, onMicBufferReleased);

  auto mic_config = M5.Mic.config();
  mic_config.sample_rate = kSampleRate;
  M5.Mic.config(mic_config);

  volume_percent_ = volume_percent;
  switchToSpeaker();
}

void Voice::setVolume(uint8_t volume_percent) {
  volume_percent_ = min<uint8_t>(volume_percent, 100);
  M5.Speaker.setVolume(map(volume_percent_, 0, 100, 0, 255));
}

// ---------------------------------------------------------------- listening

bool Voice::startListening(ChunkSender sender) {
  if (is_listening_) return true;
  stopSpeaking();
  switchToMicrophone();

  xQueueReset(filled_chunks);
  chunk_sender_ = sender;
  is_listening_ = true;
  heard_speech_ = false;
  loud_chunks_in_row_ = 0;
  noise_floor_ = 0;
  calibration_chunks_ = 0;
  listening_level_ = 0;
  listen_started_ms_ = millis();
  last_voice_ms_ = listen_started_ms_;

  for (int i = 0; i < kChunkBufferCount; ++i) {
    M5.Mic.record(chunk_buffers[i], kChunkSamples, kSampleRate);
  }
  return true;
}

void Voice::stopListening() {
  if (!is_listening_) return;
  is_listening_ = false;
  listening_level_ = 0;
  switchToSpeaker();
}

ListenEnd Voice::updateListening() {
  if (!is_listening_) return ListenEnd::StillListening;

  void* chunk = nullptr;
  while (xQueueReceive(filled_chunks, &chunk, 0) == pdTRUE) {
    const int16_t* samples = static_cast<const int16_t*>(chunk);
    if (chunk_sender_) chunk_sender_(samples, kChunkSamples);

    const float rms = rmsOf(samples, kChunkSamples);
    listening_level_ = min(1.0f, rms / 3000.0f);

    if (calibration_chunks_ < kCalibrationChunks) {
      noise_floor_ += rms / kCalibrationChunks;
      ++calibration_chunks_;
    } else {
      const float speech_threshold = max(kMinimumSpeechRms, noise_floor_ * kSpeechOverNoise);
      const bool is_loud = rms > speech_threshold;
      loud_chunks_in_row_ = is_loud ? loud_chunks_in_row_ + 1 : 0;
      if (loud_chunks_in_row_ >= 2) heard_speech_ = true;
      if (is_loud) last_voice_ms_ = millis();
    }

    if (is_listening_) M5.Mic.record(static_cast<int16_t*>(chunk), kChunkSamples, kSampleRate);
  }

  const uint32_t now = millis();
  ListenEnd end = ListenEnd::StillListening;
  if (heard_speech_ && now - last_voice_ms_ > kEndOfSpeechSilenceMs) {
    end = ListenEnd::Silence;
  } else if (!heard_speech_ && now - listen_started_ms_ > kGiveUpWithoutSpeechMs) {
    end = ListenEnd::NoSpeech;
  } else if (now - listen_started_ms_ > kMaxUtteranceMs) {
    end = ListenEnd::MaxLength;
  }

  if (end != ListenEnd::StillListening) {
    Serial.printf("[voice] listen end %d (noise floor %.0f)\n", static_cast<int>(end), noise_floor_);
    stopListening();
  }
  return end;
}

// ---------------------------------------------------------------- speaking

bool Voice::beginSpeech(size_t total_bytes, uint32_t sample_rate) {
  stopListening();
  stopSpeaking();
  if (total_bytes < 2) return false;

  speech_samples_ = static_cast<int16_t*>(heap_caps_malloc(total_bytes, MALLOC_CAP_SPIRAM));
  const size_t samples_per_step = sample_rate * kEnvelopeStepMs / 1000;
  const size_t steps = (total_bytes / 2 + samples_per_step - 1) / samples_per_step;
  mouth_envelope_ = static_cast<uint16_t*>(heap_caps_malloc(steps * sizeof(uint16_t), MALLOC_CAP_SPIRAM));
  if (speech_samples_ == nullptr || mouth_envelope_ == nullptr) {
    Serial.printf("[voice] no PSRAM for %u bytes of speech\n", static_cast<unsigned>(total_bytes));
    releaseSpeechBuffer();
    return false;
  }
  speech_bytes_expected_ = total_bytes;
  speech_bytes_received_ = 0;
  speech_bytes_queued_ = 0;
  speech_sample_rate_ = sample_rate;
  mouth_envelope_length_ = steps;
  mouth_envelope_filled_ = 0;
  mouth_loudest_ = 0;
  is_receiving_speech_ = true;
  return true;
}

void Voice::appendSpeech(const uint8_t* pcm, size_t length) {
  if (!is_receiving_speech_ || speech_samples_ == nullptr) return;
  const size_t room = speech_bytes_expected_ - speech_bytes_received_;
  const size_t copy_length = min(length, room);
  memcpy(reinterpret_cast<uint8_t*>(speech_samples_) + speech_bytes_received_, pcm, copy_length);
  speech_bytes_received_ += copy_length;
  extendMouthEnvelope();
  feedSpeaker();
}

void Voice::finishSpeech() {
  if (!is_receiving_speech_) return;
  is_receiving_speech_ = false;
  if (speech_bytes_received_ < 2) {
    releaseSpeechBuffer();
    return;
  }
  extendMouthEnvelope();  // the last, partial step
  feedSpeaker();          // starts now if the utterance was shorter than kStartAfterMs
}

// Starts playback once enough has arrived, then hands the speaker whatever has
// arrived since, as long as its queue (one playing request, one waiting) has room.
void Voice::feedSpeaker() {
  if (speech_samples_ == nullptr) return;
  const size_t bytes_per_ms = speech_sample_rate_ * 2 / 1000;
  if (!is_playing_) {
    const bool enough = speech_bytes_received_ >= kStartAfterMs * bytes_per_ms;
    if (is_receiving_speech_ && !enough) return;
    switchToSpeaker();
    is_playing_ = true;
    playback_started_ms_ = millis();
  }
  // isPlaying() == 2 means the queue is full; playRaw would then wait for a slot.
  while (M5.Speaker.isPlaying(kSpeechChannel) < 2) {
    size_t piece = (speech_bytes_received_ - speech_bytes_queued_) & ~static_cast<size_t>(1);
    if (piece == 0) return;
    if (is_receiving_speech_ && piece < kMinPieceMs * bytes_per_ms) return;
    piece = min(piece, kMaxPieceMs * bytes_per_ms);
    const int16_t* from = speech_samples_ + speech_bytes_queued_ / 2;
    if (!M5.Speaker.playRaw(from, piece / 2, speech_sample_rate_, /* stereo = */ false,
                            /* repeat = */ 1, kSpeechChannel, /* stop_current_sound = */ false)) {
      return;
    }
    speech_bytes_queued_ += piece;
  }
}

void Voice::stopSpeaking() {
  if (is_playing_) M5.Speaker.stop();
  is_playing_ = false;
  is_receiving_speech_ = false;
  releaseSpeechBuffer();
}

bool Voice::updateSpeaking() {
  if (!is_playing_) return false;
  feedSpeaker();
  if (is_receiving_speech_ || speech_bytes_queued_ < speech_bytes_received_) return false;
  // isPlaying() can drop a moment before the last samples leave the DAC;
  // also wait for the computed duration so the mouth closes with the sound.
  const uint32_t duration_ms = (speech_bytes_received_ / 2) * 1000 / speech_sample_rate_;
  const bool audio_done = !M5.Speaker.isPlaying(kSpeechChannel);
  const bool time_done = millis() - playback_started_ms_ > duration_ms;
  if (!(audio_done && time_done)) return false;

  is_playing_ = false;
  releaseSpeechBuffer();
  return true;
}

float Voice::mouthLevel() const {
  if (!is_playing_ || mouth_envelope_ == nullptr) return 0;
  const size_t step = (millis() - playback_started_ms_) / kEnvelopeStepMs;
  if (step >= mouth_envelope_filled_) return 0;
  const float level = mouth_envelope_[step] / max(mouth_loudest_, kQuietestFullMouthRms);
  return constrain(level * 1.3f, 0.0f, 1.0f);
}

// Computes the loudness of each step whose audio has arrived (the last, partial
// step only once everything has).
void Voice::extendMouthEnvelope() {
  if (mouth_envelope_ == nullptr) return;
  const size_t samples_per_step = speech_sample_rate_ * kEnvelopeStepMs / 1000;
  const size_t samples_received = speech_bytes_received_ / 2;
  while (mouth_envelope_filled_ < mouth_envelope_length_) {
    const size_t first = mouth_envelope_filled_ * samples_per_step;
    const size_t step_end = min(first + samples_per_step, speech_bytes_expected_ / 2);
    if (step_end > samples_received && is_receiving_speech_) return;
    const float rms = rmsOf(speech_samples_ + first, min(step_end, samples_received) - first);
    mouth_loudest_ = max(mouth_loudest_, rms);
    mouth_envelope_[mouth_envelope_filled_++] = static_cast<uint16_t>(min(rms, 65535.0f));
  }
}

void Voice::releaseSpeechBuffer() {
  if (speech_samples_ != nullptr) heap_caps_free(speech_samples_);
  if (mouth_envelope_ != nullptr) heap_caps_free(mouth_envelope_);
  speech_samples_ = nullptr;
  mouth_envelope_ = nullptr;
  mouth_envelope_length_ = 0;
  mouth_envelope_filled_ = 0;
  speech_bytes_expected_ = 0;
  speech_bytes_received_ = 0;
  speech_bytes_queued_ = 0;
}

// ---------------------------------------------------------------- small sounds

void Voice::playChime(Chime chime) {
  if (is_listening_) stopListening();
  stopSpeaking();
  switchToSpeaker();
  const uint8_t kChimeChannel = 1;  // speech plays on channel 0
  M5.Speaker.setChannelVolume(kChimeChannel, 90);  // soft, under the speaking voice
  // Two notes each: a channel holds the playing sound and one waiting sound.
  if (chime == Chime::FallAsleep) {  // falling: G5, C5
    M5.Speaker.tone(784, 160, kChimeChannel, true);
    M5.Speaker.tone(523, 300, kChimeChannel, false);
  } else {  // rising: C5, G5
    M5.Speaker.tone(523, 120, kChimeChannel, true);
    M5.Speaker.tone(784, 200, kChimeChannel, false);
  }
}

// ---------------------------------------------------------------- I2S port

void Voice::switchToMicrophone() {
  M5.Speaker.end();
  M5.Mic.begin();
}

void Voice::switchToSpeaker() {
  M5.Mic.end();
  M5.Speaker.begin();
  setVolume(volume_percent_);
}
