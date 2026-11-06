"""Streaming text span via useEffect.

Uses textContent assignment (React-safe) instead of el.append() which
creates native Text nodes that React doesn't know about — causing
"removeChild" crashes when React unmounts the component.

The text prop is bound to AIState.current_ai_response which grows
during streaming.
"""

from __future__ import annotations

from reflex.components.el.elements.inline import Span
from reflex.vars.base import Var


class StreamingText(Span):
    """A span that updates text content via useEffect."""

    text: Var[str]

    @classmethod
    def create(cls, *children, **props):
        if "id" not in props:
            raise ValueError("StreamingText requires an explicit 'id' prop")
        return super().create(*children, **props)

    def _exclude_props(self) -> list[str]:
        """Exclude text from DOM attributes — it's only used in useEffect hooks."""
        return ["text"]

    def add_imports(self) -> dict:
        return {"react": ["useEffect", "useRef"]}

    def add_hooks(self) -> list[str | Var]:
        ref = self.get_ref()
        text_js = self.text._js_expr
        return [
            f"const lastLen_{ref} = useRef(0);",
            f"""
useEffect(() => {{
    const text = {text_js};
    const el = {ref}.current;
    if (!el) return;
    if (text.length !== lastLen_{ref}.current) {{
        // Replace double newlines with single to match rendered Markdown spacing
        el.textContent = text.replace(/\\n\\n/g, '\\n');
        lastLen_{ref}.current = text.length;
    }}
}}, [{text_js}]);
""",
        ]


streaming_text = StreamingText.create
