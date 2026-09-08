# Frozen local wake acknowledgement asset

`wake_ack_zaide_tts.wav` is the user-approved production-TTS wake acknowledgement for HomeAIAgent.

- Text: `在的。`
- Resource: `seed-tts-2.0`
- Voice: `zh_female_vv_uranus_bigtts`
- Format: 16 kHz / mono / PCM16 WAV
- Runtime: fully local on StickS3; no TTS request is made when waking the device.

The matching firmware array is already committed as `include/wake_ack_voice_pcm.h`.
Ordinary builds must **not** regenerate it. `generate_wake_ack_tts.sh` is retained only as an optional maintenance tool if the approved wake voice is intentionally replaced in the future.
