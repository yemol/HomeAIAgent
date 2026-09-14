Import("env")

from pathlib import Path
import shutil
import re

PROJECT = Path(env["PROJECT_DIR"])
print("[ESP-SR-CN] patcher=guarded-cn-multinet+a1r25b1.3-sr-rollback-stable")
TARGET = PROJECT / ".pio-local" / "ESP-SR-For-M5Unified" / "src" / "esp32-hal-sr-m5.c"
BACKUP = TARGET.with_name(TARGET.name + ".homeai-a4.4.12.prepatch")
CN_MARKER = "HOMEAI_A4_4_10_MULTINET_CN"
GUARD_MARKER = "HOMEAI_A4_4_12_MULTINET_ONLY_GUARDED"
LEGACY_GUARD_MARKER = "HOMEAI_A4_4_11_MULTINET_ONLY_GUARDED"
OBS_MARKER = "HOMEAI_A1R25A_MULTINET_OBSERVATORY"
OBS_RESULT_ANCHOR = "int sr_command_id = mn_result->command_id[0];"
B2_PROB_EXPORT_MARKER = "HOMEAI_A1R25B2_PROB_EXPORT"
B2_PROB_ASSIGN_MARKER = "HOMEAI_A1R25B2_PROB_ASSIGN"


START_ANCHOR = "esp_err_t sr_start_m5(\n"
INIT_ANCHOR = "  // Init Model\n"
COMMAND_COMMENT_ANCHOR = "  // Load Custom Command Detection only if commands are provided\n"
COMMAND_IF_ANCHOR = "  if (cmd_number > 0)\n  {\n"
ADD_COMMANDS_ANCHOR = "    // Add commands\n"

HELPER = r'''/* HOMEAI_A4_4_12_MULTINET_ONLY_GUARDED
 * HomeAIAgent uses Chinese MultiNet directly as a local trigger recognizer.
 * No WakeNet model is bundled. Guard every model/AFE/MultiNet handle so a
 * failed model init returns ESP_FAIL instead of dereferencing NULL.
 */
static esp_err_t homeai_sr_init_fail_m5(afe_config_t *afe_config, const char *stage)
{
  log_e("[HOMEAI-SR] init failed stage=%s", stage ? stage : "unknown");

  if (afe_config != NULL)
  {
    afe_config_free(afe_config);
  }

  if (g_sr_data_m5 != NULL)
  {
    if (g_sr_data_m5->model_data != NULL && g_sr_data_m5->multinet != NULL)
    {
      g_sr_data_m5->multinet->destroy(g_sr_data_m5->model_data);
      g_sr_data_m5->model_data = NULL;
    }
    if (g_sr_data_m5->afe_data != NULL && g_sr_data_m5->afe_handle != NULL)
    {
      g_sr_data_m5->afe_handle->destroy(g_sr_data_m5->afe_data);
      g_sr_data_m5->afe_data = NULL;
    }
    if (g_sr_data_m5->result_que != NULL)
    {
      vQueueDelete(g_sr_data_m5->result_que);
      g_sr_data_m5->result_que = NULL;
    }
    if (g_sr_data_m5->event_group != NULL)
    {
      vEventGroupDelete(g_sr_data_m5->event_group);
      g_sr_data_m5->event_group = NULL;
    }
    heap_caps_free(g_sr_data_m5);
    g_sr_data_m5 = NULL;
  }

  if (models_m5 != NULL)
  {
    esp_srmodel_deinit(models_m5);
    models_m5 = NULL;
  }

  return ESP_FAIL;
}

'''

AFE_BLOCK = r'''  // Init Model
  log_i("[HOMEAI-SR] stage=model-init");
  models_m5 = esp_srmodel_init("model");
  if (models_m5 == NULL)
  {
    return homeai_sr_init_fail_m5(NULL, "model-partition");
  }

  log_i("[HOMEAI-SR] model-count=%d", models_m5->num);
  for (int homeai_i = 0; homeai_i < models_m5->num; ++homeai_i)
  {
    const char *homeai_name = models_m5->model_name[homeai_i];
    log_i("[HOMEAI-SR] model[%d]=%s", homeai_i, homeai_name ? homeai_name : "<null>");
  }

  log_i("[HOMEAI-SR] stage=afe-config");
  afe_config_t *afe_config = afe_config_init(input_format, models_m5, AFE_TYPE_SR, AFE_MODE_LOW_COST);
  if (afe_config == NULL)
  {
    return homeai_sr_init_fail_m5(NULL, "afe-config");
  }

  if (mode == SR_MODE_COMMAND)
  {
    afe_config->wakenet_init = false;
    log_i("[HOMEAI-SR] command-only mode: WakeNet disabled");
  }

  g_sr_data_m5->afe_handle = esp_afe_handle_from_config(afe_config);
  if (g_sr_data_m5->afe_handle == NULL)
  {
    return homeai_sr_init_fail_m5(afe_config, "afe-handle");
  }

  log_i("[HOMEAI-SR] stage=afe-create wakenet=%s",
        afe_config->wakenet_model_name ? afe_config->wakenet_model_name : "<none>");
  g_sr_data_m5->afe_data = g_sr_data_m5->afe_handle->create_from_config(afe_config);
  if (g_sr_data_m5->afe_data == NULL)
  {
    return homeai_sr_init_fail_m5(afe_config, "afe-create");
  }
  afe_config_free(afe_config);
  afe_config = NULL;
  log_i("[HOMEAI-SR] AFE ready");
'''

MULTINET_PREFIX = r'''    /* HOMEAI_A4_4_10_MULTINET_CN: HomeAIAgent bundles mn5q8_cn. */
#if defined(HOMEAI_TRIGGER_MULTINET_CN) && HOMEAI_TRIGGER_MULTINET_CN
    char *mn_name = esp_srmodel_filter(models_m5, ESP_MN_PREFIX, ESP_MN_CHINESE);
#else
    char *mn_name = esp_srmodel_filter(models_m5, ESP_MN_PREFIX, ESP_MN_ENGLISH);
#endif
    if (mn_name == NULL)
    {
      return homeai_sr_init_fail_m5(NULL, "multinet-name");
    }
    log_i("[HOMEAI-SR] MultiNet selected=%s", mn_name);

    g_sr_data_m5->multinet = esp_mn_handle_from_name(mn_name);
    if (g_sr_data_m5->multinet == NULL)
    {
      return homeai_sr_init_fail_m5(NULL, "multinet-handle");
    }

    log_i("[HOMEAI-SR] stage=multinet-create");
    g_sr_data_m5->model_data = g_sr_data_m5->multinet->create(mn_name, 5760);
    if (g_sr_data_m5->model_data == NULL)
    {
      return homeai_sr_init_fail_m5(NULL, "multinet-create");
    }
    log_i("[HOMEAI-SR] MultiNet ready");
'''


def require_once(text: str, token: str, label: str, start: int = 0) -> int:
    pos = text.find(token, start)
    if pos < 0:
        raise RuntimeError(f"[ESP-SR-CN] {label} anchor not found")
    if text.find(token, pos + len(token)) >= 0:
        raise RuntimeError(f"[ESP-SR-CN] {label} anchor is ambiguous")
    return pos


def validate(text: str) -> None:
    checks = [
        GUARD_MARKER,
        CN_MARKER,
        "ESP_MN_CHINESE",
        "command-only mode: WakeNet disabled",
        "stage=model-init",
        "model-partition",
        "afe-create",
        "multinet-name",
        "multinet-create",
    ]
    missing = [item for item in checks if item not in text]
    if missing:
        raise RuntimeError("[ESP-SR-CN] post-patch validation failed: " + ", ".join(missing))


if not TARGET.is_file():
    raise RuntimeError(
        "[ESP-SR-CN] local wrapper missing: %s\n"
        "Keep the existing .pio-local/ESP-SR-For-M5Unified directory in place."
        % TARGET
    )

text = TARGET.read_text(encoding="utf-8")

# A1R25B1.3 rollback cleanup.
# A1R25B2/B2.1 experimentally exported the top MultiNet probability from the
# low-level ESP-SR wrapper. Wake recall regressed in real-device testing, so
# remove ONLY those two marked additions and restore the previous A1R25A.1
# observe-only wrapper behavior. Keep .pio-local itself intact.
rollback_changed = False

export_pattern = re.compile(
    rf"/\* {re.escape(B2_PROB_EXPORT_MARKER)}: diagnostics-only top probability export\. \*/\n"
    r"static volatile int homeai_sr_last_prob_x10000_m5 = -1;\n"
    r"int homeai_sr_get_last_prob_x10000_m5\(void\)\n"
    r"\{\n"
    r"  return homeai_sr_last_prob_x10000_m5;\n"
    r"\}\n\n"
)
text, export_count = export_pattern.subn("", text)
if export_count > 1:
    raise RuntimeError("[ESP-SR-CN] multiple A1R25B2 probability export helpers found; refusing unsafe rollback")
if export_count == 1:
    rollback_changed = True
    print("[ESP-SR-CN] rollback removed A1R25B2 probability export helper")

assign_pattern = re.compile(
    r"^[ \t]*if \(homeai_obs_i == 0\) \{ homeai_sr_last_prob_x10000_m5 = homeai_prob_x10000; \}"
    r"[ \t]*/\* " + re.escape(B2_PROB_ASSIGN_MARKER) + r" \*/\n",
    re.MULTILINE,
)
text, assign_count = assign_pattern.subn("", text)
if assign_count > 1:
    raise RuntimeError("[ESP-SR-CN] multiple A1R25B2 probability assignments found; refusing unsafe rollback")
if assign_count == 1:
    rollback_changed = True
    print("[ESP-SR-CN] rollback removed A1R25B2 probability assignment")

if B2_PROB_EXPORT_MARKER in text or B2_PROB_ASSIGN_MARKER in text:
    raise RuntimeError("[ESP-SR-CN] A1R25B2 probability markers remain after rollback")

if rollback_changed:
    TARGET.write_text(text, encoding="utf-8")
    print("[ESP-SR-CN] rollback PASS: restored pre-A1R25B2 low-level SR instrumentation")
else:
    print("[ESP-SR-CN] rollback not needed: A1R25B2 low-level probability taps absent")

# Remove the withdrawn candidate-threshold experiment if it is still present.
# Never replace or delete .pio-local; only remove the known marked block.
WITHDRAWN_THRESHOLD_MARKER = "HOMEAI_A4_5_WAKE_CANDIDATE_THRESHOLD"
if WITHDRAWN_THRESHOLD_MARKER in text:
    threshold_start_token = "    /* HOMEAI_A4_5_WAKE_CANDIDATE_THRESHOLD"
    threshold_start = require_once(text, threshold_start_token, "candidate-threshold-start")
    threshold_end = text.find(ADD_COMMANDS_ANCHOR, threshold_start)
    if threshold_end < 0:
        raise RuntimeError("[ESP-SR-CN] candidate-threshold block end anchor not found; refusing unsafe edit")
    text = text[:threshold_start] + text[threshold_end:]
    TARGET.write_text(text, encoding="utf-8")
    print("[ESP-SR-CN] removed withdrawn candidate-threshold override; MultiNet default restored")
else:
    print("[ESP-SR-CN] candidate-threshold override absent; MultiNet default unchanged")

if WITHDRAWN_THRESHOLD_MARKER in TARGET.read_text(encoding="utf-8"):
    raise RuntimeError("[ESP-SR-CN] validation failed: withdrawn threshold override still present")

if GUARD_MARKER in text:
    validate(text)
    print("[ESP-SR-CN] guarded CN patch already applied")
else:
    if not BACKUP.exists():
        shutil.copy2(TARGET, BACKUP)
        print(f"[ESP-SR-CN] backup={BACKUP}")

    # Accept upstream, CN-selector-only, or previously patched wrappers as long as
    # the sr_start_m5 structural anchors remain intact.
    start_pos = require_once(text, START_ANCHOR, "sr_start_m5")

    if GUARD_MARKER not in text:
        # If a failed/manual helper was ever persisted, remove only that
        # helper marker text by rejecting it rather than stacking helpers.
        if LEGACY_GUARD_MARKER in text:
            raise RuntimeError(
                "[ESP-SR-CN] legacy A4.4.11 guarded helper is already persisted. "
                "Restore esp32-hal-sr-m5.c.homeai-a4.4.12.prepatch or the A4.4.10 backup before retrying."
            )
        text = text[:start_pos] + HELPER + text[start_pos:]
        start_pos += len(HELPER)

    init_pos = text.find(INIT_ANCHOR, start_pos)
    command_comment_pos = text.find(COMMAND_COMMENT_ANCHOR, init_pos)
    if init_pos < 0 or command_comment_pos < 0 or command_comment_pos <= init_pos:
        raise RuntimeError("[ESP-SR-CN] sr_start_m5 model-init structure not found")

    # Replace by structural boundaries so already-patched sources migrate safely.
    text = text[:init_pos] + AFE_BLOCK + text[command_comment_pos:]

    # Locate command block again after AFE replacement.
    start_pos = require_once(text, START_ANCHOR, "sr_start_m5")
    cmd_if_pos = text.find(COMMAND_IF_ANCHOR, start_pos)
    if cmd_if_pos < 0:
        raise RuntimeError("[ESP-SR-CN] command-mode block not found")
    body_pos = cmd_if_pos + len(COMMAND_IF_ANCHOR)
    add_commands_pos = text.find(ADD_COMMANDS_ANCHOR, body_pos)
    if add_commands_pos < 0:
        raise RuntimeError("[ESP-SR-CN] add-commands anchor not found")

    text = text[:body_pos] + MULTINET_PREFIX + text[add_commands_pos:]

    validate(text)
    TARGET.write_text(text, encoding="utf-8")
    print("[ESP-SR-CN] migrated wrapper by structural anchors")

# A1R25A.1 diagnostics-only probability tap. This runs after the guarded CN
# migration and only adds log output when MultiNet has already detected a
# command. It does not change thresholds, candidates, phrase tables, or mode.
verify = TARGET.read_text(encoding="utf-8")
if OBS_MARKER not in verify:
    obs_pos = verify.find(OBS_RESULT_ANCHOR)
    if obs_pos >= 0:
        line_start = verify.rfind("\n", 0, obs_pos) + 1
        indent = verify[line_start:obs_pos]
        obs_block = (
            f"{indent}/* {OBS_MARKER}: observe-only probability log. */\n"
            f"{indent}if (mn_result != NULL)\n"
            f"{indent}{{\n"
            f"{indent}  int homeai_obs_count = mn_result->num < 5 ? mn_result->num : 5;\n"
            f"{indent}  log_w(\"[WAKE-MN] candidates=%d\", mn_result->num);\n"
            f"{indent}  for (int homeai_obs_i = 0; homeai_obs_i < homeai_obs_count; ++homeai_obs_i)\n"
            f"{indent}  {{\n"
            f"{indent}    int homeai_prob_x10000 = (int)(mn_result->prob[homeai_obs_i] * 10000.0f + 0.5f);\n"
            f"{indent}    log_w(\"[WAKE-MN] rank=%d command=%d phrase=%d prob_x10000=%d\",\n"
            f"{indent}          homeai_obs_i + 1,\n"
            f"{indent}          mn_result->command_id[homeai_obs_i],\n"
            f"{indent}          mn_result->phrase_id[homeai_obs_i],\n"
            f"{indent}          homeai_prob_x10000);\n"
            f"{indent}  }}\n"
            f"{indent}}}\n"
        )
        verify = verify[:line_start] + obs_block + verify[line_start:]
        TARGET.write_text(verify, encoding="utf-8")
        print("[ESP-SR-CN] A1R25A.1 MultiNet probability observatory applied")
    else:
        print("[ESP-SR-CN] WARN A1R25A.1 probability anchor unavailable; app-level observatory remains active")
else:
    print("[ESP-SR-CN] A1R25A.1 MultiNet probability observatory already applied")

verify = TARGET.read_text(encoding="utf-8")
validate(verify)
print(f"[ESP-SR-CN] source={TARGET}")
print("[ESP-SR-CN] validation PASS: guarded Chinese MultiNet + A1R25A.1 evidence observatory")
