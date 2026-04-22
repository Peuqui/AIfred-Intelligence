# Sandbox Plugin

**File:** `aifred/plugins/tools/sandbox/`

Isolated Python code execution in a sandboxed subprocess.

## Tools

| Tool | Description | Tier |
|------|------------|------|
| `execute_code` | Execute Python code (documents read-only) | WRITE_DATA |
| `execute_code_write` | Execute Python code with write access to documents | WRITE_SYSTEM |

## Features

- **Available libraries:** numpy, pandas, matplotlib, plotly, sympy, scipy
- **HTML/JS visualizations:** Generated charts are rendered as inline HTML
- **Timeout protection:** Automatic termination on excessive runtime
- **Isolated subprocess:** Code runs in its own process, no access to AIfred internals
- **Two variants:** `execute_code` mounts `data/documents/` read-only; `execute_code_write` allows writing
