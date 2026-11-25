"""The tuning buckets of a runtime view as the default agents give them: the permanent ones (AIfred, Vision) and one
for each other agent of the built-in agents.json (runtime_settings.registered_agent_tuning builds them from the file;
a test's view takes them from the built-in defaults, not from the repository's data/)."""

from __future__ import annotations

from agent_fixtures import raw_test_agents
from aifred.lib.agent_tuning import AgentTuning, default_agent_tuning, default_tuning


def default_agents_tuning() -> dict[str, AgentTuning]:
    tuning = default_agent_tuning()
    for agent in raw_test_agents():
        tuning.setdefault(agent, default_tuning(agent))
    return tuning
