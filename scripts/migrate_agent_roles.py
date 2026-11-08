#!/usr/bin/env python3
"""Once, at the switch to the Vue interface: the roles of the Reflex time in data/agents.json (main, critic, judge,
custom) become "agent"; "system" stays. Since then who critiques and judges is a seat of the session, not a property
of the agent (lib/debate_seats.py), and lib/agent_config.py refuses any other role.

A copy of the file goes beside it first (agents.json.bak-<date>-<time>); a file without old roles is left alone.

    venv/bin/python scripts/migrate_agent_roles.py [path to agents.json]
"""

from __future__ import annotations

import json
import shutil
import sys
from datetime import datetime
from pathlib import Path

OLD_ROLES = {"main", "critic", "judge", "custom"}
VALID_ROLES = {"agent", "system"}


def migrate(path: Path) -> list[str]:
    """The agents whose role changed; the file is rewritten only when there are any."""
    data = json.loads(path.read_text(encoding="utf-8"))
    agents = data["agents"]
    unknown = {agent: entry["role"] for agent, entry in agents.items() if entry["role"] not in OLD_ROLES | VALID_ROLES}
    if unknown:
        sys.exit(f"Unknown roles, nothing changed: {unknown}")
    changed = [agent for agent, entry in agents.items() if entry["role"] in OLD_ROLES]
    if not changed:
        return []
    backup = path.with_name(f"{path.name}.bak-{datetime.now():%Y%m%d-%H%M%S}")
    shutil.copy2(path, backup)
    for agent in changed:
        agents[agent]["role"] = "agent"
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"Backup: {backup}")
    return changed


if __name__ == "__main__":
    target = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).resolve().parents[1] / "data" / "agents.json"
    done = migrate(target)
    print(f"{target}: {', '.join(done) if done else 'no old roles'}")
