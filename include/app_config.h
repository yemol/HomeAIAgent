#pragma once

// Core features.
#define COMPANION_GATEWAY_ENABLE 1
#define COMPANION_AUDIO_ENABLE 1

// Glass2 (Grove / HY2.0-4P).
#define GLASS2_SDA_PIN 9
#define GLASS2_SCL_PIN 10
#define GLASS2_I2C_ADDR 0x3C
#define GLASS2_I2C_FREQ 400000
#define GLASS2_I2C_PORT 0
#define INFO_HOLD_MS 15000

// Microphone: PCM16 LE, mono, 16 kHz.
#define AUDIO_MIC_SAMPLE_RATE 16000
#define AUDIO_MIC_BLOCK_SAMPLES 320   // 20 ms
#define AUDIO_MIC_RING_BLOCKS 4
#define AUDIO_MAX_PTT_MS 20000

// Gateway TTS buffer.
#define AUDIO_TTS_MAX_BYTES (1536 * 1024)

// StickS3 / ES8311 audio calibration.
#define AUDIO_MIC_PGA_GAIN_DB 6       // command capture
#define AUDIO_WAKE_MIC_PGA_GAIN_DB 6  // A1R25B0 front-end A/B: reduce wake clipping
#define AUDIO_MIC_DIGITAL_MAG 16
#define AUDIO_MIC_NOISE_FILTER_LEVEL 64
#define AUDIO_SPEAKER_VOLUME 255
#define AUDIO_SPEAKER_MAGNIFICATION 5
#define AUDIO_WAKE_ACK_MAGNIFICATION 6

// Idle animation.
#define IDLE_BLINK_INTERVAL_MS 4200
#define IDLE_BLINK_DURATION_MS 150

// 40 quarter-dBm = 10 dBm. Keep brownout protection enabled.
#define WIFI_MAX_TX_POWER_QDBM 40

// Buffer the complete utterance in PSRAM and transmit only after Mic/I2S stops.
#define AUDIO_PTT_BUFFER_MS (AUDIO_MAX_PTT_MS + 1000)
#define AUDIO_PTT_BUFFER_BYTES ((AUDIO_MIC_SAMPLE_RATE * 2 * AUDIO_PTT_BUFFER_MS) / 1000)
#define AUDIO_PTT_TX_CHUNK_BYTES 640
#define AUDIO_PTT_TX_PACE_MS 4
