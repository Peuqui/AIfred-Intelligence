# Work Package Outline: RPC-Capable AI Calibration

> **Deutsche Version:** [calibration-rpc.md](../../de/architecture/calibration-rpc.md)

> Outline for extending the AI calibrator (`ai_agent.py`) with
> distributed inference via llama.cpp RPC (Mini + Aragon). Status: planned,
> waiting for the RPC setup to be reactivated (cable + Aragon).
> Context: for RPC basics and benchmarks see
> [showcase-notes.md](../benchmarks/showcase-notes.md) (update 2026-02-28).

## Positioning

RPC operation is a **research setup, not an everyday mode**. Therefore:

- RPC profiles stay excluded from the llama-swap autoscan
  (`scripts/llama-swap-autoscan.py` deliberately skips `--rpc` profiles).
- The deterministic algorithm (`flow.py`) is NOT made RPC-capable —
  it requires exact per-GPU telemetry via nvidia-smi, which doesn't exist for
  remote cards.
- Instead: the **AI calibrator** takes over RPC profiles. Its
  trial-and-error loop with OOM recovery structurally copes with the uncertainty
  "remote VRAM not directly measurable".

## Verified current state (as of 2026-08-06)

| Component | Status |
|----------|--------|
| `--rpc` is passed through to fit-params | ✅ present (`_GPU_FLAGS` in `projection.py`) |
| fit-params plans the RPC device correctly | ✅ tested live: output line `RPC0 <model> <ctx> <compute>` |
| Parser `_parse_fit_output` | ❌ regex `^CUDA(\d+)` silently discards `RPC0` lines |
| GPU enumeration (`gpu.py`) | ❌ local nvidia-smi only, the remote card has no budget entry |
| RPC connectivity precheck | ✅ present in the backend (`backends/llamacpp.py`, regex on cmd) |
| llama-server `--rpc` flag | ✅ both builds built with `GGML_RPC=ON` (2026-08-06) |
| Worker binary | ✅ now called `ggml-rpc-server` (renamed, old docs say `rpc-server`) |

## Design decision: detection instead of toggle

**No new config switch.** The SSOT for "this profile uses RPC" is
the `--rpc host:port` in the cmd of the llama-swap profile. The calibrator
detects the flag (same regex as `backends/llamacpp.py` —
extract as a shared helper). The user "switch" is the
profile choice itself (two-profile pattern: `Model` vs. `Model-rpc`).

## Work package (5 sub-steps)

1. **Extend the parser** (`projection.py::_parse_fit_output`):
   match `(CUDA|RPC)(\d+)` lines, carry the device type along.
   Smallest change, biggest lever — fit-params already computes correctly.
2. **`--rpc` detection + precheck** at the calibration entry point:
   TCP check on all endpoints BEFORE the first estimate (reuse the helper from
   the backend). Aragon off → clean abort with a clear
   message instead of a cryptic fit-params error.
3. **Hardware block in the system prompt** (`ai_agent.py::_hardware_block`):
   add a synthetic entry for the RPC device:
   total VRAM (reported by the rpc-server on connect, taken from the
   fit-params device list — not configured),
   free VRAM "not measurable", OOM "only shows up as a probe failure",
   note "network-bound — when in doubt, fill last".
4. **Split length**: extend the `tensor_split` validation to
   n_local_GPUs + n_RPC_devices.
5. **Probe feedback**: "unknown" instead of free MB for remote devices.
   Later upgrade (optional): parse per-device allocations from the
   llama-server load log.

## Constraints

- Aragon must be running during calibration (fit-params really connects
  to the rpc-server — verified).
- Aragon side: llama.cpp with `-DGGML_CUDA=ON -DGGML_RPC=ON` (arch 86),
  `ggml-rpc-server -H 0.0.0.0 -p 50052`, static IP 10.0.0.2/30 on the
  USB4 adapter. Checklist see showcase-notes.md §Einrichtung (setup)
  (binary name outdated there).
- Mini side: NetworkManager profile `rpc-direct` (enp4s0, 10.0.0.1/30)
  already exists.
- The calibration LLM comes from the agent editor as before
  (system agent "Calibration" in `data/agents.json`) — no coupling
  to this work package.

## Open policy questions (clarify before implementation)

1. **Fill order**: The 3090 Ti is the fastest card in the cluster
   (compute 8.6), but sits behind the network. Following speed-class logic
   it would be filled first — last probably makes more sense.
   Decide with measurements from real operation.
2. **Remote safety margin**: assumption "worker is dedicated, card empty"
   plus which buffer? (Windows/desktop on Aragon occupies VRAM.)
3. **Verifier**: accept the remote card as "unverifiable" or
   include load-log parsing (sub-step 5, upgrade) right away?
