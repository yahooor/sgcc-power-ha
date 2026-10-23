"""Load the integration's protocol modules without importing Home Assistant."""

import sys
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CUSTOM_COMPONENTS = ROOT / "custom_components"
PACKAGE = CUSTOM_COMPONENTS / "sgcc_power"

custom_components = types.ModuleType("custom_components")
custom_components.__path__ = [str(CUSTOM_COMPONENTS)]
sys.modules.setdefault("custom_components", custom_components)

sgcc_power = types.ModuleType("custom_components.sgcc_power")
sgcc_power.__path__ = [str(PACKAGE)]
sys.modules.setdefault("custom_components.sgcc_power", sgcc_power)
