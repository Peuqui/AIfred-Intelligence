"""System Monitor Plugin — CPU, RAM, GPU, Disk, Uptime."""

import json
import subprocess
from dataclasses import dataclass
from typing import Any

from ....lib.function_calling import Tool
from ....lib.security import TIER_READONLY
from ....lib.plugin_base import PluginContext
from ....lib.logging_utils import log_message


@dataclass
class SystemMonitorPlugin:
    name: str = "system_monitor"
    display_name: str = "System Monitor"
    description: str = "Liest Systemzustand: CPU, RAM, GPU-VRAM, Datenträger, Netzwerk, laufende Prozesse."

    def is_available(self) -> bool:
        return True

    def get_tools(self, ctx: PluginContext) -> list[Tool]:
        tools: list[Tool] = []

        async def _system_status(components: str = "all") -> str:
            """Get system status: CPU, RAM, GPU, Disk, Uptime."""
            result: dict[str, Any] = {}
            parts = [c.strip().lower() for c in components.split(",")]
            check_all = "all" in parts

            # Uptime + Load
            if check_all or "cpu" in parts or "uptime" in parts:
                try:
                    uptime_out = subprocess.check_output(
                        ["uptime", "-p"], text=True, timeout=5
                    ).strip()
                    load_out = subprocess.check_output(
                        ["cat", "/proc/loadavg"], text=True, timeout=5
                    ).strip()
                    loads = load_out.split()
                    import os
                    result["uptime"] = uptime_out
                    result["cpu"] = {
                        "cores": os.cpu_count(),
                        "load_1m": loads[0],
                        "load_5m": loads[1],
                        "load_15m": loads[2],
                    }
                except Exception as e:
                    result["cpu"] = {"error": str(e)}

            # RAM
            if check_all or "ram" in parts or "memory" in parts:
                try:
                    mem_out = subprocess.check_output(
                        ["free", "-h", "--si"], text=True, timeout=5
                    ).strip()
                    lines = mem_out.split("\n")
                    if len(lines) >= 2:
                        values = lines[1].split()
                        result["ram"] = {
                            "total": values[1],
                            "used": values[2],
                            "free": values[3],
                            "available": values[6] if len(values) > 6 else "",
                        }
                    if len(lines) >= 3:
                        swap = lines[2].split()
                        result["swap"] = {
                            "total": swap[1],
                            "used": swap[2],
                            "free": swap[3],
                        }
                except Exception as e:
                    result["ram"] = {"error": str(e)}

            # GPU (nvidia-smi)
            if check_all or "gpu" in parts:
                try:
                    gpu_out = subprocess.check_output(
                        ["nvidia-smi", "--query-gpu=index,name,memory.total,memory.used,memory.free,temperature.gpu,utilization.gpu",
                         "--format=csv,noheader,nounits"],
                        text=True, timeout=10
                    ).strip()
                    gpus = []
                    for line in gpu_out.split("\n"):
                        parts_gpu = [p.strip() for p in line.split(",")]
                        if len(parts_gpu) >= 7:
                            gpus.append({
                                "index": parts_gpu[0],
                                "name": parts_gpu[1],
                                "vram_total_mb": parts_gpu[2],
                                "vram_used_mb": parts_gpu[3],
                                "vram_free_mb": parts_gpu[4],
                                "temp_c": parts_gpu[5],
                                "utilization_pct": parts_gpu[6],
                            })
                    result["gpus"] = gpus
                except FileNotFoundError:
                    result["gpus"] = {"error": "nvidia-smi not found"}
                except Exception as e:
                    result["gpus"] = {"error": str(e)}

            # Disk
            if check_all or "disk" in parts:
                try:
                    disk_out = subprocess.check_output(
                        ["df", "-h", "--output=target,size,used,avail,pcent", "/", "/home"],
                        text=True, timeout=5
                    ).strip()
                    disks = []
                    for line in disk_out.split("\n")[1:]:
                        cols = line.split()
                        if len(cols) >= 5:
                            disks.append({
                                "mount": cols[0],
                                "size": cols[1],
                                "used": cols[2],
                                "available": cols[3],
                                "usage_pct": cols[4],
                            })
                    result["disks"] = disks
                except Exception as e:
                    result["disks"] = {"error": str(e)}

            # Temperatures (optional) — extract key values only
            if check_all or "temp" in parts:
                try:
                    sensors_out = subprocess.check_output(
                        ["sensors", "-j"], text=True, timeout=5
                    )
                    import json as _json
                    raw = _json.loads(sensors_out)
                    temps: dict[str, str] = {}
                    for chip, data in raw.items():
                        if not isinstance(data, dict):
                            continue
                        for label, values in data.items():
                            if not isinstance(values, dict):
                                continue
                            for key, val in values.items():
                                if "input" in key and isinstance(val, (int, float)) and val > 0:
                                    temps[f"{chip}/{label}"] = f"{val:.0f}°C"
                    if temps:
                        result["temps"] = temps
                except (FileNotFoundError, subprocess.CalledProcessError):
                    pass
                except Exception:
                    pass

            log_message(f"📊 system_status: {list(result.keys())}")
            return json.dumps(result, ensure_ascii=False)

        tools.append(Tool(
            name="system_status",
            tier=TIER_READONLY,
            description=(
                "Get system hardware status: CPU load, RAM usage, GPU VRAM and temperature, "
                "disk space, uptime. Use components parameter to query specific parts "
                "(e.g. 'gpu', 'ram,cpu', 'disk') or 'all' for everything."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "components": {
                        "type": "string",
                        "description": "Comma-separated: cpu, ram, gpu, disk, temp, uptime, or 'all'",
                        "default": "all",
                    },
                },
            },
            executor=_system_status,
        ))

        return tools

    def get_prompt_instructions(self, lang: str) -> str:
        if lang == "de":
            return (
                "## System Monitor\n"
                "Wenn du system_status aufrufst, antworte mit einer kompakten Tabelle.\n"
                "Zeige IMMER Auslastung/Belegung, nicht nur Gesamtgroessen:\n"
                "- RAM/Swap: belegt / gesamt (z.B. '15 / 32 GB')\n"
                "- GPU: VRAM belegt / gesamt + Temperatur + Auslastung %\n"
                "- Disk: belegt / gesamt + Auslastung %\n"
                "- CPU: Load + Anzahl Cores\n"
                "Kein Fliesstext, keine Kommentare, keine Analogien.\n"
                "WICHTIG: Rufe system_status DIREKT auf. NIEMALS ueber den Scheduler!"
            )
        return (
            "## System Monitor\n"
            "When using system_status, respond with a compact table.\n"
            "ALWAYS show utilization, not just totals:\n"
            "- RAM/Swap: used / total (e.g. '15 / 32 GB')\n"
            "- GPU: VRAM used / total + temp + utilization %\n"
            "- Disk: used / total + usage %\n"
            "- CPU: load + core count\n"
            "No prose, no commentary, no analogies.\n"
            "IMPORTANT: Call system_status DIRECTLY. NEVER use the scheduler!"
        )

    def get_ui_status(self, tool_name: str, tool_args: dict[str, Any], lang: str) -> str:
        return "📊 System Status"


plugin = SystemMonitorPlugin()
