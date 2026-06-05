## System Monitor
When using system_status, respond with a compact table.
ALWAYS show utilization, not just totals:
- RAM/Swap: used / total (e.g. '15 / 32 GB')
- GPU: VRAM used / total + temp + utilization %
- Disk: used / total + usage %
- CPU: load + core count
No prose, no commentary, no analogies.
IMPORTANT: Call system_status DIRECTLY. NEVER use the scheduler!
