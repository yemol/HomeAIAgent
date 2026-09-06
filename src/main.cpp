#include <Arduino.h>
#include <esp_heap_caps.h>
#include <esp_system.h>
#include <esp_attr.h>
#include <esp_wifi.h>
#include <Preferences.h>

// M5Stack requires external-display headers before M5Unified.
#include <M5UnitGLASS2.h>
#include <M5Unified.h>

#include "app_config.h"
#include "glass2_zh_font_demo.h"

#ifndef HOMEAI_WAKEWORD_ENABLE
#define HOMEAI_WAKEWORD_ENABLE 0
#endif

#if HOMEAI_WAKEWORD_ENABLE
  #include <ESP_SR_M5Unified.h>

  // Local custom trigger phrase via Chinese MultiNet.
  static const sr_cmd_t HOMEAI_WAKE_COMMANDS[] = {
      {0, "逐光逐光", "zhu guang zhu guang"},
  };
  static constexpr size_t HOMEAI_WAKE_COMMAND_COUNT =
      sizeof(HOMEAI_WAKE_COMMANDS) / sizeof(HOMEAI_WAKE_COMMANDS[0]);
#endif

#if COMPANION_GATEWAY_ENABLE
  #include <WiFi.h>
  #include <WebSocketsClient.h>
  #include <ArduinoJson.h>

  // Submission-safe configuration: prefer a local, gitignored secrets.h when
  // present. Clean/source-control builds fall back to placeholder defaults;
  // provisioned devices load the real Wi-Fi/Gateway values from NVS.
  #if __has_include("secrets.h")
    #include "secrets.h"
  #else
    #include "secrets.example.h"
  #endif
#endif

enum class CompanionState : uint8_t {
  Idle,
  Listening,
  Thinking,
  Speaking,
  Success,
  Error
};

struct InfoItem {
  String id;
  String category;
  String headline;
  bool hasFrame = false;
};

#if COMPANION_GATEWAY_ENABLE
struct RuntimeDeviceConfig {
  String wifiSsid;
  String wifiPassword;
  String gatewayHost;
  uint16_t gatewayPort = 8765;
  String gatewayPath = "/companion";
  String deviceName = "HomeAIAgent";
  bool provisioned = false;
};

static RuntimeDeviceConfig deviceConfig;
static Preferences configPrefs;
static String serialConfigLine;
static constexpr uint32_t kDeviceConfigSchema = 1;
static constexpr char kDeviceConfigNamespace[] = "homeai_cfg";

static bool looksLikePlaceholder(const String& value) {
  return value.isEmpty() ||
         value == "YOUR_WIFI" ||
         value == "YOUR_PASSWORD" ||
         value.indexOf("192.168.1.10") >= 0;
}

static bool validateDeviceConfig(const RuntimeDeviceConfig& cfg) {
  return !cfg.wifiSsid.isEmpty() &&
         !cfg.wifiPassword.isEmpty() &&
         !cfg.gatewayHost.isEmpty() &&
         cfg.gatewayPort > 0 &&
         !cfg.gatewayPath.isEmpty();
}

static void printDeviceConfig() {
  Serial.println("[CONFIG] StickS3 persistent NVS:");
  Serial.printf("[CONFIG]   schema=%u\n",
                static_cast<unsigned>(kDeviceConfigSchema));
  Serial.printf("[CONFIG]   wifi_ssid=%s\n", deviceConfig.wifiSsid.c_str());
  Serial.printf("[CONFIG]   wifi_password=%s\n",
                deviceConfig.wifiPassword.isEmpty() ? "MISSING" : "SET");
  Serial.printf("[CONFIG]   gateway=ws://%s:%u%s\n",
                deviceConfig.gatewayHost.c_str(),
                static_cast<unsigned>(deviceConfig.gatewayPort),
                deviceConfig.gatewayPath.c_str());
  Serial.printf("[CONFIG]   device_name=%s\n", deviceConfig.deviceName.c_str());
  Serial.printf("[CONFIG]   provisioned=%s\n",
                deviceConfig.provisioned ? "YES" : "NO");
}

static bool persistDeviceConfig(const RuntimeDeviceConfig& cfg) {
  if (!validateDeviceConfig(cfg)) return false;

  configPrefs.putUInt("schema", kDeviceConfigSchema);
  configPrefs.putString("wifi_ssid", cfg.wifiSsid);
  configPrefs.putString("wifi_pass", cfg.wifiPassword);
  configPrefs.putString("gateway_host", cfg.gatewayHost);
  configPrefs.putUShort("gateway_port", cfg.gatewayPort);
  configPrefs.putString("gateway_path", cfg.gatewayPath);
  configPrefs.putString("device_name", cfg.deviceName);
  return true;
}

static void loadPersistentDeviceConfig() {
  configPrefs.begin(kDeviceConfigNamespace, false);

  const uint32_t schema = configPrefs.getUInt("schema", 0);
  if (schema == kDeviceConfigSchema) {
    deviceConfig.wifiSsid = configPrefs.getString("wifi_ssid", "");
    deviceConfig.wifiPassword = configPrefs.getString("wifi_pass", "");
    deviceConfig.gatewayHost = configPrefs.getString("gateway_host", "");
    deviceConfig.gatewayPort = configPrefs.getUShort("gateway_port", 8765);
    deviceConfig.gatewayPath = configPrefs.getString("gateway_path", "/companion");
    deviceConfig.deviceName = configPrefs.getString("device_name", "HomeAIAgent");
    deviceConfig.provisioned = validateDeviceConfig(deviceConfig);
    if (deviceConfig.provisioned) {
      Serial.println("[CONFIG] loaded StickS3 config from NVS");
      printDeviceConfig();
      return;
    }
  }

  // One-time migration from old firmware compile-time secrets.
  RuntimeDeviceConfig migrated;
  migrated.wifiSsid = WIFI_SSID;
  migrated.wifiPassword = WIFI_PASSWORD;
  migrated.gatewayHost = GATEWAY_HOST;
  migrated.gatewayPort = GATEWAY_PORT;
  migrated.gatewayPath = GATEWAY_PATH;
  migrated.deviceName = "HomeAIAgent";

  if (!looksLikePlaceholder(migrated.wifiSsid) &&
      !looksLikePlaceholder(migrated.wifiPassword) &&
      !looksLikePlaceholder(migrated.gatewayHost) &&
      validateDeviceConfig(migrated)) {
    if (persistDeviceConfig(migrated)) {
      deviceConfig = migrated;
      deviceConfig.provisioned = true;
      Serial.println("[CONFIG] migrated compile-time secrets -> NVS");
      printDeviceConfig();
      return;
    }
  }

  deviceConfig = RuntimeDeviceConfig{};
  deviceConfig.provisioned = false;
  Serial.println("[CONFIG] device is not provisioned.");
  Serial.println("[CONFIG] terminal setup required");
}

static void handleConfigJson(const String& jsonText) {
  JsonDocument doc;
  const DeserializationError err = deserializeJson(doc, jsonText);
  if (err) {
    Serial.printf("[CONFIG] JSON error: %s\n", err.c_str());
    return;
  }

  RuntimeDeviceConfig next = deviceConfig;
  if (doc["wifi_ssid"].is<const char*>())
    next.wifiSsid = doc["wifi_ssid"].as<const char*>();
  if (doc["wifi_password"].is<const char*>())
    next.wifiPassword = doc["wifi_password"].as<const char*>();
  if (doc["gateway_host"].is<const char*>())
    next.gatewayHost = doc["gateway_host"].as<const char*>();
  if (doc["gateway_port"].is<uint16_t>())
    next.gatewayPort = doc["gateway_port"].as<uint16_t>();
  if (doc["gateway_path"].is<const char*>())
    next.gatewayPath = doc["gateway_path"].as<const char*>();
  if (doc["device_name"].is<const char*>())
    next.deviceName = doc["device_name"].as<const char*>();

  if (!validateDeviceConfig(next)) {
    Serial.println("[CONFIG] invalid configuration; not saved");
    return;
  }

  if (!persistDeviceConfig(next)) {
    Serial.println("[CONFIG] NVS write failed");
    return;
  }

  deviceConfig = next;
  deviceConfig.provisioned = true;
  Serial.println("[CONFIG] SAVED");
  printDeviceConfig();
  Serial.println("[CONFIG] rebooting to apply...");
  Serial.flush();
  delay(250);
  ESP.restart();
}

static void handleSerialConfig() {
  while (Serial.available()) {
    const char ch = static_cast<char>(Serial.read());
    if (ch == '\r') continue;
    if (ch != '\n') {
      if (serialConfigLine.length() < 1024) serialConfigLine += ch;
      continue;
    }

    serialConfigLine.trim();
    if (serialConfigLine == "CFG SHOW") {
      printDeviceConfig();
    }
    else if (serialConfigLine == "CFG RESET") {
      Serial.println("[CONFIG] FACTORY RESET requested");
      configPrefs.clear();
      Serial.println("[CONFIG] cleared NVS; rebooting");
      Serial.flush();
      delay(250);
      ESP.restart();
    }
    else if (serialConfigLine.startsWith("CFG ")) {
      handleConfigJson(serialConfigLine.substring(4));
    }
    else if (!serialConfigLine.isEmpty()) {
      Serial.println("[CONFIG] commands: CFG SHOW | CFG {json} | CFG RESET");
    }
    serialConfigLine = "";
  }
}
#endif

// The Gateway pre-renders each complete 128x64 monochrome Glass2 frame.
// StickS3 only caches and displays it, so arbitrary Chinese headlines do not
// require a full CJK font inside firmware.
static constexpr size_t INFO_FRAME_BYTES = 128 * 64 / 8;
static constexpr size_t INFO_MAX_ITEMS = 20;

static InfoItem infoItems[INFO_MAX_ITEMS];
static uint8_t infoFrames[INFO_MAX_ITEMS][INFO_FRAME_BYTES];
static size_t infoItemCount = 0;

// Two-phase sync: keep the current feed intact until every incoming item/frame
// has arrived successfully.
static InfoItem pendingInfoItems[INFO_MAX_ITEMS];
static uint8_t pendingInfoFrames[INFO_MAX_ITEMS][INFO_FRAME_BYTES];
static bool pendingInfoReceived[INFO_MAX_ITEMS] = {};
static size_t pendingInfoExpected = 0;
static String pendingInfoRevision;

static void initFallbackInfoItems() {
  infoItemCount = 1;

  infoItems[0].id = "system_wait_info";
  infoItems[0].category = "系统";
  infoItems[0].headline = "正在等待下一次资讯同步";
  infoItems[0].hasFrame = false;
}

M5UnitGLASS2 glass2;
bool glass2Ready = false;

// Diagnostic mode for isolating Glass2 UI-traffic and shared-power audio noise.
// B cycles the three diagnostic modes. A remains push-to-talk.
enum class GlassAudioDiagMode : uint8_t {
  Normal = 0,      // Glass2 powered + normal UI updates
  UiFrozen = 1,    // Glass2 powered, but no I2C/display writes during the voice turn
  PowerOff = 2     // Glass2 EXT 5V off for the entire voice turn
};

GlassAudioDiagMode glassDiagMode = GlassAudioDiagMode::Normal;
bool glassUiFrozen = false;
bool glassPowerCutForVoice = false;

#if COMPANION_AUDIO_ENABLE
static int16_t micRing[AUDIO_MIC_RING_BLOCKS][AUDIO_MIC_BLOCK_SAMPLES];
static bool micStreaming = false;
static uint32_t micAcceptedBlocks = 0;
static uint32_t micBufferedBlocks = 0;
static uint32_t pttStartedMs = 0;

// Keep the complete utterance in PSRAM during capture, then transmit only after
// Mic/I2S has stopped. This avoids overlapping mic capture with Wi-Fi RF peaks.
static uint8_t* pttCaptureBuffer = nullptr;
static size_t pttCaptureBytes = 0;
static bool pttCaptureOverflow = false;

// Gapless segmented TTS pipeline.
// M5Unified Speaker channel 0 exposes a two-request queue. Keep two PSRAM
// buffers alive and alternate them so segment N+1 is already queued before
// segment N finishes.
struct TtsSlot {
  uint8_t* data = nullptr;
  size_t expectedBytes = 0;
  size_t receivedBytes = 0;
  uint32_t sampleRate = 16000;
  uint16_t segmentIndex = 0;
  uint16_t segmentTotal = 0;
  bool receiving = false;
};

static TtsSlot ttsSlots[2];
static int8_t ttsReceiveSlot = -1;
static int8_t ttsCurrentSlot = -1;
static int8_t ttsNextSlot = -1;
static size_t ttsLastQueueDepth = 0;
static bool ttsSequenceActive = false;
static constexpr uint8_t kTtsSpeakerChannel = 0;

#if HOMEAI_WAKEWORD_ENABLE
// Local wake-word path.
// Local command-only MultiNet wake phrase: "逐光逐光".
// Wake listening stays entirely local.
static volatile bool wakeWordDetected = false;
static bool wakeEngineReady = false;
static bool wakeListening = false;
static bool wakeRecognizerPaused = true;

static int16_t wakeRing[AUDIO_MIC_RING_BLOCKS][AUDIO_MIC_BLOCK_SAMPLES];
static uint32_t wakeAcceptedBlocks = 0;
static uint32_t wakeFedBlocks = 0;
static uint32_t wakeNoiseFloor = 0;

// Wake audio-feed watchdog. M5Unified's half-duplex Mic can
// occasionally report a running state after a voice/error transition while
// no new record blocks are actually being accepted. Track real feed progress
// and rebuild only the Mic -> ESP-SR pipe if it stalls.
static uint32_t wakeLastAudioProgressMs = 0;
static uint32_t wakeLastRecoveryMs = 0;
static uint32_t wakeRecoveryCount = 0;
static constexpr uint32_t WAKE_FEED_STALL_MS = 1200;
static constexpr uint32_t WAKE_RECOVERY_COOLDOWN_MS = 2500;

// Hands-free capture state after a local wake event.
static bool autoWakeCaptureActive = false;
static bool autoWakeSpeechStarted = false;
static bool autoWakePttStartSent = false;
static uint32_t autoWakeCaptureStartedMs = 0;
static uint32_t autoWakeLastVoiceMs = 0;
static uint32_t autoWakeLongestSilenceMs = 0;
static uint32_t autoWakeLastVadLevel = 0;
static bool autoWakeVadVoice = false;
static size_t autoWakeTrimOffsetBytes = 0;

static constexpr uint32_t AUTO_WAKE_WAIT_SPEECH_MS = 3500;
static constexpr uint32_t AUTO_WAKE_END_SILENCE_MS = 3000;
static constexpr uint32_t AUTO_WAKE_MAX_CAPTURE_MS = 10000;
static constexpr uint32_t AUTO_WAKE_MIN_CAPTURE_MS = 700;
static constexpr uint32_t AUTO_WAKE_PREROLL_MS = 300;
static constexpr uint32_t AUTO_WAKE_MIN_VOICE_LEVEL = 450;
#endif
#endif

// Reset forensics.
// RTC_NOINIT survives most software/panic/watchdog resets, so after a reboot
// we can print the last critical stage even when USB CDC disappeared before
// the crash text reached PlatformIO.
struct ResetBreadcrumb {
  uint32_t magic;
  uint32_t checkpoint;
  uint32_t ms;
  uint32_t micBlocks;
  uint32_t sequence;
  uint32_t freeHeap;
  uint32_t minHeap;
  uint32_t freePsram;
  uint32_t freeInternal;
  uint32_t largestInternal;
  uint32_t freeDma;
  uint32_t glassMode;
};

static constexpr uint32_t kDiagMagic = 0xA272D1A6u;
RTC_NOINIT_ATTR ResetBreadcrumb rtcDiag;

enum : uint32_t {
  CP_BOOT_READY          = 10,
  CP_GLASS_POWER_OFF_PRE = 100,
  CP_GLASS_POWER_OFF_OK  = 110,
  CP_GLASS_POWER_ON_PRE  = 120,
  CP_GLASS_POWER_ON_OK   = 130,

  CP_PTT_ENTER           = 200,
  CP_PTT_ISOLATION_DONE  = 210,
  CP_PTT_AUDIO_IDLE      = 220,
  CP_MIC_BEGIN_PRE       = 230,
  CP_MIC_BEGIN_OK        = 240,
  CP_MIC_CODEC_OK        = 250,
  CP_PTT_EVENT_PRE       = 260,
  CP_PTT_RUNNING         = 270,
  CP_MIC_RECORD_PRE      = 271,
  CP_MIC_RECORD_OK       = 272,
  CP_WS_BIN_PRE          = 273,
  CP_WS_BIN_POST         = 274,

  CP_PTT_FINISH_ENTER    = 300,
  CP_MIC_DRAIN_DONE      = 310,
  CP_MIC_END_DONE        = 320,
  CP_MIC_FLUSH_DONE      = 330,
  CP_PTT_TX_BEGIN        = 331,
  CP_PTT_TX_PRE          = 332,
  CP_PTT_TX_POST         = 333,
  CP_PTT_TX_DONE         = 334,
  CP_PTT_STOP_SENT       = 340,

  CP_TTS_RX_START        = 400,
  CP_SPK_BEGIN_PRE       = 410,
  CP_SPK_BEGIN_OK        = 420,
  CP_PLAYRAW_OK          = 430,
  CP_PLAYBACK_DONE       = 440,
};

static const char* checkpointName(uint32_t cp) {
  switch (cp) {
    case CP_BOOT_READY:          return "BOOT_READY";
    case CP_GLASS_POWER_OFF_PRE: return "GLASS_POWER_OFF_PRE";
    case CP_GLASS_POWER_OFF_OK:  return "GLASS_POWER_OFF_OK";
    case CP_GLASS_POWER_ON_PRE:  return "GLASS_POWER_ON_PRE";
    case CP_GLASS_POWER_ON_OK:   return "GLASS_POWER_ON_OK";
    case CP_PTT_ENTER:           return "PTT_ENTER";
    case CP_PTT_ISOLATION_DONE:  return "PTT_ISOLATION_DONE";
    case CP_PTT_AUDIO_IDLE:      return "PTT_AUDIO_IDLE";
    case CP_MIC_BEGIN_PRE:       return "MIC_BEGIN_PRE";
    case CP_MIC_BEGIN_OK:        return "MIC_BEGIN_OK";
    case CP_MIC_CODEC_OK:        return "MIC_CODEC_OK";
    case CP_PTT_EVENT_PRE:       return "PTT_EVENT_PRE";
    case CP_PTT_RUNNING:         return "PTT_RUNNING";
    case CP_MIC_RECORD_PRE:      return "MIC_RECORD_PRE";
    case CP_MIC_RECORD_OK:       return "MIC_RECORD_OK";
    case CP_WS_BIN_PRE:          return "WS_BIN_PRE";
    case CP_WS_BIN_POST:         return "WS_BIN_POST";
    case CP_PTT_FINISH_ENTER:    return "PTT_FINISH_ENTER";
    case CP_MIC_DRAIN_DONE:      return "MIC_DRAIN_DONE";
    case CP_MIC_END_DONE:        return "MIC_END_DONE";
    case CP_MIC_FLUSH_DONE:      return "MIC_FLUSH_DONE";
    case CP_PTT_TX_BEGIN:        return "PTT_TX_BEGIN";
    case CP_PTT_TX_PRE:          return "PTT_TX_PRE";
    case CP_PTT_TX_POST:         return "PTT_TX_POST";
    case CP_PTT_TX_DONE:         return "PTT_TX_DONE";
    case CP_PTT_STOP_SENT:       return "PTT_STOP_SENT";
    case CP_TTS_RX_START:        return "TTS_RX_START";
    case CP_SPK_BEGIN_PRE:       return "SPK_BEGIN_PRE";
    case CP_SPK_BEGIN_OK:        return "SPK_BEGIN_OK";
    case CP_PLAYRAW_OK:          return "PLAYRAW_OK";
    case CP_PLAYBACK_DONE:       return "PLAYBACK_DONE";
    default:                     return "UNKNOWN";
  }
}

static const char* resetReasonName(esp_reset_reason_t reason) {
  switch (reason) {
    case ESP_RST_UNKNOWN:   return "UNKNOWN";
    case ESP_RST_POWERON:   return "POWERON";
    case ESP_RST_EXT:       return "EXTERNAL";
    case ESP_RST_SW:        return "SOFTWARE";
    case ESP_RST_PANIC:     return "PANIC";
    case ESP_RST_INT_WDT:   return "INT_WDT";
    case ESP_RST_TASK_WDT:  return "TASK_WDT";
    case ESP_RST_WDT:       return "OTHER_WDT";
    case ESP_RST_DEEPSLEEP: return "DEEPSLEEP";
    case ESP_RST_BROWNOUT:  return "BROWNOUT";
    case ESP_RST_SDIO:      return "SDIO";
    default:                return "OTHER";
  }
}

static void diagCheckpoint(uint32_t cp, bool printNow = false, uint32_t sequence = 0) {
  rtcDiag.magic = kDiagMagic;
  rtcDiag.checkpoint = cp;
  rtcDiag.ms = millis();
#if COMPANION_AUDIO_ENABLE
  rtcDiag.micBlocks = micAcceptedBlocks;
#else
  rtcDiag.micBlocks = 0;
#endif
  rtcDiag.sequence = sequence;
  rtcDiag.freeHeap = heap_caps_get_free_size(MALLOC_CAP_8BIT);
  rtcDiag.minHeap = heap_caps_get_minimum_free_size(MALLOC_CAP_8BIT);
  rtcDiag.freePsram = heap_caps_get_free_size(MALLOC_CAP_SPIRAM);
  rtcDiag.freeInternal = heap_caps_get_free_size(MALLOC_CAP_INTERNAL | MALLOC_CAP_8BIT);
  rtcDiag.largestInternal = heap_caps_get_largest_free_block(MALLOC_CAP_INTERNAL | MALLOC_CAP_8BIT);
  rtcDiag.freeDma = heap_caps_get_free_size(MALLOC_CAP_DMA);
  rtcDiag.glassMode = static_cast<uint32_t>(glassDiagMode);

  if (printNow) {
    Serial.printf(
      "[DIAG] cp=%s(%u) ms=%u blocks=%u seq=%u heap=%u min=%u psram=%u "
      "internal=%u largestInt=%u dma=%u glass=%u\n",
      checkpointName(cp),
      static_cast<unsigned>(cp),
      static_cast<unsigned>(rtcDiag.ms),
      static_cast<unsigned>(rtcDiag.micBlocks),
      static_cast<unsigned>(rtcDiag.sequence),
      static_cast<unsigned>(rtcDiag.freeHeap),
      static_cast<unsigned>(rtcDiag.minHeap),
      static_cast<unsigned>(rtcDiag.freePsram),
      static_cast<unsigned>(rtcDiag.freeInternal),
      static_cast<unsigned>(rtcDiag.largestInternal),
      static_cast<unsigned>(rtcDiag.freeDma),
      static_cast<unsigned>(rtcDiag.glassMode));
    Serial.flush();
  }
}

static void printResetForensics() {
  const esp_reset_reason_t reason = esp_reset_reason();
  Serial.printf("[BOOT-DIAG] reset_reason=%s(%d)\n",
                resetReasonName(reason), static_cast<int>(reason));

  if (rtcDiag.magic == kDiagMagic) {
    Serial.printf(
      "[BOOT-DIAG] last_checkpoint=%s(%u) last_ms=%u micBlocks=%u seq=%u "
      "heap=%u minHeap=%u psram=%u internal=%u largestInt=%u dma=%u glassMode=%u\n",
      checkpointName(rtcDiag.checkpoint),
      static_cast<unsigned>(rtcDiag.checkpoint),
      static_cast<unsigned>(rtcDiag.ms),
      static_cast<unsigned>(rtcDiag.micBlocks),
      static_cast<unsigned>(rtcDiag.sequence),
      static_cast<unsigned>(rtcDiag.freeHeap),
      static_cast<unsigned>(rtcDiag.minHeap),
      static_cast<unsigned>(rtcDiag.freePsram),
      static_cast<unsigned>(rtcDiag.freeInternal),
      static_cast<unsigned>(rtcDiag.largestInternal),
      static_cast<unsigned>(rtcDiag.freeDma),
      static_cast<unsigned>(rtcDiag.glassMode));
  } else {
    Serial.println("[BOOT-DIAG] no breadcrumb");
  }

  Serial.printf(
      "[BOOT-DIAG] now heap=%u minHeap=%u psram=%u internal=%u largestInt=%u dma=%u\n",
      static_cast<unsigned>(heap_caps_get_free_size(MALLOC_CAP_8BIT)),
      static_cast<unsigned>(heap_caps_get_minimum_free_size(MALLOC_CAP_8BIT)),
      static_cast<unsigned>(heap_caps_get_free_size(MALLOC_CAP_SPIRAM)),
      static_cast<unsigned>(heap_caps_get_free_size(MALLOC_CAP_INTERNAL | MALLOC_CAP_8BIT)),
      static_cast<unsigned>(heap_caps_get_largest_free_block(MALLOC_CAP_INTERNAL | MALLOC_CAP_8BIT)),
      static_cast<unsigned>(heap_caps_get_free_size(MALLOC_CAP_DMA)));
  Serial.flush();
}

static const char* glassDiagLabel() {
  switch (glassDiagMode) {
    case GlassAudioDiagMode::Normal:   return "GLASS NORMAL";
    case GlassAudioDiagMode::UiFrozen: return "GLASS FREEZE";
    case GlassAudioDiagMode::PowerOff: return "GLASS POWER OFF";
  }
  return "";
}

CompanionState companionState = CompanionState::Idle;
uint32_t stateSinceMs = 0;

size_t currentItem = 0;
uint32_t itemShownSinceMs = 0;
bool infoPaused = false;

// Night screen protection.
// Wall-clock policy comes from the Mac mini Gateway.
// During the night window, any button wakes both displays immediately for
// two minutes. Core/Wi-Fi/audio/Gateway remain running throughout.
bool nightScreenWindowActive = false;
bool displaysSleeping = false;
uint32_t manualWakeUntilMs = 0;
static constexpr uint32_t NIGHT_MANUAL_WAKE_MS = 120000;

// Display execution handshake.
// The Gateway supplies command_id; the device only reports "applied" after
// the requested screen state is actually true.
String displayPolicyCommandId;
bool displayPolicyAckPending = false;
bool displayPolicyTargetSleep = false;

static void sendDisplayPolicyAck(const char* status);
static constexpr uint8_t STICKS3_ACTIVE_BRIGHTNESS = 140;
static constexpr uint8_t GLASS2_ACTIVE_BRIGHTNESS = 255;

bool lastBtnA = false;
bool lastBtnB = false;
uint32_t btnBPressedMs = 0;

uint32_t lastIdleBlinkMs = 0;
bool idleBlink = false;
uint32_t idleBlinkStartedMs = 0;

#if COMPANION_GATEWAY_ENABLE
WebSocketsClient webSocket;
bool gatewayConnected = false;
bool webSocketStarted = false;

bool wifiOnline = false;
bool wifiAttemptActive = false;
bool wifiEverConnected = false;
uint32_t wifiAttemptStartedMs = 0;
uint32_t wifiNextAttemptMs = 0;
uint8_t wifiBackoffIndex = 0;

static constexpr uint32_t WIFI_CONNECT_TIMEOUT_MS = 15000;
static constexpr uint32_t WIFI_RETRY_BACKOFF_MS[] = {
  30000,   // 30 s
  60000,   // 60 s
  120000,  // 120 s
  240000,  // 240 s
};
static constexpr size_t WIFI_RETRY_BACKOFF_COUNT =
    sizeof(WIFI_RETRY_BACKOFF_MS) / sizeof(WIFI_RETRY_BACKOFF_MS[0]);
#endif


static const char* stateLabel(CompanionState state) {
  switch (state) {
    case CompanionState::Idle:      return "STANDBY";
    case CompanionState::Listening: return "LISTEN";
    case CompanionState::Thinking:  return "THINK";
    case CompanionState::Speaking:  return "SPEAK";
    case CompanionState::Success:   return "COMPLETE";
    case CompanionState::Error:     return "FAULT";
  }
  return "";
}

// Cyber Expression renderer.
//
// The StickS3 LCD is no longer a digital-pet face.  HomeAI now uses one stable
// visual identity across all runtime states:
//   1) luminous central AI core
//   2) segmented horizontal VISOR
//   3) incomplete mechanical/data orbits
//   4) fixed cardinal locator marks
//
// Keep animation intentionally lightweight while Mic/I2S or gapless TTS is
// active.  The voice chain is the product-critical path; UI motion must never
// win a scheduling fight against capture/playback.
static uint32_t cyberLastFrameMs = 0;
static float cyberAudioLevel = 0.0f;
static float cyberSmoothedAudio = 0.0f;

// Full-frame off-screen renderer.
// All cyber UI primitives are drawn into this RGB565 canvas first, then the
// completed frame is transferred to the StickS3 LCD in one pushSprite().
// This prevents a visible clear -> redraw cycle on the physical LCD.
static M5Canvas cyberCanvas;
static bool cyberCanvasReady = false;
static bool cyberCanvasAllocFailed = false;

static uint16_t rgb565(uint8_t r, uint8_t g, uint8_t b) {
  return M5.Display.color565(r, g, b);
}

static bool ensureCyberCanvas() {
  if (cyberCanvasReady) {
    // Rotation is frozen in production, but reject a stale buffer defensively.
    if (cyberCanvas.width() == M5.Display.width() &&
        cyberCanvas.height() == M5.Display.height()) {
      return true;
    }
    cyberCanvas.deleteSprite();
    cyberCanvasReady = false;
  }
  if (cyberCanvasAllocFailed) return false;

  cyberCanvas.setPsram(true);
  cyberCanvas.setColorDepth(16);
  void* buffer = cyberCanvas.createSprite(M5.Display.width(), M5.Display.height());
  if (!buffer) {
    // One SRAM fallback is useful for development boards with PSRAM disabled.
    // Production StickS3 builds have BOARD_HAS_PSRAM and should use PSRAM.
    cyberCanvas.setPsram(false);
    cyberCanvas.setColorDepth(16);
    buffer = cyberCanvas.createSprite(M5.Display.width(), M5.Display.height());
  }

  if (!buffer) {
    cyberCanvasAllocFailed = true;
    Serial.printf("[UI] Cyber canvas allocation FAILED (%dx%d RGB565)\n",
                  M5.Display.width(), M5.Display.height());
    return false;
  }

  cyberCanvasReady = true;
  Serial.printf("[UI] Cyber canvas ready: %dx%d RGB565, %u bytes\n",
                cyberCanvas.width(), cyberCanvas.height(),
                static_cast<unsigned>(cyberCanvas.bufferLength()));
  return true;
}

static float cyberClamp01(float v) {
  if (v < 0.0f) return 0.0f;
  if (v > 1.0f) return 1.0f;
  return v;
}

static float cyberEaseOutCubic(float t) {
  t = cyberClamp01(t);
  const float p = 1.0f - t;
  return 1.0f - p * p * p;
}

static void setCyberAudioLevel(float level) {
  cyberAudioLevel = cyberClamp01(level);
}

static void cyberDrawRotArc(M5Canvas& d, int cx, int cy, int outerR, int thickness,
                            float startDeg, float sweepDeg, uint16_t color) {
  while (startDeg < 0.0f) startDeg += 360.0f;
  while (startDeg >= 360.0f) startDeg -= 360.0f;
  const float end = startDeg + sweepDeg;
  if (end <= 360.0f) {
    d.drawArc(cx, cy, outerR, outerR - thickness,
              static_cast<int>(startDeg), static_cast<int>(end), color);
  } else {
    d.drawArc(cx, cy, outerR, outerR - thickness,
              static_cast<int>(startDeg), 360, color);
    d.drawArc(cx, cy, outerR, outerR - thickness,
              0, static_cast<int>(end - 360.0f), color);
  }
}

static void cyberDrawDotPolar(M5Canvas& d, int cx, int cy, float radius, float angleDeg,
                              int radiusPx, uint16_t color) {
  constexpr float kPi = 3.14159265358979323846f;
  const float a = angleDeg * kPi / 180.0f;
  const int x = cx + static_cast<int>(cosf(a) * radius);
  const int y = cy + static_cast<int>(sinf(a) * radius);
  d.fillCircle(x, y, radiusPx, color);
}

static void cyberDrawCore(M5Canvas& d, int cx, int cy, float pulse, uint16_t coreColor) {
  pulse = cyberClamp01(pulse);
  const int glowR = 9 + static_cast<int>(pulse * 3.0f);

  d.fillCircle(cx, cy, glowR + 8, rgb565(0, 8, 24));
  d.fillCircle(cx, cy, glowR + 5, rgb565(0, 27, 61));
  d.fillCircle(cx, cy, glowR + 2, rgb565(0, 72, 139));
  d.fillCircle(cx, cy, 7 + static_cast<int>(pulse * 2.0f), coreColor);
  d.fillCircle(cx, cy, 3, rgb565(239, 250, 255));

  d.drawCircle(cx, cy, 16, rgb565(13, 85, 144));
  d.drawCircle(cx, cy, 20, rgb565(5, 39, 80));
  d.drawCircle(cx, cy, 23, rgb565(3, 23, 52));
}

static void cyberDrawVisor(M5Canvas& d, int cx, int cy, int halfWidth,
                           uint16_t color, uint16_t glow,
                           int yOffsetLeft = 0, int yOffsetRight = 0) {
  const int gap = 12;
  const int xL0 = cx - halfWidth;
  const int xL1 = cx - gap;
  const int xR0 = cx + gap;
  const int xR1 = cx + halfWidth;

  d.drawFastHLine(xL0, cy - 2 + yOffsetLeft, xL1 - xL0, glow);
  d.drawFastHLine(xL0, cy + 2 + yOffsetLeft, xL1 - xL0, glow);
  d.drawFastHLine(xR0, cy - 2 + yOffsetRight, xR1 - xR0, glow);
  d.drawFastHLine(xR0, cy + 2 + yOffsetRight, xR1 - xR0, glow);

  d.drawFastHLine(xL0, cy + yOffsetLeft, xL1 - xL0, color);
  d.drawFastHLine(xR0, cy + yOffsetRight, xR1 - xR0, color);
  d.drawFastHLine(xL0 + 3, cy - 1 + yOffsetLeft,
                  xL1 - xL0 - 6, rgb565(121, 227, 255));
  d.drawFastHLine(xR0 + 3, cy - 1 + yOffsetRight,
                  xR1 - xR0 - 6, rgb565(121, 227, 255));

  d.drawFastVLine(xL0, cy - 4 + yOffsetLeft, 9, rgb565(9, 75, 126));
  d.drawFastVLine(xR1, cy - 4 + yOffsetRight, 9, rgb565(9, 75, 126));
  d.drawPixel(xL0 - 2, cy + yOffsetLeft, rgb565(106, 220, 255));
  d.drawPixel(xR1 + 2, cy + yOffsetRight, rgb565(106, 220, 255));
}

static void cyberDrawOrbitBase(M5Canvas& d, int cx, int cy, uint32_t now, float energy,
                               uint16_t midColor = 0) {
  const float drift = fmodf(now * 0.018f, 360.0f);
  const uint16_t dim = rgb565(3, 34, 66);
  const uint16_t mid = midColor ? midColor : rgb565(7, 78, 137);

  cyberDrawRotArc(d, cx, cy, 29, 2, 205 + drift * 0.08f, 58, dim);
  cyberDrawRotArc(d, cx, cy, 29, 2, 25 + drift * 0.08f, 58, dim);
  cyberDrawRotArc(d, cx, cy, 36, 2, 224 - drift * 0.05f, 42, mid);
  cyberDrawRotArc(d, cx, cy, 36, 2, 44 - drift * 0.05f, 42, mid);

  // HomeAI visual DNA: fixed cardinal locator marks remain in every state.
  d.drawFastVLine(cx, cy - 49, 8, rgb565(20, 94, 155));
  d.drawFastVLine(cx, cy + 42, 8, rgb565(20, 94, 155));
  d.drawFastHLine(cx - 50, cy, 7, rgb565(20, 94, 155));
  d.drawFastHLine(cx + 43, cy, 7, rgb565(20, 94, 155));

  if (energy > 0.25f) {
    cyberDrawDotPolar(d, cx, cy, 40, 305 + drift, 1, rgb565(29, 156, 237));
    cyberDrawDotPolar(d, cx, cy, 40, 125 + drift, 1, rgb565(29, 156, 237));
  }
}

static void cyberDrawWaveCluster(M5Canvas& d, int x0, int cy, int direction,
                                 float intensity, uint32_t now,
                                 uint16_t primary, uint16_t secondary) {
  constexpr int bars = 6;
  const float phase = now * 0.012f;
  for (int i = 0; i < bars; ++i) {
    const float local = 0.35f + 0.65f * fabsf(sinf(phase + i * 0.78f));
    const int barH = 3 + static_cast<int>(intensity * local * (7 + i));
    const int x = x0 + direction * i * 3;
    d.drawFastVLine(x, cy - barH / 2, barH, i < 2 ? primary : secondary);
  }
}

static void cyberDrawChrome(M5Canvas& d, uint16_t accent, uint16_t bg, const char* label) {
  const int w = d.width();
  const int h = d.height();
  const uint16_t frame = rgb565(5, 23, 42);
  const uint16_t muted = rgb565(57, 105, 139);

  // Sparse industrial frame.  It adds identity without turning into HUD clutter.
  d.drawFastHLine(8, 14, 15, frame);
  d.drawFastVLine(8, 14, 10, frame);
  d.drawFastHLine(w - 23, 14, 15, frame);
  d.drawFastVLine(w - 9, 14, 10, frame);
  d.drawFastHLine(8, h - 16, 15, frame);
  d.drawFastVLine(8, h - 25, 10, frame);
  d.drawFastHLine(w - 23, h - 16, 15, frame);
  d.drawFastVLine(w - 9, h - 25, 10, frame);

  d.setTextWrap(false);
  d.setFont(&fonts::efontCN_10);
  d.setTextSize(1);
  d.setTextDatum(top_left);
  d.setTextColor(muted, bg);
  d.drawString("HOMEAI", 10, 25);
  d.setTextDatum(top_right);
  d.drawString("NODE/01", w - 10, 25);

  d.setTextDatum(middle_center);
  d.setTextColor(accent, bg);
  d.drawString(label, w / 2, 204);
  d.setTextColor(muted, bg);
  d.drawString("A:TALK  B:INFO", w / 2, 222);
}

static void cyberDrawIdle(M5Canvas& d, int cx, int cy, uint32_t now) {
  const float breathing = 0.5f + 0.5f * sinf(now * 0.0018f);
  cyberDrawOrbitBase(d, cx, cy, now, 0.22f);
  cyberDrawVisor(d, cx, cy, 46, rgb565(34, 137, 221), rgb565(0, 23, 55));
  cyberDrawCore(d, cx, cy, 0.20f + breathing * 0.22f, rgb565(87, 210, 255));

  if (((now / 900U) & 1U) == 0U) {
    d.drawPixel(cx, cy + 59, rgb565(52, 132, 184));
  }
}

static void cyberDrawListening(M5Canvas& d, int cx, int cy, uint32_t now) {
  const float autoBreath = 0.18f + 0.12f * (0.5f + 0.5f * sinf(now * 0.007f));
  const float e = cyberClamp01(cyberSmoothedAudio + autoBreath);
  cyberDrawOrbitBase(d, cx, cy, now, 0.55f + e * 0.3f);
  cyberDrawVisor(d, cx, cy, 43, rgb565(48, 170, 245), rgb565(0, 34, 72));
  cyberDrawCore(d, cx, cy, 0.35f + e * 0.52f, rgb565(105, 226, 255));

  for (int i = 0; i < 3; ++i) {
    const int r = 7 + i * 6 + static_cast<int>(e * 2.0f);
    const uint16_t c = i == 0 ? rgb565(123, 231, 255)
                               : rgb565(18, 103 + i * 20, 177 + i * 17);
    d.drawArc(cx - 47, cy, r, r - 1, 300, 360, c);
    d.drawArc(cx - 47, cy, r, r - 1, 0, 60, c);
    d.drawArc(cx + 47, cy, r, r - 1, 120, 240, c);
  }

  const int travel = static_cast<int>((now / 24U) % 23U);
  d.fillCircle(cx - 60 + travel, cy, 1, rgb565(130, 235, 255));
  d.fillCircle(cx + 60 - travel, cy, 1, rgb565(130, 235, 255));
}

static void cyberDrawThinking(M5Canvas& d, int cx, int cy, uint32_t now) {
  const float t = now * 0.035f;
  cyberDrawOrbitBase(d, cx, cy, now, 1.0f);
  cyberDrawVisor(d, cx, cy, 42, rgb565(35, 130, 220), rgb565(0, 26, 64));
  cyberDrawCore(d, cx, cy, 0.48f + 0.14f * sinf(now * 0.004f), rgb565(81, 205, 255));

  cyberDrawRotArc(d, cx, cy, 42, 3, fmodf(t, 360.0f), 56, rgb565(31, 153, 238));
  cyberDrawRotArc(d, cx, cy, 42, 2, fmodf(t + 142.0f, 360.0f), 26, rgb565(119, 228, 255));
  cyberDrawRotArc(d, cx, cy, 47, 2, fmodf(320.0f - t * 0.72f, 360.0f), 68,
                  rgb565(14, 85, 162));

  cyberDrawDotPolar(d, cx, cy, 46, fmodf(t * 1.8f, 360.0f), 2, rgb565(121, 230, 255));
  cyberDrawDotPolar(d, cx, cy, 39, fmodf(260.0f - t * 1.25f, 360.0f), 1,
                    rgb565(232, 248, 255));

  for (int i = 0; i < 6; ++i) {
    const int x = cx - 21 + i * 8;
    const int len = 2 + ((i + (now / 120U)) % 3U);
    d.drawFastVLine(x, cy + 54, len, rgb565(12, 72, 130));
  }
}

static void cyberDrawSpeaking(M5Canvas& d, int cx, int cy, uint32_t now) {
  const float autoVoice = 0.28f + 0.23f * (0.5f + 0.5f * sinf(now * 0.010f));
  const float e = cyberClamp01(cyberSmoothedAudio * 0.9f + autoVoice);
  cyberDrawOrbitBase(d, cx, cy, now, 0.72f);
  cyberDrawVisor(d, cx, cy, 39, rgb565(42, 154, 238), rgb565(0, 31, 70));
  cyberDrawCore(d, cx, cy, 0.44f + e * 0.45f, rgb565(111, 232, 255));

  cyberDrawWaveCluster(d, cx - 48, cy, -1, e, now,
                       rgb565(128, 233, 255), rgb565(24, 112, 201));
  cyberDrawWaveCluster(d, cx + 48, cy, +1, e, now,
                       rgb565(128, 233, 255), rgb565(24, 112, 201));
}

static void cyberDrawSuccess(M5Canvas& d, int cx, int cy, uint32_t now) {
  const uint16_t ok = rgb565(99, 238, 218);
  cyberDrawOrbitBase(d, cx, cy, now, 0.72f, rgb565(14, 110, 112));
  cyberDrawVisor(d, cx, cy, 42, ok, rgb565(0, 38, 48));
  cyberDrawCore(d, cx, cy, 0.58f, rgb565(133, 255, 235));

  // Completion is shown as mechanical alignment, not a software check icon.
  cyberDrawRotArc(d, cx, cy, 45, 2, 8, 74, rgb565(42, 174, 170));
  cyberDrawRotArc(d, cx, cy, 45, 2, 98, 74, rgb565(42, 174, 170));
  cyberDrawRotArc(d, cx, cy, 45, 2, 188, 74, rgb565(42, 174, 170));
  cyberDrawRotArc(d, cx, cy, 45, 2, 278, 74, rgb565(42, 174, 170));
}

static void cyberDrawError(M5Canvas& d, int cx, int cy, uint32_t now) {
  const uint16_t red = rgb565(255, 73, 91);
  const uint16_t redDim = rgb565(112, 17, 31);
  cyberDrawOrbitBase(d, cx, cy, now, 0.35f, redDim);

  // A fault is the same HomeAI face losing alignment, not a separate icon.
  cyberDrawVisor(d, cx, cy, 44, red, rgb565(48, 0, 8), -2, +3);
  cyberDrawCore(d, cx, cy, 0.36f, rgb565(255, 91, 105));
  cyberDrawRotArc(d, cx, cy, 42, 3, 212, 45, red);
  cyberDrawRotArc(d, cx, cy, 47, 2, 18, 51, redDim);
  cyberDrawRotArc(d, cx, cy, 39, 2, 104, 34, red);

  // Deterministic short glitch bars: visible, but never a full-screen strobe.
  const int phase = static_cast<int>((now / 120U) % 4U);
  d.drawFastHLine(cx - 52 + phase * 2, cy - 31, 17, redDim);
  d.drawFastHLine(cx + 25 - phase, cy + 28, 24, redDim);
  d.drawFastHLine(cx - 18, cy + 50 + (phase & 1), 31, red);
}

static uint32_t cyberFrameIntervalMs() {
  // Conservative refresh while the half-duplex audio path is active.
  switch (companionState) {
    case CompanionState::Listening: return 160;  // ~6 FPS during Mic capture
    case CompanionState::Speaking:  return 125;  // 8 FPS during gapless TTS
    case CompanionState::Thinking:  return 50;   // 20 FPS when audio is idle
    case CompanionState::Success:   return 80;
    case CompanionState::Error:     return 100;
    case CompanionState::Idle:
    default:                        return 100;   // restrained ambient motion
  }
}

static void renderCyberExpression(CompanionState state, uint32_t now) {
  if (displaysSleeping) return;
  if (!ensureCyberCanvas()) return;

  auto& d = cyberCanvas;
  const int w = d.width();
  const int cx = w / 2;
  const int cy = 112;

  const uint16_t bg = rgb565(1, 5, 11);
  const uint16_t normalAccent = rgb565(75, 185, 246);
  const uint16_t successAccent = rgb565(99, 238, 218);
  const uint16_t errorAccent = rgb565(255, 73, 91);
  const uint16_t accent = state == CompanionState::Success ? successAccent
                           : state == CompanionState::Error ? errorAccent
                           : normalAccent;

  // IMPORTANT: clear only the off-screen canvas. The physical LCD keeps the
  // previous complete frame until pushSprite() below replaces it atomically
  // from the user's point of view.
  d.fillSprite(bg);

  const float transition = cyberEaseOutCubic((now - stateSinceMs) / 360.0f);
  const int reveal = 14 + static_cast<int>(30.0f * transition);
  const uint16_t transitionColor = state == CompanionState::Error
                                       ? rgb565(76, 10, 22)
                                       : rgb565(10, 59, 105);
  d.drawArc(cx, cy, reveal, reveal - 1, 210, 330, transitionColor);
  d.drawArc(cx, cy, reveal, reveal - 1, 30, 150, transitionColor);

  // Long vertical sensor axis makes the composition recognisable even in a
  // peripheral glance beside the monitor.
  d.drawFastVLine(cx, 45, 15, rgb565(11, 63, 105));
  d.drawFastVLine(cx, 164, 15, rgb565(11, 63, 105));
  d.drawPixel(cx, 65, accent);
  d.drawPixel(cx, 184, accent);

  switch (state) {
    case CompanionState::Idle:      cyberDrawIdle(d, cx, cy, now); break;
    case CompanionState::Listening: cyberDrawListening(d, cx, cy, now); break;
    case CompanionState::Thinking:  cyberDrawThinking(d, cx, cy, now); break;
    case CompanionState::Speaking:  cyberDrawSpeaking(d, cx, cy, now); break;
    case CompanionState::Success:   cyberDrawSuccess(d, cx, cy, now); break;
    case CompanionState::Error:     cyberDrawError(d, cx, cy, now); break;
  }

  cyberDrawChrome(d, accent, bg, stateLabel(state));

  // Single physical display transaction per frame. No visible fillScreen().
  M5.Display.startWrite();
  d.pushSprite(&M5.Display, 0, 0);
  M5.Display.endWrite();
}

static void drawMascotFace(CompanionState state, bool blink = false) {
  (void)blink;
  if (displaysSleeping) return;
  renderCyberExpression(state, millis());
  cyberLastFrameMs = millis();
}

static void updateCyberExpression() {
  if (displaysSleeping) return;
  const uint32_t now = millis();
  if (now - cyberLastFrameMs < cyberFrameIntervalMs()) return;
  cyberLastFrameMs = now;

  // Audio-reactive hook.  Current production voice path intentionally does not
  // perform extra RMS passes solely for UI.  The autonomous envelope keeps
  // Listening/Speaking alive without adding work to Mic/TTS critical sections.
  cyberSmoothedAudio += (cyberAudioLevel - cyberSmoothedAudio) * 0.34f;
  cyberAudioLevel *= 0.87f;
  renderCyberExpression(companionState, now);
}

static void setState(CompanionState state) {
  if (companionState == state) return;
  companionState = state;
  stateSinceMs = millis();
  idleBlink = false;

  // Force the next cyber frame immediately after a state transition.
  cyberLastFrameMs = 0;
}


// Return the next UTF-8 character boundary after byte index i.
static size_t nextUtf8Boundary(const String& s, size_t i) {
  if (i >= s.length()) return s.length();
  const uint8_t c = static_cast<uint8_t>(s[i]);
  size_t n = 1;
  if ((c & 0xE0) == 0xC0) n = 2;
  else if ((c & 0xF0) == 0xE0) n = 3;
  else if ((c & 0xF8) == 0xF0) n = 4;
  size_t next = i + n;
  if (next > s.length()) next = s.length();
  return next;
}

static bool decodeUtf8At(const String& s, size_t start, uint32_t& cp, size_t& next) {
  if (start >= s.length()) return false;
  const uint8_t c0 = static_cast<uint8_t>(s[start]);
  if ((c0 & 0x80) == 0) {
    cp = c0;
    next = start + 1;
    return true;
  }
  if ((c0 & 0xE0) == 0xC0 && start + 1 < s.length()) {
    const uint8_t c1 = static_cast<uint8_t>(s[start + 1]);
    cp = ((c0 & 0x1F) << 6) | (c1 & 0x3F);
    next = start + 2;
    return true;
  }
  if ((c0 & 0xF0) == 0xE0 && start + 2 < s.length()) {
    const uint8_t c1 = static_cast<uint8_t>(s[start + 1]);
    const uint8_t c2 = static_cast<uint8_t>(s[start + 2]);
    cp = ((c0 & 0x0F) << 12) | ((c1 & 0x3F) << 6) | (c2 & 0x3F);
    next = start + 3;
    return true;
  }
  if ((c0 & 0xF8) == 0xF0 && start + 3 < s.length()) {
    const uint8_t c1 = static_cast<uint8_t>(s[start + 1]);
    const uint8_t c2 = static_cast<uint8_t>(s[start + 2]);
    const uint8_t c3 = static_cast<uint8_t>(s[start + 3]);
    cp = ((c0 & 0x07) << 18) | ((c1 & 0x3F) << 12) |
         ((c2 & 0x3F) << 6) | (c3 & 0x3F);
    next = start + 4;
    return true;
  }
  cp = '?';
  next = nextUtf8Boundary(s, start);
  return true;
}


static const Glass2ZhGlyph14* findHeaderGlyph(uint32_t cp) {
  for (size_t i = 0; i < kGlass2ZhHeaderGlyphCount; ++i) {
    if (kGlass2ZhHeaderGlyphs[i].codepoint == cp) return &kGlass2ZhHeaderGlyphs[i];
  }
  return nullptr;
}

static const Glass2ZhGlyph12* findBodyGlyph(uint32_t cp) {
  for (size_t i = 0; i < kGlass2ZhBodyGlyphCount; ++i) {
    if (kGlass2ZhBodyGlyphs[i].codepoint == cp) return &kGlass2ZhBodyGlyphs[i];
  }
  return nullptr;
}

static int monoBodyCharWidth(uint32_t cp) {
  if (cp == ' ') return 4;
  if (cp < 128) return 6;
  return GLASS2_ZH_BODY_W;
}

static int measureMonoBodyText(const String& s) {
  int width = 0;
  size_t cursor = 0;
  while (cursor < s.length()) {
    uint32_t cp = 0;
    size_t next = cursor;
    if (!decodeUtf8At(s, cursor, cp, next)) break;
    width += monoBodyCharWidth(cp);
    cursor = next;
  }
  return width;
}

static size_t fitUtf8RangeMonoBody(const String& s, size_t start, int maxWidth) {
  size_t cursor = start;
  size_t lastFit = start;
  int width = 0;
  while (cursor < s.length()) {
    uint32_t cp = 0;
    size_t next = cursor;
    if (!decodeUtf8At(s, cursor, cp, next)) break;
    const int w = monoBodyCharWidth(cp);
    if (width + w > maxWidth) break;
    width += w;
    lastFit = next;
    cursor = next;
  }
  return lastFit;
}

static void drawMonoHeaderText(int x, int y, const String& s) {
  size_t cursor = 0;
  while (cursor < s.length()) {
    uint32_t cp = 0;
    size_t next = cursor;
    if (!decodeUtf8At(s, cursor, cp, next)) break;

    if (cp < 128) {
      glass2.setFont(&fonts::Font0);
      glass2.setTextDatum(top_left);
      char buf[2] = { static_cast<char>(cp), 0 };
      glass2.drawString(buf, x, y + 3);
      x += 6;
    } else {
      const Glass2ZhGlyph14* glyph = findHeaderGlyph(cp);
      if (glyph) {
        for (int row = 0; row < GLASS2_ZH_HEADER_H; ++row) {
          const uint16_t bits = glyph->rows[row];
          for (int col = 0; col < GLASS2_ZH_HEADER_W; ++col) {
            if (bits & (1 << (GLASS2_ZH_HEADER_W - 1 - col))) {
              glass2.drawPixel(x + col, y + row, TFT_WHITE);
            }
          }
        }
      }
      x += GLASS2_ZH_HEADER_W;
    }
    cursor = next;
  }
}

static void drawMonoBodyText(int x, int y, const String& s) {
  size_t cursor = 0;
  while (cursor < s.length()) {
    uint32_t cp = 0;
    size_t next = cursor;
    if (!decodeUtf8At(s, cursor, cp, next)) break;

    if (cp < 128) {
      glass2.setFont(&fonts::Font0);
      glass2.setTextDatum(top_left);
      char buf[2] = { static_cast<char>(cp), 0 };
      glass2.drawString(buf, x, y + 2);
      x += 6;
    } else {
      const Glass2ZhGlyph12* glyph = findBodyGlyph(cp);
      if (glyph) {
        for (int row = 0; row < GLASS2_ZH_BODY_H; ++row) {
          const uint16_t bits = glyph->rows[row];
          for (int col = 0; col < GLASS2_ZH_BODY_W; ++col) {
            if (bits & (1 << (GLASS2_ZH_BODY_W - 1 - col))) {
              glass2.drawPixel(x + col, y + row, TFT_WHITE);
            }
          }
        }
      }
      x += GLASS2_ZH_BODY_W;
    }
    cursor = next;
  }
}

static void makeTwoLineHeadline(const char* raw, String& line1, String& line2) {
  String text(raw);
  text.replace(" ", "");

  const int maxWidth = 124;

  size_t split1 = fitUtf8RangeMonoBody(text, 0, maxWidth);
  if (split1 == 0 && text.length() > 0) split1 = nextUtf8Boundary(text, 0);
  line1 = text.substring(0, split1);

  if (split1 >= text.length()) {
    line2 = "";
    return;
  }

  size_t split2 = fitUtf8RangeMonoBody(text, split1, maxWidth);
  if (split2 >= text.length()) {
    line2 = text.substring(split1);
    return;
  }

  const String ellipsis = "...";
  const int ellipsisWidth = measureMonoBodyText(ellipsis);
  size_t cursor = split1;
  size_t lastFit = split1;
  int width = 0;
  while (cursor < text.length()) {
    uint32_t cp = 0;
    size_t next = cursor;
    if (!decodeUtf8At(text, cursor, cp, next)) break;
    const int w = monoBodyCharWidth(cp);
    if (width + w + ellipsisWidth > maxWidth) break;
    width += w;
    lastFit = next;
    cursor = next;
  }
  line2 = text.substring(split1, lastFit) + ellipsis;
}

// Forward declaration: commitInfoSync() redraws the current Glass2 frame
// before drawGlassFrame() is defined later in this translation unit.
static void drawGlassFrame();

static int hexNibble(char c) {
  if (c >= '0' && c <= '9') return c - '0';
  if (c >= 'a' && c <= 'f') return 10 + (c - 'a');
  if (c >= 'A' && c <= 'F') return 10 + (c - 'A');
  return -1;
}

static bool decodeFrameHex(const char* hex, uint8_t* out, size_t outLen) {
  if (!hex || strlen(hex) != outLen * 2) return false;
  for (size_t i = 0; i < outLen; ++i) {
    const int hi = hexNibble(hex[i * 2]);
    const int lo = hexNibble(hex[i * 2 + 1]);
    if (hi < 0 || lo < 0) return false;
    out[i] = static_cast<uint8_t>((hi << 4) | lo);
  }
  return true;
}

static void drawCachedInfoFrame(const uint8_t* frame) {
  if (!frame) return;

  glass2.startWrite();
  glass2.fillScreen(TFT_BLACK);

  // 128x64, row-major, one bit per pixel, MSB first.
  for (int y = 0; y < 64; ++y) {
    const size_t rowBase = static_cast<size_t>(y) * 16;
    for (int xByte = 0; xByte < 16; ++xByte) {
      const uint8_t bits = frame[rowBase + xByte];
      if (!bits) continue;
      const int x0 = xByte * 8;
      for (int bit = 0; bit < 8; ++bit) {
        if (bits & (0x80 >> bit)) {
          glass2.drawPixel(x0 + bit, y, TFT_WHITE);
        }
      }
    }
  }

  glass2.endWrite();
}

static void beginInfoSync(size_t expected, const char* revision) {
  if (expected == 0 || expected > INFO_MAX_ITEMS) {
    Serial.printf("[INFO] rejected begin count=%u\n",
                  static_cast<unsigned>(expected));
    pendingInfoExpected = 0;
    return;
  }

  pendingInfoExpected = expected;
  pendingInfoRevision = revision ? revision : "";
  for (size_t i = 0; i < INFO_MAX_ITEMS; ++i) {
    pendingInfoReceived[i] = false;
    pendingInfoItems[i] = InfoItem{};
    memset(pendingInfoFrames[i], 0, INFO_FRAME_BYTES);
  }

  Serial.printf("[INFO] sync begin revision=%s count=%u\n",
                pendingInfoRevision.c_str(),
                static_cast<unsigned>(pendingInfoExpected));
}

static bool stageInfoItem(JsonDocument& doc) {
  if (pendingInfoExpected == 0) return false;

  const size_t index = doc["index"] | INFO_MAX_ITEMS;
  if (index >= pendingInfoExpected || index >= INFO_MAX_ITEMS) return false;

  const char* id = doc["id"] | "";
  const char* category = doc["category"] | "";
  const char* headline = doc["headline"] | "";
  const char* frameHex = doc["frame_hex"] | "";

  if (!id[0] || !category[0] || !headline[0]) return false;
  if (!decodeFrameHex(frameHex, pendingInfoFrames[index], INFO_FRAME_BYTES)) {
    Serial.printf("[INFO] invalid frame index=%u\n",
                  static_cast<unsigned>(index));
    return false;
  }

  pendingInfoItems[index].id = id;
  pendingInfoItems[index].category = category;
  pendingInfoItems[index].headline = headline;
  pendingInfoItems[index].hasFrame = true;
  pendingInfoReceived[index] = true;

  Serial.printf("[INFO] staged %u/%u id=%s\n",
                static_cast<unsigned>(index + 1),
                static_cast<unsigned>(pendingInfoExpected),
                id);
  return true;
}

static bool commitInfoSync() {
  if (pendingInfoExpected == 0) return false;

  for (size_t i = 0; i < pendingInfoExpected; ++i) {
    if (!pendingInfoReceived[i]) {
      Serial.printf("[INFO] sync incomplete missing=%u\n",
                    static_cast<unsigned>(i));
      pendingInfoExpected = 0;
      return false;
    }
  }

  const String oldCurrentId =
      (currentItem < infoItemCount) ? infoItems[currentItem].id : "";

  infoItemCount = pendingInfoExpected;
  for (size_t i = 0; i < infoItemCount; ++i) {
    infoItems[i] = pendingInfoItems[i];
    memcpy(infoFrames[i], pendingInfoFrames[i], INFO_FRAME_BYTES);
  }

  currentItem = 0;
  if (oldCurrentId.length()) {
    for (size_t i = 0; i < infoItemCount; ++i) {
      if (infoItems[i].id == oldCurrentId) {
        currentItem = i;
        break;
      }
    }
  }

  Serial.printf("[INFO] sync committed revision=%s count=%u current=%u\n",
                pendingInfoRevision.c_str(),
                static_cast<unsigned>(infoItemCount),
                static_cast<unsigned>(currentItem));

  pendingInfoExpected = 0;
  itemShownSinceMs = millis();
  if (!infoPaused) drawGlassFrame();
  return true;
}

static void drawGlassFrame() {
  if (displaysSleeping || !glass2Ready || glassUiFrozen || infoItemCount == 0) return;
  if (currentItem >= infoItemCount) currentItem = 0;

  const InfoItem& item = infoItems[currentItem];

  if (item.hasFrame) {
    drawCachedInfoFrame(infoFrames[currentItem]);
    return;
  }


  // Local fallback before the first successful Mini sync.
  // Production fallback layout: no category/header, three body lines,
  // bottom-left item index. The Gateway-rendered frame remains authoritative.
  glass2.fillScreen(TFT_BLACK);
  glass2.setTextColor(TFT_WHITE, TFT_BLACK);

  String line1, line2;
  makeTwoLineHeadline(item.headline.c_str(), line1, line2);
  if (line1.length()) drawMonoBodyText(2, 3, line1);
  if (line2.length()) drawMonoBodyText(2, 18, line2);

  glass2.drawFastHLine(0, 47, 128, TFT_WHITE);

  glass2.setFont(&fonts::Font0);
  glass2.setTextDatum(top_left);
  char indexBuf[12];
  snprintf(indexBuf, sizeof(indexBuf), "%02u/%02u",
           static_cast<unsigned>(currentItem + 1),
           static_cast<unsigned>(infoItemCount));
  glass2.drawString(indexBuf, 2, 54);

  if (infoPaused) {
    const char* holdText = "HOLD";
    const int holdW = glass2.textWidth(holdText);
    glass2.drawString(holdText, 126 - holdW, 54);
  }

}

static bool initGlass2AfterPowerOn() {
  glass2Ready = glass2.init(
      GLASS2_SDA_PIN,
      GLASS2_SCL_PIN,
      GLASS2_I2C_FREQ,
      GLASS2_I2C_PORT,
      GLASS2_I2C_ADDR);

  if (glass2Ready) {
    glass2.setRotation(1);
    glass2.setBrightness(GLASS2_ACTIVE_BRIGHTNESS);
    itemShownSinceMs = millis();
    drawGlassFrame();
    Serial.println("[GLASS-DIAG] Glass2 reinitialized");
    return true;
  }

  Serial.println("[GLASS-DIAG] Glass2 reinit FAILED");
  return false;
}

static bool nightConversationBusy() {
  if (companionState != CompanionState::Idle) return true;
#if COMPANION_AUDIO_ENABLE
  if (micStreaming || ttsSequenceActive || ttsReceiveSlot >= 0) return true;
#endif
  return false;
}

static void enterNightScreenSleep() {
  if (displaysSleeping) return;
  if (!nightScreenWindowActive) return;
  if (nightConversationBusy()) return;

  // Keep Glass2 powered so no shared-rail power cycling is needed. OLED pixels
  // are black and brightness is zero, which removes burn-in load while keeping
  // the controller/Gateway path alive.
  if (glass2Ready && !glassUiFrozen && !glassPowerCutForVoice) {
    glass2.fillScreen(TFT_BLACK);
    glass2.setBrightness(0);
  }

  M5.Display.setBrightness(0);
  displaysSleeping = true;
  Serial.println("[SCREEN] night sleep ON");

  if (displayPolicyAckPending && displayPolicyTargetSleep) {
    sendDisplayPolicyAck("applied");
    displayPolicyAckPending = false;
  }
}

static void wakeDisplays(bool manualWake) {
  if (manualWake && nightScreenWindowActive) {
    manualWakeUntilMs = millis() + NIGHT_MANUAL_WAKE_MS;
  }

  if (!displaysSleeping) return;

  displaysSleeping = false;
  M5.Display.setBrightness(STICKS3_ACTIVE_BRIGHTNESS);
  drawMascotFace(companionState, idleBlink);

  if (glass2Ready && !glassUiFrozen && !glassPowerCutForVoice) {
    glass2.setBrightness(GLASS2_ACTIVE_BRIGHTNESS);
    drawGlassFrame();
  }

  itemShownSinceMs = millis();
  Serial.printf(
      "[SCREEN] wake manual=%u nightWindow=%u\n",
      manualWake ? 1u : 0u,
      nightScreenWindowActive ? 1u : 0u);

  if (displayPolicyAckPending && !displayPolicyTargetSleep) {
    sendDisplayPolicyAck("applied");
    displayPolicyAckPending = false;
  }
}

static void wakeDisplaysForActivity() {
  if (nightScreenWindowActive) {
    // Any button activity extends the temporary wake period.
    manualWakeUntilMs = millis() + NIGHT_MANUAL_WAKE_MS;
  }
  wakeDisplays(true);
}

static void setNightScreenWindow(bool active) {
  nightScreenWindowActive = active;

  if (!active) {
    manualWakeUntilMs = 0;
    wakeDisplays(false);
    Serial.println("[SCREEN] night window OFF");

    if (displayPolicyAckPending && !displayPolicyTargetSleep &&
        !displaysSleeping) {
      sendDisplayPolicyAck("applied");
      displayPolicyAckPending = false;
    }
    return;
  }

  manualWakeUntilMs = 0;
  Serial.println("[SCREEN] night window ON");
  enterNightScreenSleep();
}

static void updateNightScreenSaver() {
  if (!nightScreenWindowActive) {
    if (displaysSleeping) wakeDisplays(false);
    return;
  }

  if (displaysSleeping) return;
  if (nightConversationBusy()) return;

  const uint32_t now = millis();
  const bool manualWakeExpired =
      manualWakeUntilMs == 0 ||
      static_cast<int32_t>(now - manualWakeUntilMs) >= 0;

  if (manualWakeExpired) {
    enterNightScreenSleep();
  }
}

static void beginGlassVoiceIsolation() {
  glassPowerCutForVoice = false;

  if (glassDiagMode == GlassAudioDiagMode::Normal) {
    glassUiFrozen = false;
    Serial.println("[GLASS-DIAG] voice mode=NORMAL");
    return;
  }

  // Freeze BEFORE Mic/ES8311 capture starts so there are no Glass2 I2C writes
  // in either isolation mode.
  glassUiFrozen = true;

  if (glassDiagMode == GlassAudioDiagMode::UiFrozen) {
    Serial.println("[GLASS] freeze");
    return;
  }

  // PowerOff: remove Glass2 load from the shared EXT 5V rail for the entire
  // capture -> loopback/TTS -> playback path.
  glass2Ready = false;
  diagCheckpoint(CP_GLASS_POWER_OFF_PRE, true);
  M5.Power.setExtOutput(false);
  glassPowerCutForVoice = true;
  delay(50);
  diagCheckpoint(CP_GLASS_POWER_OFF_OK, true);
  Serial.println("[GLASS] power off");
}

static void endGlassVoiceIsolation() {
  if (glassPowerCutForVoice) {
    diagCheckpoint(CP_GLASS_POWER_ON_PRE, true);
    M5.Power.setExtOutput(true);
    delay(150);
    diagCheckpoint(CP_GLASS_POWER_ON_OK, true);
    glassPowerCutForVoice = false;
    glassUiFrozen = false;
    initGlass2AfterPowerOn();
  } else {
    glassUiFrozen = false;
    drawGlassFrame();
  }
  itemShownSinceMs = millis();
  Serial.println("[GLASS-DIAG] voice isolation ended");
}

static void cycleGlassDiagMode() {
  const uint8_t next = (static_cast<uint8_t>(glassDiagMode) + 1) % 3;
  glassDiagMode = static_cast<GlassAudioDiagMode>(next);
  Serial.printf("[GLASS-DIAG] selected=%s\n", glassDiagLabel());
  drawMascotFace(companionState, idleBlink);
}

static void resetInfoHold() {
  itemShownSinceMs = millis();
  drawGlassFrame();
}

static void moveToItem(int delta) {
  if (infoItemCount == 0) return;
  int next = static_cast<int>(currentItem) + delta;
  while (next < 0) next += static_cast<int>(infoItemCount);
  while (next >= static_cast<int>(infoItemCount)) next -= static_cast<int>(infoItemCount);
  currentItem = static_cast<size_t>(next);
  resetInfoHold();
}

static void updateInfoCycle() {
  if (displaysSleeping || !glass2Ready || infoPaused) return;
  const uint32_t now = millis();
  if (now - itemShownSinceMs >= INFO_HOLD_MS) {
    moveToItem(+1);
  }
}

#if COMPANION_GATEWAY_ENABLE
#if COMPANION_AUDIO_ENABLE && HOMEAI_WAKEWORD_ENABLE
static void pauseWakeRecognizerForTurn();
#endif

static size_t wrappedItemIndex(int offset) {
  if (infoItemCount == 0) return 0;
  int idx = static_cast<int>(currentItem) + offset;
  while (idx < 0) idx += static_cast<int>(infoItemCount);
  while (idx >= static_cast<int>(infoItemCount)) idx -= static_cast<int>(infoItemCount);
  return static_cast<size_t>(idx);
}

static void addItemContext(JsonObject obj, const InfoItem& item) {
  obj["item_id"] = item.id.c_str();
  obj["category"] = item.category.c_str();
  obj["headline"] = item.headline.c_str();
}

static void sendJsonEvent(const char* type, size_t audioBytes = 0, const char* trigger = nullptr, const char* reason = nullptr) {
  if (!gatewayConnected) return;

  JsonDocument doc;
  doc["type"] = type;
  doc["protocol"] = 2;
  doc["diag_glass_mode"] = glassDiagLabel();
  if (trigger && trigger[0] != '\0') {
    doc["trigger"] = trigger;
  }
  if (reason && reason[0] != '\0') {
    doc["reason"] = reason;
  }

  JsonObject context = doc["context"].to<JsonObject>();
  if (infoItemCount > 0) {
    if (currentItem >= infoItemCount) currentItem = 0;
    addItemContext(context["current"].to<JsonObject>(), infoItems[currentItem]);
    addItemContext(context["previous"].to<JsonObject>(), infoItems[wrappedItemIndex(-1)]);
    addItemContext(context["next"].to<JsonObject>(), infoItems[wrappedItemIndex(+1)]);
  }

  if (audioBytes) {
    JsonObject audio = doc["audio"].to<JsonObject>();
    audio["format"] = "pcm_s16le";
    audio["sample_rate"] = AUDIO_MIC_SAMPLE_RATE;
    audio["channels"] = 1;
    audio["bytes"] = audioBytes;
  }

  String payload;
  serializeJson(doc, payload);
  webSocket.sendTXT(payload);
}

static void sendDisplayPolicyAck(const char* status) {
  if (!gatewayConnected) return;
  if (displayPolicyCommandId.isEmpty()) return;

  JsonDocument doc;
  doc["type"] = "display.ack";
  doc["protocol"] = 2;
  doc["command_id"] = displayPolicyCommandId;
  doc["requested"] = displayPolicyTargetSleep ? "sleep" : "wake";
  doc["status"] = status;
  doc["window_active"] = nightScreenWindowActive;
  doc["sleeping"] = displaysSleeping;
  doc["busy"] = nightConversationBusy();

  const bool manualWakeActive =
      nightScreenWindowActive &&
      !displaysSleeping &&
      manualWakeUntilMs != 0 &&
      static_cast<int32_t>(millis() - manualWakeUntilMs) < 0;
  doc["manual_wake_active"] = manualWakeActive;

  String payload;
  serializeJson(doc, payload);
  webSocket.sendTXT(payload);

  Serial.printf(
      "[SCREEN-ACK] requested=%s status=%s sleeping=%u busy=%u id=%s\n",
      displayPolicyTargetSleep ? "sleep" : "wake",
      status,
      displaysSleeping ? 1u : 0u,
      nightConversationBusy() ? 1u : 0u,
      displayPolicyCommandId.c_str());
}

static CompanionState parseRemoteState(const char* value) {
  if (!strcmp(value, "listening")) return CompanionState::Listening;
  if (!strcmp(value, "thinking"))  return CompanionState::Thinking;
  if (!strcmp(value, "speaking"))  return CompanionState::Speaking;
  if (!strcmp(value, "success"))   return CompanionState::Success;
  if (!strcmp(value, "error"))     return CompanionState::Error;
  return CompanionState::Idle;
}

#if COMPANION_AUDIO_ENABLE
static bool configureStickS3MicInput() {
  // M5Unified exposes a supported digital mic magnification setting.
  auto micCfg = M5.Mic.config();
  micCfg.sample_rate = AUDIO_MIC_SAMPLE_RATE;
  micCfg.magnification = AUDIO_MIC_DIGITAL_MAG;
  micCfg.over_sampling = 2;
  micCfg.noise_filter_level = AUDIO_MIC_NOISE_FILTER_LEVEL;
  M5.Mic.config(micCfg);

  // StickS3 uses ES8311 at 0x18 on the internal I2C bus.
  // ES8311 REG14: bit4 selects analog MIC input; bits3:0 are PGA gain
  // in 3 dB steps (0..30 dB). M5Unified initializes this register to
  // 0x10, i.e. analog MIC selected with minimum PGA gain.
  int gainDb = AUDIO_MIC_PGA_GAIN_DB;
  if (gainDb < 0) gainDb = 0;
  if (gainDb > 30) gainDb = 30;
  const uint8_t gainSteps = static_cast<uint8_t>((gainDb + 1) / 3);
  const uint8_t reg14 = static_cast<uint8_t>(0x10 | (gainSteps > 10 ? 10 : gainSteps));

  const bool ok = M5.In_I2C.writeRegister(0x18, 0x14, &reg14, 1, 100000);
  Serial.printf("[AUDIO] mic PGA request=%d dB reg14=0x%02X write=%s\n",
                gainDb, reg14, ok ? "OK" : "FAIL");
  return ok;
}

static void freeTtsSlot(int8_t slotIndex) {
  if (slotIndex < 0 || slotIndex >= 2) return;
  TtsSlot& slot = ttsSlots[slotIndex];
  if (slot.data) {
    free(slot.data);
    slot.data = nullptr;
  }
  slot.expectedBytes = 0;
  slot.receivedBytes = 0;
  slot.sampleRate = 16000;
  slot.segmentIndex = 0;
  slot.segmentTotal = 0;
  slot.receiving = false;
}

static void resetTtsPipeline(bool stopSpeaker) {
  if (stopSpeaker && M5.Speaker.isRunning()) {
    M5.Speaker.stop(kTtsSpeakerChannel);
    M5.Speaker.end();
  }
  freeTtsSlot(0);
  freeTtsSlot(1);
  ttsReceiveSlot = -1;
  ttsCurrentSlot = -1;
  ttsNextSlot = -1;
  ttsLastQueueDepth = 0;
  ttsSequenceActive = false;
}

static int8_t findFreeTtsSlot() {
  for (int8_t i = 0; i < 2; ++i) {
    if (i == ttsReceiveSlot || i == ttsCurrentSlot || i == ttsNextSlot) continue;
    if (!ttsSlots[i].data && !ttsSlots[i].receiving) return i;
  }
  return -1;
}

static bool ensureSpeakerForTts() {
#if HOMEAI_WAKEWORD_ENABLE
  if (wakeEngineReady && !wakeRecognizerPaused) {
    ESP_SR_M5.pause();
    wakeRecognizerPaused = true;
    wakeListening = false;
  }
#endif
  if (M5.Mic.isRunning()) M5.Mic.end();

  if (!M5.Speaker.isRunning()) {
    diagCheckpoint(CP_SPK_BEGIN_PRE, true);
    auto spkCfg = M5.Speaker.config();
    spkCfg.magnification = AUDIO_SPEAKER_MAGNIFICATION;
    M5.Speaker.config(spkCfg);
    if (!M5.Speaker.begin()) {
      Serial.println("[AUDIO] M5.Speaker.begin failed");
      return false;
    }
    diagCheckpoint(CP_SPK_BEGIN_OK, true);
    M5.Speaker.setVolume(AUDIO_SPEAKER_VOLUME);
    M5.Speaker.setAllChannelVolume(255);

    const auto spkCfgNow = M5.Speaker.config();
    Serial.printf(
        "[AUDIO] speaker cfg rate=%u stereo=%u mag=%u master=%u ch0=%u\n",
        static_cast<unsigned>(spkCfgNow.sample_rate),
        static_cast<unsigned>(spkCfgNow.stereo),
        static_cast<unsigned>(spkCfgNow.magnification),
        static_cast<unsigned>(M5.Speaker.getVolume()),
        static_cast<unsigned>(M5.Speaker.getChannelVolume(kTtsSpeakerChannel)));
  }
  return true;
}

static bool beginTtsReceive(
    size_t expectedBytes,
    uint32_t sampleRate,
    uint16_t segmentIndex,
    uint16_t segmentTotal) {
  diagCheckpoint(CP_TTS_RX_START, true);

  if (expectedBytes == 0 || expectedBytes > AUDIO_TTS_MAX_BYTES) {
    Serial.printf("[AUDIO] invalid TTS size: %u\n",
                  static_cast<unsigned>(expectedBytes));
    return false;
  }
  if (ttsReceiveSlot >= 0) {
    Serial.println("[AUDIO] TTS receive already active");
    return false;
  }

  const int8_t slotIndex = findFreeTtsSlot();
  if (slotIndex < 0) {
    Serial.printf(
        "[AUDIO] no free TTS slot queueDepth=%u current=%d next=%d\n",
        static_cast<unsigned>(M5.Speaker.isPlaying(kTtsSpeakerChannel)),
        static_cast<int>(ttsCurrentSlot),
        static_cast<int>(ttsNextSlot));
    return false;
  }

  TtsSlot& slot = ttsSlots[slotIndex];
  slot.data = static_cast<uint8_t*>(
      heap_caps_malloc(expectedBytes, MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT));
  if (!slot.data) {
    slot.data = static_cast<uint8_t*>(malloc(expectedBytes));
  }
  if (!slot.data) {
    Serial.println("[AUDIO] TTS allocation failed");
    freeTtsSlot(slotIndex);
    return false;
  }

  slot.expectedBytes = expectedBytes;
  slot.receivedBytes = 0;
  slot.sampleRate = sampleRate ? sampleRate : 16000;
  slot.segmentIndex = segmentIndex ? segmentIndex : 1;
  slot.segmentTotal = segmentTotal ? segmentTotal : 1;
  slot.receiving = true;
  ttsReceiveSlot = slotIndex;

  Serial.printf(
      "[AUDIO] tts.start slot=%d seg=%u/%u bytes=%u rate=%u freePSRAM=%u\n",
      static_cast<int>(slotIndex),
      static_cast<unsigned>(slot.segmentIndex),
      static_cast<unsigned>(slot.segmentTotal),
      static_cast<unsigned>(expectedBytes),
      static_cast<unsigned>(slot.sampleRate),
      static_cast<unsigned>(heap_caps_get_free_size(MALLOC_CAP_SPIRAM)));
  return true;
}

static void appendTtsBytes(const uint8_t* data, size_t length) {
  if (ttsReceiveSlot < 0 || !length) return;
  TtsSlot& slot = ttsSlots[ttsReceiveSlot];
  if (!slot.receiving || !slot.data) return;

  const size_t room = slot.expectedBytes - slot.receivedBytes;
  const size_t copyLen = length < room ? length : room;
  memcpy(slot.data + slot.receivedBytes, data, copyLen);
  slot.receivedBytes += copyLen;
}

static bool queueReceivedTtsSegment() {
  if (ttsReceiveSlot < 0) {
    Serial.println("[AUDIO] tts.end without active receive");
    return false;
  }

  const int8_t slotIndex = ttsReceiveSlot;
  TtsSlot& slot = ttsSlots[slotIndex];
  slot.receiving = false;
  ttsReceiveSlot = -1;

  if (!slot.data || slot.receivedBytes < 2) {
    Serial.printf("[AUDIO] empty TTS segment slot=%d\n", static_cast<int>(slotIndex));
    freeTtsSlot(slotIndex);
    return false;
  }

  if (slot.receivedBytes != slot.expectedBytes) {
    Serial.printf(
        "[AUDIO] TTS bytes mismatch seg=%u/%u got=%u expected=%u\n",
        static_cast<unsigned>(slot.segmentIndex),
        static_cast<unsigned>(slot.segmentTotal),
        static_cast<unsigned>(slot.receivedBytes),
        static_cast<unsigned>(slot.expectedBytes));
  }

  if (!ensureSpeakerForTts()) {
    freeTtsSlot(slotIndex);
    return false;
  }

  const size_t queueDepth = M5.Speaker.isPlaying(kTtsSpeakerChannel);
  if (queueDepth >= 2 || ttsNextSlot >= 0) {
    Serial.printf(
        "[AUDIO] speaker queue full seg=%u/%u depth=%u\n",
        static_cast<unsigned>(slot.segmentIndex),
        static_cast<unsigned>(slot.segmentTotal),
        static_cast<unsigned>(queueDepth));
    freeTtsSlot(slotIndex);
    return false;
  }

  const size_t samples = slot.receivedBytes / sizeof(int16_t);

  // stop_current_sound=false is deliberate. M5Unified channel 0 then places
  // the request into its current/next queue instead of interrupting speech.
  const bool ok = M5.Speaker.playRaw(
      reinterpret_cast<const int16_t*>(slot.data),
      samples,
      slot.sampleRate,
      false,
      1,
      kTtsSpeakerChannel,
      false);

  if (!ok) {
    Serial.printf("[AUDIO] playRaw queue failed seg=%u/%u\n",
                  static_cast<unsigned>(slot.segmentIndex),
                  static_cast<unsigned>(slot.segmentTotal));
    freeTtsSlot(slotIndex);
    return false;
  }

  if (ttsCurrentSlot < 0) {
    ttsCurrentSlot = slotIndex;
  } else {
    ttsNextSlot = slotIndex;
  }

  ttsSequenceActive = true;
  ttsLastQueueDepth = M5.Speaker.isPlaying(kTtsSpeakerChannel);
  if (ttsLastQueueDepth == 0) ttsLastQueueDepth = 1;

  diagCheckpoint(CP_PLAYRAW_OK, true);
  setState(CompanionState::Speaking);

  Serial.printf(
      "[AUDIO] TTS queued slot=%d seg=%u/%u samples=%u depth=%u current=%d next=%d\n",
      static_cast<int>(slotIndex),
      static_cast<unsigned>(slot.segmentIndex),
      static_cast<unsigned>(slot.segmentTotal),
      static_cast<unsigned>(samples),
      static_cast<unsigned>(M5.Speaker.isPlaying(kTtsSpeakerChannel)),
      static_cast<int>(ttsCurrentSlot),
      static_cast<int>(ttsNextSlot));
  return true;
}

static void finishTtsSequenceSuccess() {
  if (M5.Speaker.isRunning()) M5.Speaker.end();
  freeTtsSlot(0);
  freeTtsSlot(1);
  ttsReceiveSlot = -1;
  ttsCurrentSlot = -1;
  ttsNextSlot = -1;
  ttsLastQueueDepth = 0;
  ttsSequenceActive = false;

  Serial.println("[AUDIO] gapless playback done");
  diagCheckpoint(CP_PLAYBACK_DONE, true);
  sendJsonEvent("playback.done");
}

static void failTtsSequence(const char* reason) {
  Serial.printf("[AUDIO] TTS pipeline error: %s\n", reason);
  resetTtsPipeline(true);
  sendJsonEvent("playback.error");
  setState(CompanionState::Error);
}

static void updateTtsPlayback() {
  if (!ttsSequenceActive) return;

  const size_t depth = M5.Speaker.isPlaying(kTtsSpeakerChannel);

  // 2 -> 1 means the old current buffer is no longer used by M5Unified and
  // the preloaded next buffer has become current. Recycle the old PSRAM slot
  // and immediately tell Mini there is room for another segment.
  if (ttsLastQueueDepth >= 2 && depth == 1) {
    const int8_t finishedSlot = ttsCurrentSlot;
    ttsCurrentSlot = ttsNextSlot;
    ttsNextSlot = -1;
    freeTtsSlot(finishedSlot);

    Serial.printf(
        "[AUDIO] seamless handoff current=%d free=%d seg=%u/%u\n",
        static_cast<int>(ttsCurrentSlot),
        static_cast<int>(finishedSlot),
        ttsCurrentSlot >= 0
            ? static_cast<unsigned>(ttsSlots[ttsCurrentSlot].segmentIndex)
            : 0u,
        ttsCurrentSlot >= 0
            ? static_cast<unsigned>(ttsSlots[ttsCurrentSlot].segmentTotal)
            : 0u);

    if (ttsCurrentSlot >= 0 &&
        ttsSlots[ttsCurrentSlot].segmentIndex <
            ttsSlots[ttsCurrentSlot].segmentTotal) {
      sendJsonEvent("playback.slot_ready");
    }
  }

  // 1 -> 0 means the current item completed without a queued successor.
  if (ttsLastQueueDepth >= 1 && depth == 0) {
    if (ttsCurrentSlot >= 0) {
      const bool wasFinal =
          ttsSlots[ttsCurrentSlot].segmentIndex >=
          ttsSlots[ttsCurrentSlot].segmentTotal;
      freeTtsSlot(ttsCurrentSlot);
      ttsCurrentSlot = -1;

      if (wasFinal) {
        finishTtsSequenceSuccess();
        return;
      }

      // Network was slower than playback. Keep the speaker task available and
      // ask Mini for the next segment; playback can resume, although this is an
      // underrun and may produce a pause.
      Serial.println("[AUDIO] TTS queue underrun; requesting next segment");
      sendJsonEvent("playback.slot_ready");
    }
  }

  ttsLastQueueDepth = M5.Speaker.isPlaying(kTtsSpeakerChannel);
}
#endif

static void onWebSocketEvent(WStype_t type, uint8_t* payload, size_t length) {
  switch (type) {
    case WStype_CONNECTED:
      gatewayConnected = true;
      Serial.println("[WS] connected");
      sendJsonEvent("device.hello");
      drawGlassFrame();
      break;

    case WStype_DISCONNECTED:
      gatewayConnected = false;
      Serial.println("[WS] disconnected");
#if COMPANION_AUDIO_ENABLE && HOMEAI_WAKEWORD_ENABLE
      pauseWakeRecognizerForTurn();
#endif
#if COMPANION_AUDIO_ENABLE
      if (micStreaming) {
        if (M5.Mic.isRunning()) M5.Mic.end();
        micStreaming = false;
      }
      if (ttsSequenceActive || ttsReceiveSlot >= 0) {
        resetTtsPipeline(true);
      }
#endif
      setState(CompanionState::Error);
      if (glassUiFrozen || glassPowerCutForVoice) {
        endGlassVoiceIsolation();
      } else {
        drawGlassFrame();
      }
      break;

    case WStype_TEXT: {
      JsonDocument doc;
      DeserializationError err = deserializeJson(doc, payload, length);
      if (err) break;
      const char* msgType = doc["type"] | "";

      if (!strcmp(msgType, "info.begin")) {
        const size_t count = doc["count"] | 0;
        const char* revision = doc["revision"] | "";
        beginInfoSync(count, revision);
      }
      else if (!strcmp(msgType, "info.item")) {
        if (!stageInfoItem(doc)) {
          Serial.println("[INFO] item rejected");
        }
      }
      else if (!strcmp(msgType, "info.end")) {
        if (commitInfoSync()) {
          sendJsonEvent("info.ack");
        }
      }
      else if (!strcmp(msgType, "display.sleep")) {
        const char* commandId = doc["command_id"] | "";
        displayPolicyCommandId = commandId;
        displayPolicyTargetSleep = true;
        displayPolicyAckPending = true;

        setNightScreenWindow(true);

        if (displaysSleeping) {
          sendDisplayPolicyAck("applied");
          displayPolicyAckPending = false;
        } else {
          // The command was received but cannot yet be called "executed".
          // Typical reason: voice/TTS path is still busy.
          sendDisplayPolicyAck("pending");
        }
      }
      else if (!strcmp(msgType, "display.wake")) {
        const char* commandId = doc["command_id"] | "";
        displayPolicyCommandId = commandId;
        displayPolicyTargetSleep = false;
        displayPolicyAckPending = true;

        setNightScreenWindow(false);

        if (!displaysSleeping) {
          sendDisplayPolicyAck("applied");
          displayPolicyAckPending = false;
        } else {
          sendDisplayPolicyAck("pending");
        }
      }
      else if (!strcmp(msgType, "assistant.state")) {
        const char* state = doc["state"] | "idle";
        setState(parseRemoteState(state));
        infoPaused = companionState != CompanionState::Idle;

        if (companionState == CompanionState::Idle) {
          itemShownSinceMs = millis();
          if (glassUiFrozen || glassPowerCutForVoice) {
            endGlassVoiceIsolation();
          } else {
            drawGlassFrame();
          }
        } else {
          drawGlassFrame();
        }
      }
#if COMPANION_AUDIO_ENABLE
      else if (!strcmp(msgType, "tts.start")) {
        const size_t bytes = doc["bytes"] | 0;
        const uint32_t sampleRate = doc["sample_rate"] | 16000;
        const uint16_t segmentIndex = doc["segment_index"] | 1;
        const uint16_t segmentTotal = doc["segment_total"] | 1;
        if (!beginTtsReceive(bytes, sampleRate, segmentIndex, segmentTotal)) {
          failTtsSequence("tts.start rejected");
        }
      } else if (!strcmp(msgType, "tts.end")) {
        if (!queueReceivedTtsSegment()) {
          failTtsSequence("tts.end queue failed");
        }
      }
#endif
      else if (!strcmp(msgType, "asr.result")) {
        const char* text = doc["text"] | "";
        Serial.printf("[ASR] %s\n", text);
      }
      else if (!strcmp(msgType, "assistant.text")) {
        const char* text = doc["text"] | "";
        Serial.printf("[AGENT] %s\n", text);
      }
      else if (!strcmp(msgType, "gateway.ready")) {
        const char* mode = doc["mode"] | "";
        Serial.printf("[GATEWAY] ready mode=%s\n", mode);
      }
      else if (!strcmp(msgType, "assistant.error")) {
        const char* message = doc["message"] | "gateway error";
        Serial.printf("[GATEWAY] %s\n", message);
        setState(CompanionState::Error);
      }
      break;
    }

#if COMPANION_AUDIO_ENABLE
    case WStype_BIN:
      appendTtsBytes(payload, length);
      break;
#endif

    default:
      break;
  }
}

static void ensureWebSocketStarted() {
  if (webSocketStarted) return;

  webSocket.begin(deviceConfig.gatewayHost.c_str(), deviceConfig.gatewayPort, deviceConfig.gatewayPath.c_str());
  webSocket.onEvent(onWebSocketEvent);
  webSocket.setReconnectInterval(5000);
  webSocketStarted = true;

  Serial.printf("[WS] client initialized -> ws://%s:%u%s\n",
                deviceConfig.gatewayHost.c_str(),
                static_cast<unsigned>(deviceConfig.gatewayPort),
                deviceConfig.gatewayPath.c_str());
}

static void scheduleWifiRetry() {
  const size_t idx =
      wifiBackoffIndex < WIFI_RETRY_BACKOFF_COUNT
          ? wifiBackoffIndex
          : (WIFI_RETRY_BACKOFF_COUNT - 1);

  const uint32_t waitMs = WIFI_RETRY_BACKOFF_MS[idx];
  wifiNextAttemptMs = millis() + waitMs;

  if (wifiBackoffIndex + 1 < WIFI_RETRY_BACKOFF_COUNT) {
    ++wifiBackoffIndex;
  }

  Serial.printf("[WIFI] retry scheduled in %u s (next backoff step=%u/%u)\n",
                static_cast<unsigned>(waitMs / 1000),
                static_cast<unsigned>(wifiBackoffIndex + 1),
                static_cast<unsigned>(WIFI_RETRY_BACKOFF_COUNT));
}

static void startWifiAttempt() {
  if (wifiAttemptActive || WiFi.status() == WL_CONNECTED) return;

  // We deliberately disable Arduino's automatic reconnect and own the retry
  // cadence here. This prevents repeated rapid association attempts when an
  // AP asks the client to back off for a long comeback period.
  WiFi.disconnect(false, false);
  WiFi.begin(deviceConfig.wifiSsid.c_str(), deviceConfig.wifiPassword.c_str());

  // Keep the brownout detector ON and reduce the source of the spike
  // instead. ESP-IDF uses quarter-dBm units, so 40 = 10 dBm.
  const esp_err_t txPowerResult =
      esp_wifi_set_max_tx_power(static_cast<int8_t>(WIFI_MAX_TX_POWER_QDBM));
  Serial.printf("[WIFI] max TX power=%0.2f dBm set=%s (%d)\n",
                WIFI_MAX_TX_POWER_QDBM / 4.0f,
                txPowerResult == ESP_OK ? "OK" : "FAIL",
                static_cast<int>(txPowerResult));

  wifiAttemptActive = true;
  wifiAttemptStartedMs = millis();

  Serial.printf("[WIFI] connecting to %s (timeout=%u s)\n",
                deviceConfig.wifiSsid.c_str(),
                static_cast<unsigned>(WIFI_CONNECT_TIMEOUT_MS / 1000));
}

static void updateNetworkManager() {
  const wl_status_t status = WiFi.status();
  const uint32_t now = millis();

  if (status == WL_CONNECTED) {
    wifiAttemptActive = false;

    if (!wifiOnline) {
      wifiOnline = true;
      wifiEverConnected = true;
      wifiBackoffIndex = 0;
      wifiNextAttemptMs = 0;

      Serial.print("[WIFI] IP: ");
      Serial.println(WiFi.localIP());

      ensureWebSocketStarted();
    }
    return;
  }

  // A connection that had been healthy has now gone away.
  if (wifiOnline) {
    wifiOnline = false;
    gatewayConnected = false;
    wifiAttemptActive = false;

    Serial.printf("[WIFI] link lost (status=%d); entering backoff\n",
                  static_cast<int>(status));
    setState(CompanionState::Error);
    scheduleWifiRetry();
    return;
  }

  // Current attempt has been allowed a full 15 s association window.
  if (wifiAttemptActive) {
    if (now - wifiAttemptStartedMs >= WIFI_CONNECT_TIMEOUT_MS) {
      wifiAttemptActive = false;
      WiFi.disconnect(false, false);

      Serial.printf("[WIFI] connect timeout (status=%d)\n",
                    static_cast<int>(status));
      setState(CompanionState::Error);
      scheduleWifiRetry();
    }
    return;
  }

  // Wait quietly. No association hammering during the backoff interval.
  if (wifiNextAttemptMs != 0 &&
      static_cast<int32_t>(now - wifiNextAttemptMs) >= 0) {
    wifiNextAttemptMs = 0;
    startWifiAttempt();
  }
}

static void setupGateway() {
  if (!deviceConfig.provisioned) {
    Serial.println("[WIFI] skipped: persistent terminal config missing");
    return;
  }

  WiFi.persistent(false);
  WiFi.mode(WIFI_STA);
  WiFi.setAutoReconnect(false);

  wifiOnline = false;
  wifiAttemptActive = false;
  wifiEverConnected = false;
  wifiBackoffIndex = 0;
  wifiNextAttemptMs = 0;

  startWifiAttempt();
}
#endif

#if COMPANION_AUDIO_ENABLE && COMPANION_GATEWAY_ENABLE
static bool ensurePttCaptureBuffer() {
  if (pttCaptureBuffer) return true;

  pttCaptureBuffer = static_cast<uint8_t*>(
      heap_caps_malloc(AUDIO_PTT_BUFFER_BYTES, MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT));

  if (!pttCaptureBuffer) {
    Serial.printf("[AUDIO] PTT PSRAM allocation failed bytes=%u\n",
                  static_cast<unsigned>(AUDIO_PTT_BUFFER_BYTES));
    return false;
  }

  Serial.printf("[AUDIO] PTT PSRAM buffer ready bytes=%u\n",
                static_cast<unsigned>(AUDIO_PTT_BUFFER_BYTES));
  return true;
}

static bool bufferMicBlock(uint32_t sequence) {
  if (!pttCaptureBuffer) return false;

  const size_t blockBytes =
      AUDIO_MIC_BLOCK_SAMPLES * sizeof(int16_t);
  if (pttCaptureBytes + blockBytes > AUDIO_PTT_BUFFER_BYTES) {
    pttCaptureOverflow = true;
    Serial.printf("[AUDIO] PTT buffer overflow at seq=%u bytes=%u\n",
                  static_cast<unsigned>(sequence),
                  static_cast<unsigned>(pttCaptureBytes));
    return false;
  }

  const size_t idx = sequence % AUDIO_MIC_RING_BLOCKS;
  memcpy(
      pttCaptureBuffer + pttCaptureBytes,
      reinterpret_cast<uint8_t*>(micRing[idx]),
      blockBytes);
  pttCaptureBytes += blockBytes;
  return true;
}

static bool transmitPttBuffer() {
  if (!gatewayConnected || !pttCaptureBuffer || pttCaptureBytes == 0) {
    return false;
  }

  diagCheckpoint(CP_PTT_TX_BEGIN, true);
  Serial.printf(
      "[AUDIO] post-capture TX begin bytes=%u chunk=%u pace=%ums\n",
      static_cast<unsigned>(pttCaptureBytes),
      static_cast<unsigned>(AUDIO_PTT_TX_CHUNK_BYTES),
      static_cast<unsigned>(AUDIO_PTT_TX_PACE_MS));

  size_t offset = 0;
  uint32_t chunkSequence = 0;

  while (offset < pttCaptureBytes) {
    if (!gatewayConnected) {
      Serial.println("[AUDIO] post-capture TX aborted: gateway disconnected");
      return false;
    }

    const size_t remaining = pttCaptureBytes - offset;
    const size_t length =
        remaining < AUDIO_PTT_TX_CHUNK_BYTES
            ? remaining
            : AUDIO_PTT_TX_CHUNK_BYTES;

    diagCheckpoint(CP_PTT_TX_PRE, false, chunkSequence);
    const bool sent = webSocket.sendBIN(pttCaptureBuffer + offset, length);
    diagCheckpoint(CP_PTT_TX_POST, false, chunkSequence);

    if (!sent) {
      Serial.printf("[WS-AUDIO] post-capture sendBIN failed chunk=%u offset=%u\n",
                    static_cast<unsigned>(chunkSequence),
                    static_cast<unsigned>(offset));
      return false;
    }

    offset += length;
    ++chunkSequence;

    // Process socket ACK/control traffic only after the mic path is off.
    webSocket.loop();
    if (AUDIO_PTT_TX_PACE_MS) delay(AUDIO_PTT_TX_PACE_MS);
  }

  diagCheckpoint(CP_PTT_TX_DONE, true);
  Serial.printf("[AUDIO] post-capture TX done chunks=%u bytes=%u\n",
                static_cast<unsigned>(chunkSequence),
                static_cast<unsigned>(offset));
  return true;
}


#if COMPANION_AUDIO_ENABLE && HOMEAI_WAKEWORD_ENABLE
static void finishPttCapture();

static uint32_t meanAbsLevel(const int16_t* samples, size_t count) {
  if (!samples || count == 0) return 0;

  uint64_t sum = 0;
  for (size_t i = 0; i < count; ++i) {
    const int32_t value = static_cast<int32_t>(samples[i]);
    sum += static_cast<uint32_t>(value < 0 ? -value : value);
  }
  return static_cast<uint32_t>(sum / count);
}

static uint32_t currentAutoVadThreshold() {
  const uint32_t adaptive =
      wakeNoiseFloor > 0
          ? static_cast<uint32_t>((wakeNoiseFloor * 23u) / 10u)
          : AUTO_WAKE_MIN_VOICE_LEVEL;

  return adaptive > AUTO_WAKE_MIN_VOICE_LEVEL
             ? adaptive
             : AUTO_WAKE_MIN_VOICE_LEVEL;
}

static void updateWakeNoiseFloor(const int16_t* samples, size_t count) {
  const uint32_t level = meanAbsLevel(samples, count);
  if (level == 0) return;

  if (wakeNoiseFloor == 0) {
    wakeNoiseFloor = level;
    return;
  }

  // Avoid learning actual speech as the room noise floor.
  if (level <= wakeNoiseFloor * 2u) {
    wakeNoiseFloor = (wakeNoiseFloor * 31u + level) / 32u;
  }
}

static void onWakeSrEvent(sr_event_t event, int commandId, int phraseId) {
  (void)phraseId;

  if (event == SR_EVENT_COMMAND && commandId == 0) {
    wakeWordDetected = true;
    return;
  }

  if (event == SR_EVENT_TIMEOUT && wakeEngineReady) {
    ESP_SR_M5.setMode(SR_MODE_COMMAND);
  }
}

static bool initLocalWakeWordEngine() {
  Serial.println("[WAKE] ESP-SR init begin language=CN model=mn5q8_cn");
  ESP_SR_M5.onEvent(onWakeSrEvent);

  if (!ESP_SR_M5.begin(
          HOMEAI_WAKE_COMMANDS,
          HOMEAI_WAKE_COMMAND_COUNT,
          SR_MODE_COMMAND,
          SR_CHANNELS_MONO)) {
    Serial.println("[WAKE] ESP-SR Chinese MultiNet init FAILED");
    return false;
  }

  ESP_SR_M5.pause();
  wakeRecognizerPaused = true;
  wakeEngineReady = true;

  Serial.println("[WAKE] local keyword engine ready: 逐光逐光");
  return true;
}

static void pauseWakeRecognizerForTurn() {
  if (!wakeEngineReady) return;

  if (!wakeRecognizerPaused) {
    ESP_SR_M5.pause();
    wakeRecognizerPaused = true;
  }

  wakeListening = false;
}

static bool restartWakeMicPath(const char* reason, bool recovery) {
  if (!wakeEngineReady) return false;
  if (micStreaming || ttsSequenceActive || ttsReceiveSlot >= 0) return false;
  if (companionState != CompanionState::Idle) return false;
  if (M5.Speaker.isRunning()) return false;

  // Canonicalize the half-duplex hardware state instead of trusting only
  // Mic.isRunning(). A stale running flag with no accepted record blocks is
  // exactly the failure seen after an ASR-empty wake turn.
  const bool pauseOk = wakeRecognizerPaused ? true : ESP_SR_M5.pause();
  wakeRecognizerPaused = true;
  wakeListening = false;

  const uint32_t settleStarted = millis();
  while (M5.Mic.isRecording() && millis() - settleStarted < 100) {
    M5.update();
    delay(1);
  }
  if (M5.Mic.isRunning()) M5.Mic.end();
  delay(8);

  if (!M5.Mic.begin()) {
    Serial.printf(
        "[WAKE-RECOVER] mic begin FAILED reason=%s pause=%u\n",
        reason ? reason : "unknown",
        static_cast<unsigned>(pauseOk));
    return false;
  }
  configureStickS3MicInput();

  wakeAcceptedBlocks = 0;
  wakeFedBlocks = 0;
  wakeWordDetected = false;

  const bool modeOk = ESP_SR_M5.setMode(SR_MODE_COMMAND);
  const bool resumeOk = ESP_SR_M5.resume();
  if (!resumeOk) {
    Serial.printf(
        "[WAKE-RECOVER] ESP-SR resume FAILED reason=%s mode=%u\n",
        reason ? reason : "unknown",
        static_cast<unsigned>(modeOk));
    if (M5.Mic.isRunning()) M5.Mic.end();
    return false;
  }

  wakeRecognizerPaused = false;
  wakeListening = true;
  wakeLastAudioProgressMs = millis();

  if (recovery) {
    ++wakeRecoveryCount;
    wakeLastRecoveryMs = wakeLastAudioProgressMs;
    Serial.printf(
        "[WAKE-RECOVER] recovered reason=%s count=%u pause=%u mode=%u noiseFloor=%u\n",
        reason ? reason : "unknown",
        static_cast<unsigned>(wakeRecoveryCount),
        static_cast<unsigned>(pauseOk),
        static_cast<unsigned>(modeOk),
        static_cast<unsigned>(wakeNoiseFloor));
  } else {
    Serial.printf(
        "[WAKE] listening local-only noiseFloor=%u\n",
        static_cast<unsigned>(wakeNoiseFloor));
  }
  return true;
}

static bool startWakeListeningIfPossible() {
#if COMPANION_GATEWAY_ENABLE
  if (!gatewayConnected) return false;
#endif

  if (!wakeEngineReady || wakeListening) return wakeListening;
  return restartWakeMicPath("listen-start", false);
}

static void updateWakeAudioFeed() {
  if (!wakeListening || !wakeEngineReady) return;
  if (micStreaming || companionState != CompanionState::Idle) return;

  const uint32_t now = millis();
  const uint32_t sequence = wakeAcceptedBlocks;
  const size_t idx = sequence % AUDIO_MIC_RING_BLOCKS;

  if (M5.Mic.record(
          wakeRing[idx],
          AUDIO_MIC_BLOCK_SAMPLES,
          AUDIO_MIC_SAMPLE_RATE,
          false)) {
    ++wakeAcceptedBlocks;
    wakeLastAudioProgressMs = now;

    // Preserve the two-request safety gap required by the PTT path.
    if (wakeAcceptedBlocks >= 3) {
      const uint32_t safeSequence = wakeAcceptedBlocks - 3;

      if (safeSequence >= wakeFedBlocks) {
        const size_t safeIdx = safeSequence % AUDIO_MIC_RING_BLOCKS;

        updateWakeNoiseFloor(
            wakeRing[safeIdx],
            AUDIO_MIC_BLOCK_SAMPLES);

        ESP_SR_M5.feedAudio(
            wakeRing[safeIdx],
            AUDIO_MIC_BLOCK_SAMPLES);

        ++wakeFedBlocks;
        wakeLastAudioProgressMs = now;
      }
    }
    return;
  }

  // "listening" is not considered healthy unless record/feed blocks keep
  // moving. Self-heal a stalled M5 Mic/I2S queue after half-duplex turns.
  if (wakeLastAudioProgressMs != 0 &&
      now - wakeLastAudioProgressMs >= WAKE_FEED_STALL_MS &&
      now - wakeLastRecoveryMs >= WAKE_RECOVERY_COOLDOWN_MS) {
    Serial.printf(
        "[WAKE-RECOVER] feed stall ms=%u accepted=%u fed=%u micRunning=%u micRecording=%u\n",
        static_cast<unsigned>(now - wakeLastAudioProgressMs),
        static_cast<unsigned>(wakeAcceptedBlocks),
        static_cast<unsigned>(wakeFedBlocks),
        static_cast<unsigned>(M5.Mic.isRunning()),
        static_cast<unsigned>(M5.Mic.isRecording()));
    restartWakeMicPath("feed-stall", true);
  }
}

static void resetAutoWakeCaptureState() {
  autoWakeCaptureActive = false;
  autoWakeSpeechStarted = false;
  autoWakePttStartSent = false;
  autoWakeLongestSilenceMs = 0;
  autoWakeLastVadLevel = 0;
  autoWakeVadVoice = false;
  autoWakeTrimOffsetBytes = 0;
}

static void cancelAutoWakeCaptureNoSpeech() {
  if (!autoWakeCaptureActive) return;

  micStreaming = false;

  const uint32_t waitStarted = millis();
  while (M5.Mic.isRecording() && millis() - waitStarted < 250) {
    M5.update();
    delay(1);
  }

  if (M5.Mic.isRunning()) M5.Mic.end();

  sendJsonEvent("ptt.abort", 0, "wake_word", "no_speech");
  resetAutoWakeCaptureState();
  pttCaptureBytes = 0;
  pttCaptureOverflow = false;

  infoPaused = false;
  setState(CompanionState::Idle);
  itemShownSinceMs = millis();

  endGlassVoiceIsolation();
  drawGlassFrame();

  Serial.println("[WAKE] no command");
}

static void updateAutoWakeVad(
    const int16_t* samples,
    size_t sampleCount) {
  if (!autoWakeCaptureActive || !samples || sampleCount == 0) return;

  const uint32_t now = millis();
  const uint32_t level = meanAbsLevel(samples, sampleCount);
  const uint32_t threshold = currentAutoVadThreshold();
  autoWakeLastVadLevel = level;

  if (level >= threshold) {
    if (autoWakeSpeechStarted && !autoWakeVadVoice) {
      const uint32_t silenceMs = now - autoWakeLastVoiceMs;
      if (silenceMs > autoWakeLongestSilenceMs) {
        autoWakeLongestSilenceMs = silenceMs;
      }
      Serial.printf(
          "[VAD] voice-resume level=%u threshold=%u silence=%u\n",
          static_cast<unsigned>(level),
          static_cast<unsigned>(threshold),
          static_cast<unsigned>(silenceMs));
    }

    autoWakeVadVoice = true;
    autoWakeLastVoiceMs = now;

    if (!autoWakeSpeechStarted) {
      autoWakeSpeechStarted = true;

      const size_t prerollBytes =
          (static_cast<size_t>(AUDIO_MIC_SAMPLE_RATE) * 2u *
           AUTO_WAKE_PREROLL_MS) /
          1000u;

      autoWakeTrimOffsetBytes =
          pttCaptureBytes > prerollBytes
              ? pttCaptureBytes - prerollBytes
              : 0;

      Serial.printf(
          "[WAKE] command speech start level=%u threshold=%u trim=%u\n",
          static_cast<unsigned>(level),
          static_cast<unsigned>(threshold),
          static_cast<unsigned>(autoWakeTrimOffsetBytes));
    }
  } else if (autoWakeSpeechStarted) {
    if (autoWakeVadVoice) {
      Serial.printf(
          "[VAD] silence level=%u threshold=%u\n",
          static_cast<unsigned>(level),
          static_cast<unsigned>(threshold));
    }
    autoWakeVadVoice = false;
    const uint32_t silenceMs = now - autoWakeLastVoiceMs;
    if (silenceMs > autoWakeLongestSilenceMs) {
      autoWakeLongestSilenceMs = silenceMs;
    }
  }

  const uint32_t elapsed = now - autoWakeCaptureStartedMs;

  if (!autoWakeSpeechStarted) {
    if (elapsed >= AUTO_WAKE_WAIT_SPEECH_MS) {
      cancelAutoWakeCaptureNoSpeech();
    }
    return;
  }

  if (elapsed >= AUTO_WAKE_MIN_CAPTURE_MS &&
      now - autoWakeLastVoiceMs >= AUTO_WAKE_END_SILENCE_MS) {
    Serial.printf(
        "[WAKE] end-of-speech silence=%u ms\n",
        static_cast<unsigned>(now - autoWakeLastVoiceMs));

    finishPttCapture();
    return;
  }

  if (elapsed >= AUTO_WAKE_MAX_CAPTURE_MS) {
    Serial.printf(
        "[VAD] timeout level=%u threshold=%u longest=%u\n",
        static_cast<unsigned>(autoWakeLastVadLevel),
        static_cast<unsigned>(threshold),
        static_cast<unsigned>(autoWakeLongestSilenceMs));
    Serial.println("[WAKE] hands-free capture max duration reached");
    finishPttCapture();
  }
}

static void playLocalWakeAckTone() {
  // StickS3 ES8311 is half-duplex.  Stop the wake microphone first, play a
  // tiny local confirmation sound, then let startVoiceCaptureInternal()
  // restart the microphone.  No Gateway / ASR / OpenClaw / cloud TTS is used.
  const uint32_t settleStarted = millis();
  while (M5.Mic.isRecording() && millis() - settleStarted < 80) {
    M5.update();
    delay(1);
  }

  if (M5.Mic.isRunning()) M5.Mic.end();

  // The wake acknowledgement is intentionally much lower-power than normal
  // TTS.  A full-volume speaker transition immediately after Mic shutdown can
  // create a sharp rail transient on StickS3 and trigger the brownout detector.
  // Give the half-duplex audio rail a moment to settle, then use a dedicated
  // low-power speaker configuration.  Normal TTS restores its validated
  // AUDIO_SPEAKER_* settings in ensureSpeakerForTts().
  delay(24);

  if (!M5.Speaker.isRunning()) {
    auto ackCfg = M5.Speaker.config();
    ackCfg.magnification = 1;
    M5.Speaker.config(ackCfg);
    if (!M5.Speaker.begin()) {
      Serial.println("[WAKE-ACK] speaker begin failed");
      return;
    }
  }

  M5.Speaker.setVolume(80);
  M5.Speaker.setAllChannelVolume(96);
  delay(12);

  // Short rising two-note acknowledgement.  It is deliberately compact so
  // the wake-to-command gap remains small and firmware flash cost stays tiny.
  const bool tone1 = M5.Speaker.tone(1047.0f, 65, -1, true);
  delay(78);
  const bool tone2 = M5.Speaker.tone(1319.0f, 80, -1, true);
  delay(96);

  M5.Speaker.stop();
  M5.Speaker.end();

  Serial.printf(
      "[WAKE-ACK] local confirm tone played tone1=%u tone2=%u ms=%u\n",
      static_cast<unsigned>(tone1),
      static_cast<unsigned>(tone2),
      static_cast<unsigned>(millis()));
}

static bool startVoiceCaptureInternal(bool wakeInitiated) {
#if COMPANION_GATEWAY_ENABLE
  if (!gatewayConnected) return false;
#endif

  if (micStreaming || ttsSequenceActive || ttsReceiveSlot >= 0) return false;

  if (companionState == CompanionState::Thinking ||
      companionState == CompanionState::Speaking) {
    return false;
  }

  diagCheckpoint(CP_PTT_ENTER, true);
  pauseWakeRecognizerForTurn();

  beginGlassVoiceIsolation();
  diagCheckpoint(CP_PTT_ISOLATION_DONE, true);

  if (M5.Speaker.isRunning()) {
    M5.Speaker.stop();
    M5.Speaker.end();
  }

  if (!wakeInitiated) {
    if (M5.Mic.isRunning()) M5.Mic.end();
  } else {
    // Give the user an immediate local acknowledgement before opening the
    // hands-free command window.  The 3.5 s speech-start timer is armed only
    // after this function returns and the microphone is running again.
    playLocalWakeAckTone();
  }

  diagCheckpoint(CP_PTT_AUDIO_IDLE, true);

  if (!M5.Mic.isRunning()) {
    diagCheckpoint(CP_MIC_BEGIN_PRE, true);

    if (!M5.Mic.begin()) {
      Serial.println("[AUDIO] M5.Mic.begin failed");
      setState(CompanionState::Error);
      endGlassVoiceIsolation();
      return false;
    }

    diagCheckpoint(CP_MIC_BEGIN_OK, true);
    configureStickS3MicInput();
    diagCheckpoint(CP_MIC_CODEC_OK, true);
  }

  if (!ensurePttCaptureBuffer()) {
    if (M5.Mic.isRunning()) M5.Mic.end();

    setState(CompanionState::Error);
    endGlassVoiceIsolation();
    return false;
  }

  micAcceptedBlocks = 0;
  micBufferedBlocks = 0;
  pttCaptureBytes = 0;
  pttCaptureOverflow = false;
  pttStartedMs = millis();

  micStreaming = true;
  infoPaused = true;

  autoWakeCaptureActive = wakeInitiated;
  autoWakeSpeechStarted = false;
  autoWakePttStartSent = false;
  autoWakeCaptureStartedMs = millis();
  autoWakeLastVoiceMs = autoWakeCaptureStartedMs;
  autoWakeLongestSilenceMs = 0;
  autoWakeLastVadLevel = 0;
  autoWakeVadVoice = false;
  autoWakeTrimOffsetBytes = 0;

  if (wakeInitiated) {
    wakeDisplaysForActivity();
  }

  drawGlassFrame();
  setState(CompanionState::Listening);

  if (!wakeInitiated) {
    // Manual PTT retains the existing protocol behavior.
    diagCheckpoint(CP_PTT_EVENT_PRE, true);
    sendJsonEvent("ptt.start", 0, "button_a");
    diagCheckpoint(CP_PTT_RUNNING, true);
    Serial.println("[TRG] button_a");
  } else {
    // Hands-free wake keeps all network audio work out of the active mic
    // window. ptt.start will be sent only after Mic/I2S stops.
    Serial.println("[WAKE] capture start");
  }

  return true;
}

static bool startAutoCaptureFromWake() {
  wakeWordDetected = false;
  Serial.println("[TRG] wake_word");

  if (!startVoiceCaptureInternal(true)) {
    Serial.println("[WAKE] wake capture could not start");
    return false;
  }

  return true;
}

static void updateWakeWordSystem() {
  if (!wakeEngineReady) return;

  // Event callbacks only set a flag. Product-state changes happen here.
  if (wakeWordDetected &&
      wakeListening &&
      companionState == CompanionState::Idle) {
    if (!startAutoCaptureFromWake()) {
      wakeWordDetected = false;
    }
    return;
  }

  if (micStreaming ||
      ttsSequenceActive ||
      ttsReceiveSlot >= 0 ||
      companionState != CompanionState::Idle) {
    if (wakeListening) {
      pauseWakeRecognizerForTurn();
    }
    return;
  }

  if (!wakeListening) {
    startWakeListeningIfPossible();
  }

  updateWakeAudioFeed();
}
#endif  // COMPANION_AUDIO_ENABLE && HOMEAI_WAKEWORD_ENABLE

static bool startPttCapture() {
#if HOMEAI_WAKEWORD_ENABLE
  return startVoiceCaptureInternal(false);
#else
  if (!gatewayConnected || micStreaming || ttsSequenceActive || ttsReceiveSlot >= 0) return false;
  diagCheckpoint(CP_PTT_ENTER, true);
  if (companionState == CompanionState::Thinking || companionState == CompanionState::Speaking) return false;

  beginGlassVoiceIsolation();
  diagCheckpoint(CP_PTT_ISOLATION_DONE, true);

  if (M5.Speaker.isRunning()) {
    M5.Speaker.stop();
    M5.Speaker.end();
  }
  if (M5.Mic.isRunning()) M5.Mic.end();
  diagCheckpoint(CP_PTT_AUDIO_IDLE, true);

  diagCheckpoint(CP_MIC_BEGIN_PRE, true);
  if (!M5.Mic.begin()) {
    Serial.println("[AUDIO] M5.Mic.begin failed");
    setState(CompanionState::Error);
    endGlassVoiceIsolation();
    return false;
  }

  diagCheckpoint(CP_MIC_BEGIN_OK, true);
  configureStickS3MicInput();
  diagCheckpoint(CP_MIC_CODEC_OK, true);

  if (!ensurePttCaptureBuffer()) {
    if (M5.Mic.isRunning()) M5.Mic.end();
    setState(CompanionState::Error);
    endGlassVoiceIsolation();
    return false;
  }

  micAcceptedBlocks = 0;
  micBufferedBlocks = 0;
  pttCaptureBytes = 0;
  pttCaptureOverflow = false;
  pttStartedMs = millis();
  micStreaming = true;
  infoPaused = true;
  drawGlassFrame();
  setState(CompanionState::Listening);
  diagCheckpoint(CP_PTT_EVENT_PRE, true);
  sendJsonEvent("ptt.start", 0, "button_a");
  diagCheckpoint(CP_PTT_RUNNING, true);
  Serial.println("[TRG] button_a");
  return true;
#endif
}

static void finishPttCapture() {
  if (!micStreaming) return;
  diagCheckpoint(CP_PTT_FINISH_ENTER, true);
  micStreaming = false;

  // Let M5Unified's outstanding mic queue complete WITHOUT servicing the
  // WebSocket. RF application traffic stays out of the
  // active capture window.
  const uint32_t waitStarted = millis();
  while (M5.Mic.isRecording() && millis() - waitStarted < 250) {
    M5.update();
    delay(1);
  }
  diagCheckpoint(CP_MIC_DRAIN_DONE, true);

  if (M5.Mic.isRunning()) M5.Mic.end();
  diagCheckpoint(CP_MIC_END_DONE, true);

  // Copy the final two queue-gap blocks into PSRAM after I2S has stopped.
  while (micBufferedBlocks < micAcceptedBlocks) {
    if (!bufferMicBlock(micBufferedBlocks)) break;
    ++micBufferedBlocks;
  }
  diagCheckpoint(CP_MIC_FLUSH_DONE, true);

#if HOMEAI_WAKEWORD_ENABLE
  if (autoWakeCaptureActive &&
      autoWakeTrimOffsetBytes > 0 &&
      autoWakeTrimOffsetBytes < pttCaptureBytes) {
    const size_t remaining =
        pttCaptureBytes - autoWakeTrimOffsetBytes;

    memmove(
        pttCaptureBuffer,
        pttCaptureBuffer + autoWakeTrimOffsetBytes,
        remaining);

    pttCaptureBytes = remaining;

    Serial.printf(
        "[WAKE] trimmed leading silence bytes=%u\n",
        static_cast<unsigned>(pttCaptureBytes));
  }
#endif

  if (pttCaptureOverflow || pttCaptureBytes == 0) {
    const char* trigger = "button_a";
#if HOMEAI_WAKEWORD_ENABLE
    if (autoWakeCaptureActive) trigger = "wake_word";
#endif
    const char* reason = pttCaptureOverflow ? "buffer_overflow" : "empty";
    Serial.printf("[AUDIO] PTT abort trigger=%s reason=%s bytes=%u\n",
                  trigger, reason, static_cast<unsigned>(pttCaptureBytes));
    sendJsonEvent("ptt.abort", 0, trigger, reason);
#if HOMEAI_WAKEWORD_ENABLE
    resetAutoWakeCaptureState();
#endif
    setState(CompanionState::Error);
    endGlassVoiceIsolation();
    return;
  }

  // Important: the radio upload now happens only after Mic/I2S is fully off.
#if HOMEAI_WAKEWORD_ENABLE
  if (autoWakeCaptureActive && !autoWakePttStartSent) {
    diagCheckpoint(CP_PTT_EVENT_PRE, true);
    sendJsonEvent("ptt.start", 0, "wake_word");
    autoWakePttStartSent = true;
    diagCheckpoint(CP_PTT_RUNNING, true);
  }
#endif

  if (!transmitPttBuffer()) {
    const char* trigger = "button_a";
#if HOMEAI_WAKEWORD_ENABLE
    if (autoWakeCaptureActive) trigger = "wake_word";
#endif
    sendJsonEvent("ptt.abort", 0, trigger, "tx_failed");
#if HOMEAI_WAKEWORD_ENABLE
    resetAutoWakeCaptureState();
#endif
    setState(CompanionState::Error);
    endGlassVoiceIsolation();
    return;
  }

  const char* stopTrigger = "button_a";
#if HOMEAI_WAKEWORD_ENABLE
  if (autoWakeCaptureActive) stopTrigger = "wake_word";
#endif
  sendJsonEvent("ptt.stop", pttCaptureBytes, stopTrigger);
  diagCheckpoint(CP_PTT_STOP_SENT, true);

#if HOMEAI_WAKEWORD_ENABLE
  resetAutoWakeCaptureState();
#endif

  setState(CompanionState::Thinking);
  Serial.printf("[AUDIO] PTT stop blocks=%u buffered=%u bytes=%u\n",
                static_cast<unsigned>(micAcceptedBlocks),
                static_cast<unsigned>(micBufferedBlocks),
                static_cast<unsigned>(pttCaptureBytes));
}

static void updateMicStreaming() {
  if (!micStreaming) return;

  const uint32_t now = millis();
#if HOMEAI_WAKEWORD_ENABLE
  if (autoWakeCaptureActive) {
    if (now - autoWakeCaptureStartedMs >= AUTO_WAKE_MAX_CAPTURE_MS) {
      const uint32_t threshold = currentAutoVadThreshold();
      Serial.printf(
          "[VAD] timeout level=%u threshold=%u longest=%u\n",
          static_cast<unsigned>(autoWakeLastVadLevel),
          static_cast<unsigned>(threshold),
          static_cast<unsigned>(autoWakeLongestSilenceMs));
      Serial.println("[WAKE] max capture reached");
      finishPttCapture();
      return;
    }
  } else
#endif
  if (now - pttStartedMs >= AUDIO_MAX_PTT_MS) {
    Serial.println("[AUDIO] max PTT duration reached");
    finishPttCapture();
    return;
  }

  const uint32_t sequence = micAcceptedBlocks;
  const size_t idx = sequence % AUDIO_MIC_RING_BLOCKS;
  diagCheckpoint(CP_MIC_RECORD_PRE, false, sequence);
  if (M5.Mic.record(micRing[idx], AUDIO_MIC_BLOCK_SAMPLES, AUDIO_MIC_SAMPLE_RATE, false)) {
    ++micAcceptedBlocks;
    diagCheckpoint(CP_MIC_RECORD_OK, false, sequence);
    diagCheckpoint(CP_PTT_RUNNING, false, sequence);


    // M5Unified's official mic example keeps a two-buffer gap before consuming
    // recorded data. Preserve that rule, but copy the stable block to PSRAM
    // instead of transmitting it over Wi-Fi during capture.
    if (micAcceptedBlocks >= 3) {
      const uint32_t safeSequence = micAcceptedBlocks - 3;
      if (safeSequence >= micBufferedBlocks) {
        if (bufferMicBlock(safeSequence)) {
#if HOMEAI_WAKEWORD_ENABLE
          if (autoWakeCaptureActive) {
            const size_t safeIdx =
                safeSequence % AUDIO_MIC_RING_BLOCKS;

            updateAutoWakeVad(
                micRing[safeIdx],
                AUDIO_MIC_BLOCK_SAMPLES);

            // VAD may have ended or cancelled capture.
            if (!micStreaming) return;
          }
#endif
          ++micBufferedBlocks;
        }
      }
    }
  }

}
#else
static bool startPttCapture() { return false; }
static void finishPttCapture() {}
static void updateMicStreaming() {}
static void updateTtsPlayback() {}
#endif

static void onPttStart() {
#if COMPANION_AUDIO_ENABLE && COMPANION_GATEWAY_ENABLE
  startPttCapture();
#else
  infoPaused = true;
  drawGlassFrame();
  setState(CompanionState::Listening);
#if COMPANION_GATEWAY_ENABLE
  sendJsonEvent("ptt.start", 0, "button_a");
#endif
#endif
}

static void onPttStop() {
#if COMPANION_AUDIO_ENABLE && COMPANION_GATEWAY_ENABLE
  finishPttCapture();
#else
#if COMPANION_GATEWAY_ENABLE
  sendJsonEvent("ptt.stop", 0, "button_a");
#endif
  setState(CompanionState::Thinking);
#endif
}

static void updateMockConversation() {
#if !COMPANION_GATEWAY_ENABLE
  const uint32_t elapsed = millis() - stateSinceMs;
  if (companionState == CompanionState::Thinking && elapsed >= MOCK_THINK_MS) {
    setState(CompanionState::Speaking);
  } else if (companionState == CompanionState::Speaking && elapsed >= MOCK_SPEAK_MS) {
    setState(CompanionState::Idle);
    infoPaused = false;
    itemShownSinceMs = millis();
    drawGlassFrame();
  }
#endif
}

static void updateButtons() {
  const bool btnA = M5.BtnA.isPressed();
  const bool btnB = M5.BtnB.isPressed();

  if ((btnA && !lastBtnA) || (btnB && !lastBtnB)) {
    wakeDisplaysForActivity();
  }

  if (btnA && !lastBtnA) onPttStart();
  if (!btnA && lastBtnA) onPttStop();

  if (btnB && !lastBtnB) {
    btnBPressedMs = millis();
  }
  if (!btnB && lastBtnB) {
    const uint32_t held = millis() - btnBPressedMs;
    if (held >= 650) moveToItem(-1);
    else moveToItem(+1);
  }

  lastBtnA = btnA;
  lastBtnB = btnB;
}

static bool updateIdleAnimation() {
  if (companionState != CompanionState::Idle) {
    idleBlink = false;
    return false;
  }

  const uint32_t now = millis();
  if (!idleBlink && now - lastIdleBlinkMs >= IDLE_BLINK_INTERVAL_MS) {
    idleBlink = true;
    idleBlinkStartedMs = now;
    lastIdleBlinkMs = now;
    return true;
  }
  if (idleBlink && now - idleBlinkStartedMs >= IDLE_BLINK_DURATION_MS) {
    idleBlink = false;
    return true;
  }
  return false;
}

void setup() {
  Serial.begin(115200);
  delay(500);
  Serial.println();
  Serial.println("=== HomeAIAgent A4.5 Cyber Expression A2 Flicker-Free / base A4.4.18 RC1R9 ===");

  auto cfg = M5.config();
  M5.begin(cfg);

  initFallbackInfoItems();

#if COMPANION_GATEWAY_ENABLE
  loadPersistentDeviceConfig();
#endif

  // Native USB monitor may still be reconnecting during the first few hundred ms.
  // Delay the forensic print so the previous reset reason/breadcrumb is actually visible.
  delay(1400);
  printResetForensics();

#if COMPANION_AUDIO_ENABLE
  // StickS3 ES8311 microphone and speaker are half-duplex in M5Unified.
  // Configure the supported digital mic stage here. The analog PGA is
  // applied immediately after each M5.Mic.begin(), because that begin
  // callback restores ES8311 REG14 to minimum gain.
  auto micCfg = M5.Mic.config();
  micCfg.sample_rate = AUDIO_MIC_SAMPLE_RATE;
  micCfg.magnification = AUDIO_MIC_DIGITAL_MAG;
  micCfg.over_sampling = 2;
  micCfg.noise_filter_level = AUDIO_MIC_NOISE_FILTER_LEVEL;
  M5.Mic.config(micCfg);

  if (M5.Mic.isRunning()) M5.Mic.end();
  if (M5.Speaker.isRunning()) M5.Speaker.end();
  M5.Speaker.setVolume(AUDIO_SPEAKER_VOLUME);

  Serial.printf("[AUDIO] mic mag=%u PGA=%d dB NF=%u speaker=%u spkMag=%u\n",
                static_cast<unsigned>(AUDIO_MIC_DIGITAL_MAG),
                static_cast<int>(AUDIO_MIC_PGA_GAIN_DB),
                static_cast<unsigned>(AUDIO_MIC_NOISE_FILTER_LEVEL),
                static_cast<unsigned>(AUDIO_SPEAKER_VOLUME),
                static_cast<unsigned>(AUDIO_SPEAKER_MAGNIFICATION));

#if HOMEAI_WAKEWORD_ENABLE
  if (!initLocalWakeWordEngine()) {
    Serial.println("[WAKE] local wake word disabled due to init failure");
  }
#endif
#endif

  // Glass2 is powered only after StickS3 itself has initialized successfully.
  M5.Power.setExtOutput(true);
  delay(120);

  M5.Display.setRotation(0);
  M5.Display.setBrightness(STICKS3_ACTIVE_BRIGHTNESS);
  drawMascotFace(CompanionState::Idle, false);
  lastIdleBlinkMs = millis();

  // External Glass2 bus: G9 SDA / G10 SCL, address 0x3C.
  if (initGlass2AfterPowerOn()) {
    Serial.println("[P0-A4.3.1] Glass2 ready");
  } else {
    Serial.println("[P0-A4.3.1] Glass2 init FAILED");
    setState(CompanionState::Error);
  }

  // Hardware tests found NORMAL/FREEZE/POWER_OFF acoustically equivalent.
  // Return the product UI to normal; reset diagnostics remain enabled.
  glassDiagMode = GlassAudioDiagMode::Normal;
  Serial.println("[GLASS] isolation test closed; normal display mode restored");

#if COMPANION_GATEWAY_ENABLE
  setupGateway();
#endif

  diagCheckpoint(CP_BOOT_READY, true);
}

void loop() {
  M5.update();

#if COMPANION_GATEWAY_ENABLE
  handleSerialConfig();
  updateNetworkManager();
  if (webSocketStarted && wifiOnline) {
#if COMPANION_AUDIO_ENABLE
    // Do not service application-level WebSocket traffic during Mic/I2S capture.
    // The Wi-Fi link remains associated; queued socket work resumes immediately
    // after the microphone is stopped.
    if (!micStreaming) webSocket.loop();
#else
    webSocket.loop();
#endif
  }
#endif

#if COMPANION_AUDIO_ENABLE && HOMEAI_WAKEWORD_ENABLE
  updateWakeWordSystem();
#endif
  updateButtons();
  updateMicStreaming();
  updateTtsPlayback();
  updateMockConversation();
  updateInfoCycle();
  updateNightScreenSaver();

  // Flicker-free cyber expression uses one off-screen RGB565 canvas.
  // Its internal state-dependent frame cap protects Mic/I2S and gapless TTS.
  updateCyberExpression();

  delay(4);
}
