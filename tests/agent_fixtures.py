"""The agents the tests run with, as raw entries of an agents.json (the application has no built-in agents: its
agents.json is checked in, see agent_config.check_required_agents). Each call returns a fresh copy a test may change.
Not the repository's data/agents.json: the tests must not depend on what Peuqui configured there."""

from __future__ import annotations


def raw_test_agents() -> dict[str, dict]:
    """A fresh copy of the test agents (aifred, sokrates, salomo, vision) as raw entries of agents.json."""
    return {
        "aifred": {
            "display_name": "AIfred",
            "aliases": ["alfred", "ai fred", "eifred", "ai-fred"],
            "emoji": "\U0001f3a9",
            "description": "Gentleman-Berater und KI-Butler",
            "role": "agent",
            "color": "#e67700",
            "tools": [],
            "prompts": {
                "identity": "aifred/identity.txt",
                "personality": "aifred/personality.txt",
                "reminder": "aifred/reminder.txt",
            },
            "toggles": {
                "personality": True,
                "reasoning": False,
                "thinking": True,
            },
        },
        "sokrates": {
            "display_name": "Sokrates",
            "aliases": ["socrates"],
            "emoji": "\U0001f3db\ufe0f",
            "description": "Scharfsinniger Philosoph und Kritiker",
            "role": "agent",
            "color": "#cd7f32",
            "tools": [],
            "prompts": {
                "identity": "sokrates/identity.txt",
                "personality": "sokrates/personality.txt",
                "reminder": "sokrates/reminder.txt",
            },
            "toggles": {
                "personality": True,
                "reasoning": False,
                "thinking": True,
            },
        },
        "salomo": {
            "display_name": "Salomo",
            "aliases": ["salomon", "solomon"],
            "emoji": "\U0001f451",
            "description": "Weiser Richter und Synthesist",
            "role": "agent",
            "color": "#daa520",
            "tools": [],
            "prompts": {
                "identity": "salomo/identity.txt",
                "personality": "salomo/personality.txt",
                "reminder": "salomo/reminder.txt",
            },
            "toggles": {
                "personality": True,
                "reasoning": False,
                "thinking": True,
            },
        },
        "vision": {
            "display_name": "Vision",
            "emoji": "\U0001f4f7",
            "description": "Vision Language Model fuer Bildanalyse",
            "color": "#56d4dd",
            "role": "system",   # backs the image workflow, no chat partner (as every agents.json has it)
            "tools": [],           # it runs without a tool loop
            # What the image model reads (it never speaks as an agent): the prompt editor shows these.
            "prompts": {
                "event_single": "vision/vision_event_single.txt",
                "event_sequence": "vision/vision_event_sequence.txt",
                "event_summary": "vision/vision_event_summary.txt",
                "continuous": "vision/vision_continuous.txt",
                "ir_context": "vision/vision_ir_context.txt",
                "stamp_context": "vision/vision_stamp_context.txt",
                "identity_context": "vision/vision_identity_context.txt",
                "headcount_context": "vision/vision_headcount_context.txt",
                "default_question": "vision/default_question.txt",
                "task_adaptive": "vision/task_adaptive.txt",
                "task_instruction_default": "vision/task_instruction_default.txt",
                "task_instruction_question": "vision/task_instruction_question.txt",
                "task_instruction_handoff": "vision/task_instruction_handoff.txt",
                "task_instruction_handoff_question": "vision/task_instruction_handoff_question.txt",
                "sandbox_screenshot": "vision/sandbox_screenshot.txt",
                "sandbox_screenshot_focus": "vision/sandbox_screenshot_focus.txt",
                "sandbox_screenshot_sequence": "vision/sandbox_screenshot_sequence.txt",
            },
            "toggles": {
                "personality": True,
                "reasoning": False,
                "thinking": False,
            },
        },
    }
