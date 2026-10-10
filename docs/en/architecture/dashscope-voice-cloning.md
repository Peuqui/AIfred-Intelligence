# DashScope Qwen-Audio 3 — Cloud TTS and Voice Cloning

> **Deutsche Version:** [dashscope-voice-cloning.md](../../de/architecture/dashscope-voice-cloning.md)

> As of: 2026-10-10 | Region: international/Singapore (`dashscope-intl.aliyuncs.com`)
> Code: [`aifred/lib/tts_engines/dashscope_audio3/engine.py`](../../../aifred/lib/tts_engines/dashscope_audio3/engine.py),
> [`aifred/lib/dashscope_enroll.py`](../../../aifred/lib/dashscope_enroll.py),
> [`aifred/state/_tts_config_mixin.py`](../../../aifred/state/_tts_config_mixin.py)

DashScope Qwen-Audio 3 (engine key `dashscope_audio3`) is the **cloud TTS engine**: no local
GPU, no container. On 2026-10-10 it replaced the older Qwen3-TTS cloud engine, whose cloned
voices Alibaba switched off; that engine has been removed. This document records how synthesis
and voice cloning are called.

## Engine

| Property | Value |
|----------|-------|
| Engine key | `dashscope_audio3` |
| Access | API key in the credential broker (`cloud_qwen` / `api_key`); without a key the engine is unavailable (`is_running()`) |
| Models | `qwen-audio-3.0-tts-flash` (default), `qwen-audio-3.0-tts-plus` |
| System voices | 14 (table below) |
| Cloned voices | from the shared voice tree, shown with a `★` before the name |
| Languages | `de`, `en`, `fr`, `es`, `it`, `pt`, `ru`, `ja`, `ko`, `zh` are passed as `language_type`, any other as `Auto` |
| Default voice | `Mary` |
| Escalation list | in the default list (`in_default_escalation`), display order 50 |
| Speed/pitch | not done by the API but centrally via ffmpeg (`needs_speed_postprocess`) |

Synthesis is one HTTP call (`POST /services/audio/tts/SpeechSynthesizer`, WAV, 24 kHz). The
reply holds a URL; the engine downloads the WAV file (the URL arrives as `http://`, the file is
fetched over `https://`), stores it in the TTS audio folder and returns its path. Timeout for
synthesis and download: 60 s each (`request_timeout_s`).

**Failure kinds** (`TTSFailure`, see [tts-escalation.md](tts-escalation.md)): a network or
internet outage is `unreachable`; everything else, including an error answer from the API
(authentication, quota, unknown voice), is `engine`.

### System voices

Display name → voice ID at Alibaba. The names are listed in `_VOICES` of the engine; the code
notes that all voices speak German understandably although the Alibaba docs list only Chinese
and English — the voice carries the accent.

| Name | ID | Name | ID |
|------|----|------|----|
| Mary | `loongmary` | Xiao Xin | `longanxiaoxin` |
| Eva | `loongeva_v3.6` | Huan | `longanhuan_v3.6` |
| John | `loongjohn` | Li Dou | `longjielidou_v3.6` |
| Ling Xin | `longanlingxin` | Pao Pao | `longpaopao_v3.6` |
| Lu Feng | `longanlufeng` | Huo Huo | `longhuohuo_v3.6` |
| Feng Yue | `longanfengyue` | Chuan Shu | `longchuanshu_v3.6` |
| Yuan Fei | `longanyuanfei` | Ling Xi | `longanlingxi` |

`Ling Xin` and `Lu Feng` only work with the **plus** model; the engine picks the model per voice
itself. All other voices, cloned ones included, use **flash**.

## Cloned voices

The source is the shared voice tree `TTS_VOICES_DIR` (`docker/tts/voices/<Name>/<Name>.wav`, the
same one the container engines use, see [tts-container-conventions.md](tts-container-conventions.md)).
Each reference WAV is enrolled with DashScope **once**. The returned `voice_id` is kept in the
mapping file [`data/tts/dashscope_audio3_voices.json`](../../../data/tts/); this file is the
truth on whether a voice is enrolled (the cloud's own listing is no use for that check).

Per voice name it holds:

| Field | Content |
|-------|---------|
| `voice_id` | the ID DashScope assigned |
| `wav_sha256` | SHA-256 of the reference WAV |
| `target_model` | model the voice was enrolled for (`qwen-audio-3.0-tts-flash`) |

A cloned voice is bound to its `target_model`; synthesis must use the same model — which is why
cloned voices always run on flash.

### Enrolling voices

API (`dashscope_enroll.py`): `POST /api/v1/services/audio/tts/customization` with
`model: "voice-enrollment"`, `input.action: "create_voice"`, `input.target_model`,
`input.prefix` (start of the name: lowercase letters and digits only, at most 9 characters) and
the reference WAV in **full length** as a base64 data URI in `input.url`. The `voice_id` is in
the reply under `output.voice_id`. Timeout 90 s per voice.

Enrollment is triggered by the engine's `prepare_voices()`. The call comes from
`_apply_planned_tts` (`_tts_config_mixin.py`) when spoken output is switched on and on every
change of the escalation list, once per engine with an enabled entry. The run
(`enroll_progress()`):

1. Every subfolder of `TTS_VOICES_DIR` holding a `<Name>/<Name>.wav` is considered.
2. If the name is in the mapping **and** the WAV's SHA-256 matches, it is skipped (no cloud call).
3. Otherwise — new or changed WAV — the voice is enrolled and the mapping saved.
4. The run reports line by line to the debug console and **always** ends with a summary line
   ("N new, M already current"), even when there was nothing to do.

Without an API key or a voices folder, `enroll_progress()` says so and enrolls nothing. If
enrolling a voice fails, it is not in the mapping and not available; there is no substitute path.

**Forcing a re-enrollment:** delete the entry (or the whole file) from
`dashscope_audio3_voices.json`. The folder is not watched at runtime; the trigger is switching
spoken output on or changing the list.

### Voice list

The cloned voices are not fixed in code: `_cloned_voices()` reads the mapping on every call
(`★ Name` → `voice_id`) and puts them in front of the system voices. A freshly enrolled voice
appears in the selection without a restart.

## Not verified from the code

The previous version of this document described limits of the old Qwen3-TTS cloud API (no
reference text on enrollment, no sampling parameters, style control only with a different
model). Whether these statements hold for Qwen-Audio 3 is not shown by the code — the engine
only sends `text`, `voice`, `language_type`, `format` and `sample_rate`, and enrollment sends no
reference text. Alibaba's official docs (Model Studio, "Qwen-Audio TTS") are the source for that.
