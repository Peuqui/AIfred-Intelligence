# System Monitor Plugin

**File:** `aifred/plugins/tools/system_monitor/`

Reports current system hardware status: CPU load, RAM/swap, GPU VRAM and
temperature, disk space, uptime and sensor temperatures. Read-only — it only
runs query commands (`uptime`, `free`, `df`, `nvidia-smi`, `sensors`) and never
changes anything.

## Tools

| Tool | Description | Tier |
|------|------------|------|
| `system_status` | Query hardware status (CPU, RAM, GPU, disk, temperature, uptime) | READONLY |

## Parameter

`components` — Comma-separated list selecting what to query: `cpu`, `ram`
(alias `memory`), `gpu`, `disk`, `temp`, `uptime`, or `all` (default).

## What each component returns

- **cpu / uptime** — uptime string, core count, 1/5/15-minute load average
- **ram / memory** — RAM total/used/free/available plus swap (from `free -h --si`)
- **gpu** — per-GPU index, name, VRAM total/used/free (MB), temperature, utilization
  (from `nvidia-smi`; reports an error if `nvidia-smi` is absent)
- **disk** — usage for the `/` and `/home` mounts (size, used, available, percent)
- **temp** — key sensor input temperatures (from `sensors -j`; silently skipped
  if `sensors` is not installed)

## Example usage

- "How much VRAM is free?" → `system_status(components="gpu")`
- "Show CPU and RAM" → `system_status(components="cpu,ram")`
- "Full system status" → `system_status(components="all")`

Output is returned as JSON; the agent is instructed to render it as a compact
table showing utilization (used / total), not just totals.
