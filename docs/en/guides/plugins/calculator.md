# Calculator Plugin

**File:** `aifred/plugins/tools/calculator/`

Safe evaluation of mathematical expressions. The expression is parsed via Python's
`ast` module and walked node by node — no `eval()`, no name lookups, no function
calls — so only the supported arithmetic operators can ever run.

## Tools

| Tool | Description | Tier |
|------|------------|------|
| `calculate` | Evaluate a mathematical expression and return the exact result | READONLY |

### Parameters (`calculate`)

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `expression` | string | yes | Mathematical expression, e.g. `4832 * 0.17` |

## Supported operators

- `+`, `-`, `*`, `/`
- `//` (floor division), `%` (modulo), `**` (power)
- unary `+` / `-`
- parentheses for grouping

Integer results are returned without a decimal point; non-integer results are
formatted with up to 10 significant digits.

## Examples

```
17.5 * 1.19      → 17.5 * 1.19 = 20.825
2**10            → 2**10 = 1024
(100 - 15) / 3   → (100 - 15) / 3 = 28.33333333
```

On an invalid or unsupported expression, the tool returns a JSON error object
(`{"error": "Cannot evaluate '...': ..."}`) instead of raising.

## Configuration

None — the plugin is always available and has no dependencies or API keys.
