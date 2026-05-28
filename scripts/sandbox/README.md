# AIfred install-script sandbox

Test environment for `scripts/install-all.sh` + `scripts/install-services.sh`
in full isolation from the host system.

Uses `systemd-nspawn --ephemeral`: when the container exits all
changes are gone, the host stays untouched. Even a buggy script
(one that accidentally writes during `--dry-run`) only affects the
container snapshot.

## One-time setup

```bash
sudo ./scripts/sandbox/setup-nspawn-root.sh
```

What happens:
1. `apt install systemd-container debootstrap` (if missing)
2. `debootstrap noble /var/lib/machines/aifred-test/` builds a minimal
   Ubuntu Noble root (~500 MB, ~3 min one-off)
3. Container config: hostname, apt sources with universe, test user
   `aifred` with passwordless sudo

Re-run is idempotent — an existing container root is, on prompt,
deleted and rebuilt.

## Per test

Dry-run (fast, no systemd needed):
```bash
sudo ./scripts/sandbox/run-test.sh --dry-run
sudo ./scripts/sandbox/run-test.sh --dry-run --no-overwrite
```

Full real run (with systemd boot, interactive in v1):
```bash
sudo ./scripts/sandbox/run-test.sh
sudo ./scripts/sandbox/run-test.sh --no-overwrite
```

Interactive shell (debugging):
```bash
sudo ./scripts/sandbox/run-test.sh --shell
```

## What is tested

- Script syntax + logic
- apt install of all system deps
- venv + `pip install -r requirements.txt` (also `insightface` and
  `onnxruntime-gpu` — without a CUDA runtime in the sandbox they
  fall back to the CPU provider)
- Reflex patch, patch-vite-config
- `install-services.sh` path (service-file diff, backup logic)
- Idempotency on a second run

## What is NOT testable here (GPU-only)

- LLM inference
- Calibration (no nvidia-smi in container)
- Vision / Vigilantia with a real camera (V4L2 + CUDA)
- Local TTS engines (XTTS, Fish-Speech need CUDA)

Verify those on the actual hardware.

## v1 limitations

* The full real run mode (with `--boot`) is interactive right now —
  you log in after boot and run the commands manually. Auto-run
  inside a booted nspawn container needs systemd-bus magic; that's
  v2.
* `--dry-run` runs non-interactively and is therefore the preferred
  sanity check before every commit.

## Removing the container

To delete the sandbox root entirely:

```bash
sudo rm -rf /var/lib/machines/aifred-test/
```

Or clean up individual `--ephemeral` snapshots (they should clear
themselves on container exit, but in case they don't):

```bash
sudo machinectl list
sudo machinectl terminate <name>
```
