# Status bar — model, image describer, STT and TTS with time left

> **Deutsche Version:** [service-status.md](../../de/architecture/service-status.md)

As of 2026-11-04. The collapsible bar under the title line shows what is loaded right now and
for how much longer: the language model (top-hat icon), the image describer (camera), speech
recognition (STT) and speech output (TTS). The closed header has one icon per service with a
status dot (green loaded and gently pulsing, yellow loading and pulsing faster, grey nothing
loaded, red not reachable); opened it has a line per service with its name, the time until it
unloads and a button to unload or load (see below). The button on the right reads STT and TTS afresh.
Embedding servers run on the CPU and are not shown.

Code: [`service_status.py`](../../../aifred/lib/service_status.py) (SSOT),
[`api/service_status.py`](../../../aifred/lib/api/service_status.py) (endpoint + push),
[`service_control.py`](../../../aifred/lib/service_control.py) (loading and unloading),
[`ui/service_status_bar.py`](../../../aifred/ui/service_status_bar.py) (empty frame),
`serviceStatus*` in [`assets/custom.js`](../../../assets/custom.js) (rendering, countdown).

## Sources and how they stay current

| Service | State | Time left |
|---|---|---|
| llama-swap | SSE stream `/api/events` (`modelStatus`, `inflight`), held permanently by the monitor. Since llama-swap v269 `inflight` reports changes per request: `snapshot` (all running ones on connect, without the list when none runs), `upsert` (start and progress, about every 250 ms while streaming), `remove` (end). The monitor tells only when the number of running requests per model changes. An event of unknown form (llama-swap changed it) counts as a lost connection: a warning in the log, llama-swap red as not reachable, a new connection after `SERVICE_STATUS_SSE_RECONNECT_S` | computed: `ttl` (from `/running`) from the end of the last request, the way llama-swap counts itself. `exact: false` = the model was already loaded when AIfred started listening (upper bound, shown with `≤`) |
| STT-Suite | `GET /status` | the service's `gpu_ttl_remaining_seconds` (the CPU model stays loaded) |
| TTS-Suite | `GET /engines` | `idle_remaining_s` per running GPU engine (the container's idle timer) |

STT and TTS report the time left themselves but push nothing. AIfred therefore reads it after
every change of its own (`notify_changed`, triggered in the client when a transcription ends, at the
first sound of a reading (it may just have started the engine) and at its summary frame, at every release
of the suites by AIfred, and in the GPU-Orc client once GPU-Orc released something for AIfred), shortly before the reported expiry (`SERVICE_STATUS_RECHECK_MARGIN_S`;
another client may have reset the clock meanwhile), every `SERVICE_STATUS_OVERDUE_POLL_S` when the
expiry is due but has not happened (the containers check their idle time only every 30 to 120 s)
and on a click of the button. The time left can therefore be too short, never too long. When GPU-Orc
releases a card for another caller (Agent-Orc, vllm-research), AIfred sees it only at its next
scheduled look or on a click of the button.

## Loading and unloading by button

Each line has a power button (`serviceStatusActionButton` in `custom.js`, endpoints
`POST /api/service-status/unload` and `/load`):

| Line | Button | Way |
|---|---|---|
| Language model, describer, loaded and idle | Unload | `POST /api/models/unload/<model>` to llama-swap. Only a model the monitor lists (the name goes into the URL path), and only while no request runs |
| Language model, nothing loaded | Load | the main model a chat request would load now (`get_effective_model_from_settings("aifred")`), through `LLMBackend.load_model`, so like every request: GPU-Orc clears the cards, llama-swap loads. Only with llama.cpp or vLLM as backend |
| Describer, nothing loaded | Load | the `-visiond` placement of the vision LLM in the settings that fits next to what is loaded (`fitting_visiond`). If none fits, nothing loads and the message says why: the describer never pushes the main model out. TTS and STT on its card GPU-Orc clears by its ranking, a running reading is aborted |
| STT, GPU | Unload | `POST /gpu/release` of the STT-Suite with `force`: a running transcription is aborted (with `device=auto` it goes on on the CPU) |
| TTS, local engine | Unload | `POST /gpu/release` of the TTS-Suite for this engine with `force`: a running reading is aborted |

STT and TTS have no load button, they load with their first request; the STT-Suite's CPU model never
unloads, and a TTS engine on another machine is not this machine's business. Unloading goes to the
suite directly, not through GPU-Orc: it clears only whole cards or room for a model, and both suites
may sit on the same card.

Loading answers at once and goes on in the background (a big model takes minutes, a closed tab does
not stop it); the line shows "loading" through llama-swap's events, a failure lands in the debug
console. A refusal (working right now, does not fit, not loaded) comes back as 409 with the message in
the page's language and shows under the list for a while (`service-status-notice`). There is
deliberately no confirmation before unloading, and a reading or transcription does not hold a click
back: the click is a deliberate decision (Peuqui, 2026-11-04). Only a model that is answering has no
button.

## Way to the browser

The monitor calls its listeners on every change; `api/service_status.py` pushes the snapshot as
kind `service_status` over the [browser push bus](browser-push-bus.md) to all connected pages
(only the newest state stays in the queue). A page fetches the state when its stream opens, over
`GET /api/service-status` (with `?refresh=true` the server reads STT and TTS first). The time left
travels as seconds at the moment of the snapshot; the browser counts down by itself.

The `system_monitor` plugin reads the same SSOT (`system_status` with `model`, `stt`, `tts`); it
pushes nothing. The one `/running` access (`running_models`) is also used by
`tts_llm_coordination`, `backends/base.py` and the chat mixin.
