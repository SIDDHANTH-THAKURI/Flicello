"""Everything the code imports is declared in pyproject: in the base, `ai` or `studio` dependencies (what
`./flicello` installs), or in an optional extra the code only imports when it is there (`paint`, `nvidia`)."""

from __future__ import annotations

import ast
import re
import sys
import tomllib
from importlib.metadata import packages_distributions
from pathlib import Path

import vidosuper

SRC = Path(vidosuper.__file__).parent
# Imported directly, but guaranteed by a declared framework that exposes it as part of its own API.
VIA = {"markupsafe": "jinja2", "starlette": "fastapi"}


def _canon(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def test_every_package_the_code_imports_is_declared():
    """httpx once reached the code only through the dev extra (and through anthropic), so a fresh install crashed
    while every local test passed. A direct import needs a direct declaration (dev's packages don't count)."""
    project = tomllib.loads((SRC.parents[1] / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    extras = project["optional-dependencies"]
    declared = {_canon(re.split(r"[\s<>=!~\[;]", r, maxsplit=1)[0])
                for r in project["dependencies"] + [x for name, reqs in extras.items() if name != "dev" for x in reqs]}
    owners = packages_distributions()
    missing: dict[str, set[str]] = {}
    for path in SRC.rglob("*.py"):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                names = [node.module]
            else:
                continue
            for name in names:
                top = name.split(".")[0]
                if top in sys.stdlib_module_names or top == "vidosuper":
                    continue
                if top in VIA and VIA[top] in declared:
                    continue
                if not {_canon(d) for d in owners.get(top) or [top]} & declared:  # not installed here: by its name
                    missing.setdefault(top, set()).add(path.relative_to(SRC).as_posix())
    assert not missing, f"imported but not declared in pyproject: {missing}"
