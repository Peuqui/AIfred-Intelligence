"""The agents' quick choices for the Vue app (lib/agent_choices.py): per agent model, 🎩 persona,
💭 reasoning, 🧠 thinking and ⚡ speed, the Automatik's model and the Vision agent's. Global
settings (settings.json), no choice of one session; a turn reads them when it starts.
"""


from pydantic import BaseModel, ConfigDict, Field

from .. import agent_choices as rules
from ..agent_choices import QuickChoices
from ..agent_settings import get_agent_setting, set_agent_setting
from ..runtime_settings import RuntimeSettings, load_runtime_settings
from ..user_error import Invalid, Missing, NotDone
from .app import api_app

# The Automatik is no agent: its row has a model only, AIfred's when it has none.
AUTOMATIK = "automatik"


class ChoicesChange(BaseModel):
    """A field this endpoint does not know is refused (422), not ignored."""

    model_config = ConfigDict(extra="forbid")

    model_id: str | None = Field(None, description='The model; "" runs the agent on AIfred\'s (not for AIfred and Vision)')
    personality: bool | None = None
    reasoning: bool | None = None
    thinking: str | None = Field(None, description="A value of the agent's thinking_options")
    speed: bool | None = Field(None, description="The -speed variant (only where speed_available)")


async def catalog_of(view: RuntimeSettings) -> dict[str, str]:
    """The backend's models (lib/backend_switch.py), without starting a backend that does not answer."""
    from ..backend_switch import model_catalog
    from ..settings import persisted_settings

    catalog, _notes = await model_catalog(view.backend_type, persisted_settings()["cloud_api_provider"])
    return catalog


@api_app.get("/agents/choices", response_model=QuickChoices, tags=["Agents"])
async def agents_choices() -> QuickChoices:
    view = load_runtime_settings()
    return await rules.quick_choices(view, await catalog_of(view), view.ui_language)


@api_app.patch("/agents/{agent}/choices", response_model=QuickChoices, tags=["Agents"])
async def agents_change_choices(agent: str, change: ChoicesChange) -> QuickChoices:
    """Change what the request names, in this order: the model (it decides which thinking options and
    whether a speed variant exist), then the switches; settings.json gets what changed. Answers
    with every row again (a model change of AIfred moves the agents that run on his)."""
    if agent not in {row_agent for row_agent, _, _ in rules.chat_agents()} | {"vision", AUTOMATIK}:
        raise Missing("unknown_agent", agent=agent)
    backend = load_runtime_settings()
    catalog = await catalog_of(backend)
    offered = catalog
    if agent == "vision" and change.model_id is not None:
        offered = await rules.vision_candidates(backend, catalog, backend.backend_url)

    # From here no await until settings.json is written: two changes never interleave.
    view = load_runtime_settings()
    touched = _apply(view, agent, change, offered)
    if not rules.save_choices(view, touched):
        raise NotDone("settings_not_saved")
    return await rules.quick_choices(view, catalog, view.ui_language)


def _apply(view: RuntimeSettings, agent: str, change: ChoicesChange, offered: dict[str, str]) -> list[str]:
    """The change on ``view`` (``offered``: the models the row can choose); returns the agents whose
    tuning changed. 422 for a value the row does not offer."""
    if agent == AUTOMATIK:
        if change.model_dump(exclude_none=True).keys() - {"model_id"}:
            raise Invalid("automatik_model_only")
        if change.model_id is not None:
            if change.model_id and change.model_id not in offered:
                raise Invalid("no_model_choice", model=change.model_id)
            view.automatik_model_id = change.model_id
        return []

    touched = {agent}
    if change.model_id is not None:
        own_model_required = agent in ("aifred", "vision")
        if change.model_id not in offered and (change.model_id or own_model_required):
            raise Invalid("no_model_choice_for", model=change.model_id, agent=agent)
        touched.update(rules.choose_model(view, agent, change.model_id, offered.get(change.model_id, "")))
    for field in ("personality", "reasoning"):
        value = getattr(change, field)
        if value is not None:
            set_agent_setting(view, agent, field, value)
    if change.thinking is not None:
        _, options = rules.thinking_choices(view, agent, view.ui_language)
        if change.thinking not in {option.value for option in options}:
            raise Invalid("no_thinking_choice", agent=agent, value=change.thinking)
        rules.choose_thinking(view, agent, change.thinking)
    if change.speed is not None:
        if change.speed and not get_agent_setting(view, agent, "has_speed_variant"):
            raise Invalid("no_speed_variant", agent=agent)
        set_agent_setting(view, agent, "speed_mode", change.speed)
    return sorted(touched)
