#!/usr/bin/env python3
"""Render every page of the AIfred app to the JSX text Reflex compiles it to.

A change of types, declarations or decorators must not change what the browser gets. Render the
tree before and after the change and diff the two outputs: an equal output is the proof. Run both
in the same environment (a worktree has no .env, so plugins configured there render differently;
compare a worktree with a worktree). Names Reflex derives from a hash of a render function
(``index_<hash>``) change with its source and are normalised.

Usage (from the tree root):
    AIFRED_CLI_MODE=1 PYTHONPATH=. venv/bin/python scripts/render_pages.py > pages.txt
"""

import re

from aifred import aifred as app_module


def main() -> None:
    app = app_module.app
    app._apply_decorated_pages()  # @rx.page is applied at compile time
    pages = app._unevaluated_pages
    for route in sorted(pages):
        page = pages[route]
        component = page.component() if callable(page.component) else page.component
        text = re.sub(r"index_[0-9a-f]{32}", "index_X", str(component))
        print(f"=== {route} ({len(text)} chars)")
        print(text)


if __name__ == "__main__":
    main()
