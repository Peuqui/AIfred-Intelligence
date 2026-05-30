# Vision Plugin (Vigilantia)

**File:** `aifred/plugins/tools/vision/`

The vision plugin is AIfred's eyes. It lets the assistant access connected
webcams and other image sources, take snapshots, have a VLM describe what it
sees, recognise enrolled faces, and run a continuous background watch that
records motion and face events into a persistent chronicle.

The plugin itself is the thin LLM-facing glue. The heavy lifting lives in the
shared libraries under `aifred/lib/` — `frame_sources` (capture),
`vision_filters/motion` (motion detection), `vision_filters/face_detect` +
`face_recognize` (InsightFace), `vision_analyzer` (the VLM call), `vision_watcher`
(the background watch task), and `vision_store` (the SQLite database).

A key design point: **VLM calls go through Ollama as a side-channel**, fully
independent of the active chat backend on llama-swap. A snapshot analysis never
swaps out the running chat model.

## Tools

The tools are only presented to the LLM when vision is enabled (see *Vision
mode* below). When `vision_mode` is `off`, `get_tools` returns an empty list and
the assistant does not see the plugin at all.

| Tool | Description | Tier |
|------|-------------|------|
| `vision_list_sources` | List all registered image sources (webcams, IP cameras) with availability, resolution and configured context. | READONLY |
| `vision_rescan_sources` | Re-scan the system for newly attached/detached image sources (e.g. a webcam that was just plugged in). | READONLY |
| `vision_snapshot` | Capture a single frame from a source. With `save=true` (default) the image is persisted to the current session and an `image_url` + `markdown` shortcut is returned for inline rendering. | READONLY |
| `vision_analyze` | Capture N frames (1–10) and have the VLM describe them. `n_frames > 1` sends a temporal sequence (motion description). Logs a `vlm_analysis` event. | READONLY |
| `vision_enroll_face` | Snapshot the source, detect the most prominent face, and store its embedding under `name`. Repeating with an existing name appends another embedding (multi-angle enrollment). | WRITE_DATA |
| `vision_start_watch` | Start a continuous background watch on a source: captures frames at `fps`, detects motion, and (if enabled) runs face recognition. Events are logged to the vision DB. | WRITE_DATA |
| `vision_stop_watch` | Stop a running watch on a source. No-op if nothing was running. | WRITE_DATA |
| `vision_list_active_watches` | List currently-running watch tasks with per-watch counters (frames seen, motion events, face events). | READONLY |
| `vision_query_events` | Query past events (`motion` / `face_known` / `face_unsure` / `face_unknown` / `vlm_analysis`), filterable by source, event type, and time window. | READONLY |

### Tool parameters

- **`vision_snapshot`** — `source_id` (required), `save` (bool, default `true`).
- **`vision_analyze`** — `source_id` (required), `prompt` (optional; falls back
  to the configured default prompt), `n_frames` (int, 1–10, default `1`).
- **`vision_enroll_face`** — `name` (required), `source_id` (required), `notes`
  (optional).
- **`vision_start_watch`** — `source_id` (required), `fps` (optional; default
  from settings), `run_face_detect` (optional; default from settings).
- **`vision_stop_watch`** — `source_id` (required).
- **`vision_query_events`** — `source_id`, `event_type`, `since_hours`, `limit`
  (default `50`, capped at `500`) — all optional.

Per-camera resolution and a static "briefing" (prompt context) are read from
`vision_store` for each call, set via the live-preview popup in the UI. The
briefing is prepended to the analyze prompt so the VLM sees the static context
("entrance, door with mailbox") before the variable instruction.

## Snapshot vs. Analyze vs. Watch

There are three ways the plugin uses a camera:

1. **Snapshot** — grab one frame, optionally save it to the session. No model
   inference. Fast.
2. **Analyze** — grab 1–10 frames and run the VLM. Returns a text description
   plus VLM stats (TTFT / inference / tokens-per-second), which the chat bubble
   renders as a collapsible `<vlm_output>` with a metrics footer. The captured
   frame is pinned into the response so you see what the VLM saw. Every analyze
   call is logged as a `vlm_analysis` event.
3. **Watch (Vigilantia armed)** — a continuous background task. See below.

## Motion detection

Motion detection uses OpenCV's `BackgroundSubtractorMOG2` (Mixture of
Gaussians), one stateful detector per source. Each frame is reduced to grayscale
with a light Gaussian blur to dampen JPEG quantisation noise, then the
foreground mask gives a motion `area_ratio` (fraction of changed pixels) and the
largest contour's bounding box. `motion_min_area_ratio` filters out micro-noise
(wind in a tree, compression artefacts), and `motion_warmup_frames` ignores the
first frames while the background model stabilises. CPU only, ~5–15 ms per frame
at 640×480.

## Watch mode (Vigilantia armed)

`vision_start_watch` launches a background task that captures frames at the
configured `fps`, runs motion detection, and — when `run_face_detect_on_motion`
is set — runs face detection and recognition on motion events. Events flow into
the vision database:

- **`motion`** — motion above the area threshold (carries `area_ratio` + bbox).
- **`face_known` / `face_unsure` / `face_unknown`** — face detected and matched
  (or not) against enrolled persons.
- **`vlm_analysis`** — when continuous/on-motion VLM is enabled, or from a
  manual `vision_analyze` call.

`min_event_interval_sec` debounces events so a single passer-by does not flood
the log. Event frames are saved to disk when `save_event_frames` is true, so the
chronicle entries carry a thumbnail.

## Face recognition (InsightFace)

Detection and embedding run via **InsightFace `buffalo_l`** (RetinaFace
detection + ArcFace embedding in one pass). On first use, InsightFace downloads
the model (~280 MB) to `~/.insightface/models/buffalo_l/`; initialisation is
lazy so the module import stays cheap.

Each detected face yields a 512-dim L2-normalised embedding. Matching is cosine
similarity (a dot product on the normalised vectors), bulk-vectorised with NumPy.
A person can have **multiple embeddings** (different angles / lighting) — the
recogniser max-pools, so the highest similarity of *any* embedding for a person
counts. Two thresholds define three bands:

- `similarity >= threshold_known` → **known**
- `threshold_unsure <= similarity < threshold_known` → **unsure** (best
  candidate named, but not certain)
- below `threshold_unsure` → **unknown**

The "unsure" band exists deliberately so a doorkeeper workflow can treat
ambiguity as "unknown" rather than a false positive.

## Personarium (identity management)

Enrolled people live in the `faces` table of the vision store. The **Personarium**
UI modal lists every identity with an avatar (last crop), embedding count and
last-seen timestamp, and lets you rename a person, delete one, or remove
individual embeddings. Reads and writes go directly through `VisionStore`; the
REST endpoints under `/api/vision/face/*` are for external consumers.

`vision_enroll_face` is the LLM-facing single-shot path. Calling it again with an
existing name appends another embedding to the same person — iterative
enrollment is the intended workflow.

## Multi-Pose enrollment

For robust recognition under head movement, the **Multi-Pose** UI wizard guides
the user through several poses (frontal / left / right / up / down). Each pose is
captured individually — instruction → live snapshot → face detect → embedding
into the capture list — and all embeddings are written as one sample bundle at
the end. It can create a new person or add more poses to an existing identity
(launched from Personarium). The pose info is purely instructional; embeddings
themselves are pose-agnostic and the pose label is not stored in the database.

## Casus (event chronicle)

**Casus** is the event-management UI modal: a chronological list of all vision
events (`motion` / `face_known` / `face_unsure` / `face_unknown` / `vlm_analysis`)
with filters (source, type, identity) and per-row actions — delete an event, or
assign an unknown face to a person after the fact. It reads and writes the
`VisionStore` directly. The same data is exposed to the LLM through
`vision_query_events`, so the assistant can answer questions like "what happened
at the door today?" or "who was here last?".

A live **Vigilantia feed** card on the main page shows the last N events across
all sources.

## Model lifecycle

Two models back this plugin, both loaded on demand:

- **InsightFace `buffalo_l`** — face detection + embedding. Auto-downloaded to
  `~/.insightface/models/buffalo_l/` on first use (~280 MB). Lazy init, one
  instance per process. Provider and GPU are configurable; on a GPU-poor host it
  falls back to `CPUExecutionProvider`.
- **VLM via Ollama** — image description. Default in code is `qwen2.5vl:7b-q8_0`;
  the shipped `settings.json` overrides this to `qwen3-vl:4b-instruct-q8_0`.
  Multi-image inputs are sent as a temporal sequence. The model is held in VRAM
  via `keep_alive` (default `30m`); in `live` mode `keep_alive` is forced to `-1`
  so the model stays permanently loaded for always-on surveillance.

GPU placement is automatic (`gpu_id: "auto"`). The chat LLM owns the fastest
compute class; the VLM and InsightFace co-locate on the *side-channel tier* (the
class below), with a soft floor of compute capability ≥ 7.0 (Volta+) so a slow
Pascal card is only used as a last resort. See `aifred/lib/vision_gpu_select.py`.

## Vision mode

A global toggle in `settings.json` (`vision_mode`) controls the whole subsystem:

- **`off`** — vision disabled; the plugin presents no tools and the LLM never
  sees it. No VRAM reservation during calibration, no watch tasks accepted.
- **`on-demand`** (default) — snapshot/analyze run on demand; watch tasks need an
  explicit `vision_start_watch`. The VLM is held with `keep_alive` (typically
  30 min).
- **`live`** — like on-demand, plus the VLM is kept permanently loaded
  (`keep_alive=-1`) for always-on / doorkeeper surveillance.

## Configuration

All settings live in `aifred/plugins/tools/vision/settings.json` and are loaded
fresh on every call, so you can tune them from the plugin manager without
restarting. Key groups:

- **`vlm`** — `model`, `num_ctx`, `keep_alive`, `host`, `default_prompt`.
- **`face_recognition`** — `providers`, `gpu_id` (int / `"auto"` / `null` for
  CPU-only), `det_size`, `model_name` (`buffalo_l`), `threshold_known`,
  `threshold_unsure`.
- **`watch`** — `default_fps`, the motion thresholds (`motion_min_area_ratio`,
  `motion_history_frames`, `motion_var_threshold`, `motion_warmup_frames`),
  `min_event_interval_sec`, `save_event_frames`, `run_face_detect_on_motion`,
  `run_vlm_on_motion`.
- **`snapshot`** — `jpeg_quality`, `save_to_disk`, `retention_days`.
- **`events`** — per-type retention (`retention_days_motion` / `_face` / `_vlm`)
  and `default_query_limit`.

Ollama must be reachable (default `http://localhost:11434`) and the configured
VLM must be pulled. InsightFace requires `insightface` + `onnxruntime` (GPU or
CPU) installed.

## Usage examples

- "Take a photo of the front door." → `vision_snapshot`
- "What do you see on the webcam right now?" → `vision_analyze`
- "Remember this person as Alex." → `vision_enroll_face` (repeat for more angles)
- "Watch the entrance and tell me if someone shows up." → `vision_start_watch`
- "Who was at the door today?" → `vision_query_events`
