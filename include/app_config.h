#pragma once

// Enable the local Companion Gateway and real microphone/speaker audio path.
#define COMPANION_GATEWAY_ENABLE 1
#define COMPANION_AUDIO_ENABLE 1

// StickS3 external Grove / HY2.0-4P bus.
#define GLASS2_SDA_PIN 9
#define GLASS2_SCL_PIN 10
#define GLASS2_I2C_ADDR 0x3C
#define GLASS2_I2C_FREQ 400000
#define GLASS2_I2C_PORT 0

// Glass2 information card timing.
#define INFO_HOLD_MS 10000

// True voice audio transport.
// Mic: signed PCM16 little-endian, mono, 16 kHz.
#define AUDIO_MIC_SAMPLE_RATE 16000
#define AUDIO_MIC_BLOCK_SAMPLES 320   // 20 ms @ 16 kHz
#define AUDIO_MIC_RING_BLOCKS 4
#define AUDIO_MAX_PTT_MS 10000

// TTS is returned by the gateway as PCM16 with sample rate declared in tts.start.
#define AUDIO_TTS_MAX_BYTES (1536 * 1024)
// StickS3 / ES8311 voice calibration.
// M5Unified currently starts StickS3 ES8311 PGA at minimum gain (0 dB).
// Apply a moderate analog PGA boost for near-field speech.
#define AUDIO_MIC_PGA_GAIN_DB 6       // valid: 0..30, 3 dB steps
#define AUDIO_MIC_DIGITAL_MAG 16       // keep digital stage neutral
#define AUDIO_MIC_NOISE_FILTER_LEVEL 64 // mild first-order smoothing; 0=off, 255=strong
#define AUDIO_SPEAKER_VOLUME 255       // validated real-voice baseline
#define AUDIO_SPEAKER_MAGNIFICATION 4  // validated real-voice baseline

// Idle mascot micro-animation.
#define IDLE_BLINK_INTERVAL_MS 4200
#define IDLE_BLINK_DURATION_MS 150

// Brownout-safe Wi-Fi transmit-power limit.
// 40 quarter-dBm = 10 dBm. Reduces ESP32-S3 RF transmit current spikes while
// remaining ample for normal in-home 2.4 GHz use. Do NOT disable brownout detection.
#define WIFI_MAX_TX_POWER_QDBM 40

// During PTT, capture goes to PSRAM first. Wi-Fi PCM transmission starts only
// after the microphone/I2S path is stopped, removing simultaneous Mic + RF peaks.
#define AUDIO_PTT_BUFFER_MS 11000
#define AUDIO_PTT_BUFFER_BYTES ((AUDIO_MIC_SAMPLE_RATE * 2 * AUDIO_PTT_BUFFER_MS) / 1000)
#define AUDIO_PTT_TX_CHUNK_BYTES 640
#define AUDIO_PTT_TX_PACE_MS 4
