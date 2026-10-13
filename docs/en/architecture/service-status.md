# Status bar — model, image describer, STT and TTS with time left

> **Deutsche Version:** [service-status.md](../../de/architecture/service-status.md)

As of 2026-10-13. The collapsible bar under the title line shows what is loaded right now and
for how much longer: the language model (top-hat icon), the image describer (camera), speech
recognition (STT) and speech output (TTS). The closed header has one icon per service with a
status dot (green loaded and gently pulsing, yellow loading and pulsing faster, grey nothing
loaded, red not reachable); opened it has a line per service with its name and the time until it
unloads. The button next to it reads STT and TTS afresh. Embedding servers run on the CPU and are
not shown.

Code: [`service_status.py`](../../../aifred/lib/service_status.py) (SSOT),
[`api/service_status.py`](../../../aifred/lib/api/service_status.py) (endpoint + push),
[`ui/service_status_bar.py`](../../../aifred/ui/service_status_bar.py) (empty frame),
`serviceStatus*` in [`assets/custom.js`](../../../assets/custom.js) (rendering, countdown).

## Sources and how they stay current

| Service | State | Time left |
|---|---|---|
| llama-swap | SSE stream `/api/events` (`modelStatus`, `inflight`), held permanently by the monitor | computed: `ttl` (from `/running`) from the end of the last request, the way llama-swap counts itself. `exact: false` = the model was already loaded when AIfred started listening (upper bound, shown with `≤`) |
| STT-Suite | `GET /status` | the service's `gpu_ttl_remaining_seconds` (the CPU model stays loaded) |
| TTS-Suite | `GET /engines` | `idle_remaining_s` per running GPU engine (the container's idle timer) |

STT and TTS report the time left themselves but push nothing. AIfred therefore reads it after
every use of its own (`notify_used`, triggered in the client when a transcription ends or at the
summary frame of a reading), shortly before the reported expiry (`SERVICE_STATUS_RECHECK_MARGIN_S`;
another client may have reset the clock meanwhile), every `SERVICE_STATUS_OVERDUE_POLL_S` when the
expiry is due but has not happened (the containers check their idle time only every 30 to 120 s)
and on a click of the button. The display can therefore be too short, never too long.

## Way to the browser

The monitor calls its listeners on every change; `api/service_status.py` pushes the snapshot as
kind `service_status` over the [browser push bus](browser-push-bus.md) to all connected pages
(only the newest state stays in the queue). A page fetches the state when its stream opens, over
`GET /api/service-status` (with `?refresh=true` the server reads STT and TTS first). The time left
travels as seconds at the moment of the snapshot; the browser counts down by itself.

The `system_monitor` plugin reads the same SSOT (`system_status` with `model`, `stt`, `tts`); it
pushes nothing. The one `/running` access (`running_models`) is also used by
`tts_llm_coordination`, `backends/base.py` and the chat mixin.
