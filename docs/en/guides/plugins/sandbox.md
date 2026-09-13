# Sandbox Plugin

**File:** `aifred/plugins/tools/sandbox/`

Isolated Python code execution in a bubblewrap-sandboxed subprocess — for
calculations, data analysis, simulations and (interactive) visualizations.

## Tools

| Tool | Description | Tier |
|------|------------|------|
| `execute_code` | Run Python code; `data/documents/` mounted **read-only** | WRITE_DATA |
| `execute_code_write` | Run Python code with **write access** to `data/documents/` | WRITE_SYSTEM |

Both tools share the same parameters and run the identical sandbox — they only
differ in whether the documents directory is writable. The function-calling
pipeline filters by tier, so low-tier contexts only ever see `execute_code`.

### Parameters

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `code` | string | yes | Python code to execute |
| `description` | string | no | Brief description of what the code does (for logging / UI status) |

## Sandboxing

Code runs in a subprocess wrapped by **bubblewrap (`bwrap`)** with
`--unshare-all` and `--new-session`:

- **No network access** — the network namespace is unshared
- **No filesystem access** beyond `/usr`, `/etc` (read-only), a private `/tmp`,
  the venv interpreter + site-packages (read-only), and the per-run work dir
- **Resource limits:** RAM via `RLIMIT_AS` (default 2048 MB), CPU time and a wall-clock
  **timeout of 30 seconds** (`RLIMIT_CPU` + `asyncio.wait_for`); core dumps disabled
- **Inside the sandbox:** the user's documents appear under the relative path
  `documents/` (read-only for `execute_code`, read-write for `execute_code_write`)

If `bwrap` is not installed, execution is refused (no fallback). Install it with
`sudo apt install bubblewrap`.

## Output handling

- **stdout / stderr** are returned to the model (truncated at ~1 MB each). Always
  `print()` results you want back.
- **matplotlib plots** are auto-captured (`MPLBACKEND=Agg`) and embedded in the chat as images.
- **Interactive HTML/JS**: every `.html` file in the working directory (e.g. plotly
  `fig.write_html("chart.html", include_plotlyjs=True)`) is detected and embedded
  as a collapsed iframe with an "open in browser" link, with its source code
  collapsed below. The model is told to write one page into exactly one
  meaningfully named file.
- For `execute_code_write`, HTML/image artifacts written into `documents/` during
  the run are also surfaced in the chat.

Output files are stored per session under `data/sandbox_output/{session_id}/` and
cleaned up with the session. Their name is `<name>-<checksum>`, e.g.
`fibonacci-15f0ff.html` (plots `plot-…`, screenshots `shot-…`): a corrected
version gets a new checksum, a common name such as `index` does not overwrite
another page, and the same page under two names appears once in the chat. Plots and screenshots are collapsed as well.

## Available libraries

`math`, `statistics`, `numpy`, `pandas`, `matplotlib`, `scipy`, `sklearn`,
`seaborn`, `plotly`.

## Configuration

Defaults in `aifred/lib/config.py`:

- `SANDBOX_TIMEOUT_SECONDS` = 30
- `SANDBOX_MAX_RAM_MB` = 2048
- `SANDBOX_MAX_OUTPUT_BYTES` = 1_000_000
- `SANDBOX_WORK_DIR` = `/tmp/aifred_sandbox`
- `SANDBOX_OUTPUT_DIR` = `data/sandbox_output/`
