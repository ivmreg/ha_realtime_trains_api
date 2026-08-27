"""Real-Home-Assistant test harness configuration."""

from __future__ import annotations

from pathlib import Path

import custom_components

_REPOSITORY_CUSTOM_COMPONENTS = str(
    Path(__file__).resolve().parents[1] / "custom_components"
)
if _REPOSITORY_CUSTOM_COMPONENTS not in custom_components.__path__:
    custom_components.__path__ = [
        _REPOSITORY_CUSTOM_COMPONENTS,
        *custom_components.__path__,
    ]
