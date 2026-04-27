"""Calculator plugin — safe mathematical expression evaluation."""

import ast
import json
import operator
from dataclasses import dataclass
from typing import Any, Callable

from ....lib.function_calling import Tool
from ....lib.security import TIER_READONLY
from ....lib.plugin_base import PluginContext


@dataclass
class CalculatorPlugin:
    name: str = "calculator"
    display_name: str = "Calculator"
    description: str = "Mathematische Berechnungen — von Grundrechenarten bis zu komplexen Ausdrücken (sympy-basiert)."

    def is_available(self) -> bool:
        return True

    def get_tools(self, ctx: PluginContext) -> list[Tool]:

        async def _execute(expression: str) -> str:
            """Evaluate a math expression safely using AST parsing."""
            from ....lib.logging_utils import log_message

            binary_ops: dict[type, Callable[[float, float], float]] = {
                ast.Add: operator.add,
                ast.Sub: operator.sub,
                ast.Mult: operator.mul,
                ast.Div: operator.truediv,
                ast.FloorDiv: operator.floordiv,
                ast.Mod: operator.mod,
                ast.Pow: operator.pow,
            }
            unary_ops: dict[type, Callable[[float], float]] = {
                ast.USub: operator.neg,
                ast.UAdd: operator.pos,
            }

            def _eval(node: ast.AST) -> float:
                if isinstance(node, ast.Expression):
                    return _eval(node.body)
                elif isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
                    return float(node.value)
                elif isinstance(node, ast.BinOp):
                    op_type = type(node.op)
                    if op_type not in binary_ops:
                        raise ValueError(f"Unsupported operator: {op_type.__name__}")
                    return binary_ops[op_type](_eval(node.left), _eval(node.right))
                elif isinstance(node, ast.UnaryOp):
                    uop_type = type(node.op)
                    if uop_type not in unary_ops:
                        raise ValueError(f"Unsupported operator: {uop_type.__name__}")
                    return unary_ops[uop_type](_eval(node.operand))
                else:
                    raise ValueError(f"Unsupported expression: {ast.dump(node)}")

            log_message(f"🔢 calculate: {expression}")
            try:
                tree = ast.parse(expression, mode='eval')
                result = _eval(tree)
                if result == int(result):
                    result_str = str(int(result))
                else:
                    result_str = f"{result:.10g}"
                log_message(f"✅ calculate: {expression} = {result_str}")
                return f"{expression} = {result_str}"
            except Exception as e:
                log_message(f"❌ calculate failed: {e}")
                return json.dumps({"error": f"Cannot evaluate '{expression}': {e}"})

        return [
            Tool(
                name="calculate",
                tier=TIER_READONLY,
                description=(
                    "Evaluate a mathematical expression and return the exact result. "
                    "Use this for any calculation instead of doing mental math. "
                    "Supports: +, -, *, /, //, %, ** (power). "
                    "Examples: '17.5 * 1.19', '2**10', '4832 * 0.17', '(100 - 15) / 3'."
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "expression": {
                            "type": "string",
                            "description": "Mathematical expression (e.g. '4832 * 0.17')",
                        },
                    },
                    "required": ["expression"],
                },
                executor=_execute,
            ),
        ]

    def get_prompt_instructions(self, lang: str) -> str:
        return ""

    def get_ui_status(self, tool_name: str, tool_args: dict[str, Any], lang: str) -> str:
        if tool_name == "calculate":
            return f"🔢 {tool_args.get('expression', '')}"
        return ""


plugin = CalculatorPlugin()
