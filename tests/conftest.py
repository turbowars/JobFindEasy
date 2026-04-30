"""pytest fixtures.

Centralized so tests don't have to repeat boilerplate. Each fixture is named
for what it provides, not how it's built.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

# Ensure the project root is on sys.path so `import src.*` works the same way
# as it does in the running app (the `[tool.setuptools.packages.find]` block in
# pyproject.toml only kicks in for installed packages; pytest runs from source).
PROJECT_ROOT = Path(__file__).parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


@pytest.fixture
def clean_model_env(monkeypatch):
    """Tests that inspect get_model() should start from a clean env."""
    for k in list(os.environ.keys()):
        if k.endswith("_MODEL"):
            monkeypatch.delenv(k, raising=False)
    yield
