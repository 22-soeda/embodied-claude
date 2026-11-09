#pragma once

#include <Arduino.h>

// Ears and mouth. On CoreS3 the microphone and the speaker share one I2S
// port, so the body is either listening or speaking, never both (the same
// constraint PetitOnes documents).
//
// Listening streams 16 kHz PCM to the head chunk by chunk and decides by
// itself when the person has finished: the head only gets the audio.
// Speaking keeps one whole utterance in PSRAM and starts playing once the
// first half second has arrived, handing the speaker the rest piece by piece
// while it keeps arriving (the head sends faster than it plays). The mouth
// follows a loudness envelope computed as the audio comes in.
enum class ListenEnd { StillListening, Silence, NoSpeech, MaxLength };
// Short soft sounds for falling asleep and waking, where PetitOnes play
// zzz.wav and wakeup.wav from the SD card (this body has no SD files).
enum class Chime { FallAsleep, WakeUp };

class Voice {
 public:
  using ChunkSender = void (*)(const int16_t* samples, size_t count);

  void begin(uint8_t volume_percent);
  void setVolume(uint8_t volume_percent);

  // --- listening ---
  bool startListening(ChunkSender sender);
  void stopListening();
  // Sends the chunks that are ready; reports once how listening ended.
  ListenEnd updateListening();
  bool isListening() const { return is_listening_; }
  float listeningLevel() const { return listening_level_; }

  // --- speaking ---
  bool beginSpeech(size_t total_bytes, uint32_t sample_rate);
  void appendSpeech(const uint8_t* pcm, size_t length);
  void finishSpeech();  // every byte has arrived (plays it now if it was too short to start)
  void stopSpeaking();
  // True exactly once, when playback has just finished.
  bool updateSpeaking();
  bool isSpeaking() const { return is_playing_ || is_receiving_speech_; }
  float mouthLevel() const;

  // --- small sounds ---
  void playChime(Chime chime);

 private:
  void switchToMicrophone();
  void switchToSpeaker();
  void feedSpeaker();
  void extendMouthEnvelope();
  void releaseSpeechBuffer();

  uint8_t volume_percent_ = 70;

  // listening
  ChunkSender chunk_sender_ = nullptr;
  bool is_listening_ = false;
  bool heard_speech_ = false;
  uint32_t listen_started_ms_ = 0;
  uint32_t last_voice_ms_ = 0;
  int loud_chunks_in_row_ = 0;
  float noise_floor_ = 0;
  int calibration_chunks_ = 0;
  float listening_level_ = 0;

  // speaking
  int16_t* speech_samples_ = nullptr;
  size_t speech_bytes_expected_ = 0;
  size_t speech_bytes_received_ = 0;
  size_t speech_bytes_queued_ = 0;  // handed to the speaker so far
  uint32_t speech_sample_rate_ = 16000;
  bool is_receiving_speech_ = false;
  bool is_playing_ = false;
  uint32_t playback_started_ms_ = 0;
  uint16_t* mouth_envelope_ = nullptr;  // the loudness (RMS) of each kEnvelopeStepMs
  size_t mouth_envelope_length_ = 0;    // steps the whole utterance has
  size_t mouth_envelope_filled_ = 0;    // steps computed from the audio received so far
  float mouth_loudest_ = 0;             // loudest step received so far

  static const uint32_t kSampleRate = 16000;
  static const uint32_t kEnvelopeStepMs = 40;
  static const uint8_t kSpeechChannel = 0;  // chimes use channel 1
};

extern Voice voice;
