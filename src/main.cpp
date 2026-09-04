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

#if COMPANION_GATEWAY_ENABLE
  #include <WiFi.h>
  #include <WebSocketsClient.h>
  #include <ArduinoJson.h>
  #include "secrets.h"
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
  Serial.println("[CONFIG] Use tools/configure_terminal.py or serial CFG command.");
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

// A4.0: Mini pre-renders the complete 128x64 monochrome Glass2 frame.
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

// P0-A2.6: isolate whether Glass2 noise comes from UI traffic or shared power.
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

// A3.7: a 15 s mono PCM16/16k utterance is only ~480 KB.
// Keep it in PSRAM during capture, then transmit after Mic/I2S has stopped.
// This avoids overlapping the microphone capture current with Wi-Fi RF TX peaks.
static uint8_t* pttCaptureBuffer = nullptr;
static size_t pttCaptureBytes = 0;
static bool pttCaptureOverflow = false;

// A3.9 gapless TTS pipeline.
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
#endif

// A2.7 reset forensics.
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
    Serial.println("[BOOT-DIAG] no valid previous breadcrumb (power-on or RTC state lost)");
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

// A4.2 night screen protection.
// Wall-clock policy comes from the Mac mini Gateway.
// During the night window, any button wakes both displays immediately for
// two minutes. Core/Wi-Fi/audio/Gateway remain running throughout.
bool nightScreenWindowActive = false;
bool displaysSleeping = false;
uint32_t manualWakeUntilMs = 0;
static constexpr uint32_t NIGHT_MANUAL_WAKE_MS = 120000;
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
    case CompanionState::Idle:      return "在这里";
    case CompanionState::Listening: return "正在听";
    case CompanionState::Thinking:  return "想一想";
    case CompanionState::Speaking:  return "正在说";
    case CompanionState::Success:   return "完成啦";
    case CompanionState::Error:     return "出错了";
  }
  return "";
}

static void setState(CompanionState state) {
  if (companionState == state) return;
  companionState = state;
  stateSinceMs = millis();
  idleBlink = false;
}

static uint16_t rgb565(uint8_t r, uint8_t g, uint8_t b) {
  return M5.Display.color565(r, g, b);
}

static void drawMascotFace(CompanionState state, bool blink = false) {
  if (displaysSleeping) return;
  auto& d = M5.Display;
  const int w = d.width();
  const int h = d.height();

  const uint16_t bg      = rgb565(6, 11, 18);
  const uint16_t visor   = rgb565(22, 38, 49);
  const uint16_t accent  = (state == CompanionState::Thinking) ? rgb565(255, 205, 74)
                           : (state == CompanionState::Listening || state == CompanionState::Success)
                             ? rgb565(92, 231, 157)
                             : (state == CompanionState::Error)
                               ? rgb565(255, 107, 107)
                               : rgb565(73, 222, 231);
  const uint16_t white   = rgb565(246, 252, 252);
  const uint16_t pupil   = rgb565(11, 24, 31);
  const uint16_t blush   = rgb565(255, 132, 167);
  const uint16_t muted   = rgb565(138, 160, 171);

  d.fillScreen(bg);

  // P0-A1.2 treats the LCD as a "face window" inside a future fixed shell.
  // Only a small state label remains outside the expression window.
  d.setFont(&fonts::efontCN_12_b);
  d.setTextDatum(middle_center);
  d.setTextColor(accent, bg);
  d.drawString(stateLabel(state), w / 2, 20);

  d.fillRoundRect(12, 42, w - 24, 126, 24, visor);
  d.drawRoundRect(12, 42, w - 24, 126, 24, accent);

  const int eyeY = 91;
  const int leftX = w / 2 - 25;
  const int rightX = w / 2 + 25;

  if (blink && state == CompanionState::Idle) {
    d.drawRoundRect(leftX - 13, eyeY - 2, 26, 5, 2, white);
    d.drawRoundRect(rightX - 13, eyeY - 2, 26, 5, 2, white);
  } else if (state == CompanionState::Success) {
    d.drawArc(leftX, eyeY + 4, 13, 9, 195, 345, white);
    d.drawArc(rightX, eyeY + 4, 13, 9, 195, 345, white);
  } else if (state == CompanionState::Error) {
    d.drawLine(leftX - 9, eyeY - 7, leftX + 9, eyeY + 7, white);
    d.drawLine(leftX + 9, eyeY - 7, leftX - 9, eyeY + 7, white);
    d.drawLine(rightX - 9, eyeY - 7, rightX + 9, eyeY + 7, white);
    d.drawLine(rightX + 9, eyeY - 7, rightX - 9, eyeY + 7, white);
  } else {
    // Large digital-pet eyes. Thinking looks upward; listening widens pupils.
    d.fillRoundRect(leftX - 15, eyeY - 17, 30, 34, 12, white);
    d.fillRoundRect(rightX - 15, eyeY - 17, 30, 34, 12, white);

    int px = 0, py = 3, pr = 7;
    if (state == CompanionState::Thinking) { px = 5; py = -4; }
    if (state == CompanionState::Listening) { pr = 8; py = 1; }

    d.fillCircle(leftX + px, eyeY + py, pr, pupil);
    d.fillCircle(rightX + px, eyeY + py, pr, pupil);
    d.fillCircle(leftX + px - 3, eyeY + py - 4, 2, white);
    d.fillCircle(rightX + px - 3, eyeY + py - 4, 2, white);
  }

  if (state != CompanionState::Thinking && state != CompanionState::Error) {
    d.fillCircle(leftX - 20, 120, 3, blush);
    d.fillCircle(rightX + 20, 120, 3, blush);
  }

  const int mx = w / 2;
  const int my = 136;
  if (state == CompanionState::Listening) {
    d.drawCircle(mx, my, 7, accent);
  } else if (state == CompanionState::Speaking) {
    const bool open = ((millis() / 180) & 1) != 0;
    if (open) d.fillEllipse(mx, my, 7, 10, accent);
    else d.drawFastHLine(mx - 8, my, 17, accent);
  } else if (state == CompanionState::Thinking) {
    d.fillCircle(mx, my, 2, accent);
    d.fillCircle(mx + 20, 58, 2, accent);
    d.fillCircle(mx + 27, 53, 3, accent);
    d.fillCircle(mx + 35, 46, 4, accent);
  } else if (state == CompanionState::Error) {
    d.drawArc(mx, my + 10, 14, 9, 205, 335, accent);
  } else {
    d.drawArc(mx, my - 3, 15, 10, 20, 160, accent);
  }

  d.setFont(&fonts::efontCN_10);
  d.setTextColor(muted, bg);
  d.setTextDatum(middle_center);
  d.drawString("A 说话  ·  B 资讯", w / 2, h - 18);
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
  // A4.2 matches the production layout: no category/header, three body lines,
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
    Serial.println("[GLASS-DIAG] voice mode=FREEZE (powered, zero display writes)");
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
  Serial.println("[GLASS-DIAG] voice mode=POWER_OFF (EXT 5V disabled)");
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

static void sendJsonEvent(const char* type, size_t audioBytes = 0) {
  if (!gatewayConnected) return;

  JsonDocument doc;
  doc["type"] = type;
  doc["protocol"] = 2;
  doc["diag_glass_mode"] = glassDiagLabel();

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
        setNightScreenWindow(true);
      }
      else if (!strcmp(msgType, "display.wake")) {
        setNightScreenWindow(false);
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

  // A3.7: keep the brownout detector ON and reduce the source of the spike
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

static bool startPttCapture() {
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
  sendJsonEvent("ptt.start");
  diagCheckpoint(CP_PTT_RUNNING, true);
  Serial.println("[AUDIO] PTT start");
  return true;
}

static void finishPttCapture() {
  if (!micStreaming) return;
  diagCheckpoint(CP_PTT_FINISH_ENTER, true);
  micStreaming = false;

  // Let M5Unified's outstanding mic queue complete WITHOUT servicing the
  // WebSocket. A3.7 intentionally keeps RF application traffic out of the
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

  if (pttCaptureOverflow || pttCaptureBytes == 0) {
    Serial.println("[AUDIO] invalid PTT capture buffer; aborting turn");
    sendJsonEvent("ptt.stop", 0);
    setState(CompanionState::Error);
    endGlassVoiceIsolation();
    return;
  }

  // Important: the radio upload now happens only after Mic/I2S is fully off.
  if (!transmitPttBuffer()) {
    sendJsonEvent("ptt.stop", 0);
    setState(CompanionState::Error);
    endGlassVoiceIsolation();
    return;
  }

  sendJsonEvent("ptt.stop", pttCaptureBytes);
  diagCheckpoint(CP_PTT_STOP_SENT, true);
  setState(CompanionState::Thinking);
  Serial.printf("[AUDIO] PTT stop blocks=%u buffered=%u bytes=%u\n",
                static_cast<unsigned>(micAcceptedBlocks),
                static_cast<unsigned>(micBufferedBlocks),
                static_cast<unsigned>(pttCaptureBytes));
}

static void updateMicStreaming() {
  if (!micStreaming) return;

  const uint32_t sequence = micAcceptedBlocks;
  const size_t idx = sequence % AUDIO_MIC_RING_BLOCKS;
  diagCheckpoint(CP_MIC_RECORD_PRE, false, sequence);
  if (M5.Mic.record(micRing[idx], AUDIO_MIC_BLOCK_SAMPLES, AUDIO_MIC_SAMPLE_RATE, false)) {
    ++micAcceptedBlocks;
    diagCheckpoint(CP_MIC_RECORD_OK, false, sequence);
    diagCheckpoint(CP_PTT_RUNNING, false, sequence);

    if ((micAcceptedBlocks % 25u) == 0u) {
      Serial.printf(
          "[MEM] blocks=%u heap=%u min=%u psram=%u internal=%u largestInt=%u dma=%u\n",
          static_cast<unsigned>(micAcceptedBlocks),
          static_cast<unsigned>(heap_caps_get_free_size(MALLOC_CAP_8BIT)),
          static_cast<unsigned>(heap_caps_get_minimum_free_size(MALLOC_CAP_8BIT)),
          static_cast<unsigned>(heap_caps_get_free_size(MALLOC_CAP_SPIRAM)),
          static_cast<unsigned>(heap_caps_get_free_size(MALLOC_CAP_INTERNAL | MALLOC_CAP_8BIT)),
          static_cast<unsigned>(heap_caps_get_largest_free_block(MALLOC_CAP_INTERNAL | MALLOC_CAP_8BIT)),
          static_cast<unsigned>(heap_caps_get_free_size(MALLOC_CAP_DMA)));
    }

    // M5Unified's official mic example keeps a two-buffer gap before consuming
    // recorded data. Preserve that rule, but copy the stable block to PSRAM
    // instead of transmitting it over Wi-Fi during capture.
    if (micAcceptedBlocks >= 3) {
      const uint32_t safeSequence = micAcceptedBlocks - 3;
      if (safeSequence >= micBufferedBlocks) {
        if (bufferMicBlock(safeSequence)) {
          ++micBufferedBlocks;
        }
      }
    }
  }

  if (millis() - pttStartedMs >= AUDIO_MAX_PTT_MS) {
    Serial.println("[AUDIO] max PTT duration reached");
    finishPttCapture();
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
  sendJsonEvent("ptt.start");
#endif
#endif
}

static void onPttStop() {
#if COMPANION_AUDIO_ENABLE && COMPANION_GATEWAY_ENABLE
  finishPttCapture();
#else
#if COMPANION_GATEWAY_ENABLE
  sendJsonEvent("ptt.stop");
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
  Serial.println("=== HomeAIAgent P0-A4.2 THREE-LINE + NIGHT SCREEN + A3.9 GAPLESS ===");

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
    Serial.println("[P0-A4.2] Glass2 ready");
  } else {
    Serial.println("[P0-A4.2] Glass2 init FAILED");
    setState(CompanionState::Error);
  }

  // A2.6 hardware test found NORMAL/FREEZE/POWER_OFF acoustically equivalent.
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
    // A3.7: no application-level WebSocket servicing during Mic/I2S capture.
    // The Wi-Fi link remains associated; queued socket work resumes immediately
    // after the microphone is stopped.
    if (!micStreaming) webSocket.loop();
#else
    webSocket.loop();
#endif
  }
#endif

  updateButtons();
  updateMicStreaming();
  updateTtsPlayback();
  updateMockConversation();
  updateInfoCycle();
  updateNightScreenSaver();

  static CompanionState lastDrawnState = static_cast<CompanionState>(255);
  static bool lastDrawnBlink = false;
  const bool animationChanged = updateIdleAnimation();

  if (lastDrawnState != companionState || animationChanged || lastDrawnBlink != idleBlink) {
    drawMascotFace(companionState, idleBlink);
    lastDrawnState = companionState;
    lastDrawnBlink = idleBlink;
  }

  delay(4);
}
