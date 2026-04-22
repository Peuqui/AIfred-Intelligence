# System Monitor Plugin

**File:** `aifred/plugins/tools/system_monitor/`

Reports current system hardware status: CPU, RAM, GPU, disk, temperature.

## Tools

| Tool | Description | Tier |
|------|------------|------|
| `system_status` | Query hardware status (CPU, RAM, GPU, disk, temperature) | READONLY |

## Parameter

`components` — Comma-separated: `cpu`, `ram`, `gpu`, `disk`, `temp`, `uptime`, or `all` (default).

## Example Output

- CPU: 16 cores, load 1.28/0.84/0.77
- RAM: 32 GB total, 9 GB used, 22 GB available
- GPUs: 4x (Tesla P40 × 3 + RTX 8000), VRAM, temperature, utilization
- Disk: mount, size, usage percentage
- Sensors: CPU temperature, GPU temperature, NVMe temperature
