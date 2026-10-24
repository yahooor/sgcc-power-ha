"""在真实 homeassistant 包下导入集成全部模块。

捕获「HA 升级移除 API」类破坏（如 UnitOfUVIndex / SensorDeviceClass.CARBON_MONOXIDE
被移除导致的加载失败），不启动 HA。CI 的 ha-compat 矩阵按 HA 版本运行本脚本。
"""

import importlib
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tests"))

import conftest  # noqa: E402,F401  # 建立 custom_components 包路径垫片（须在 sys.path 修改后）

MODULES = (
    "custom_components.sgcc_power",
    "custom_components.sgcc_power.api",
    "custom_components.sgcc_power.models",
    "custom_components.sgcc_power.crypto",
    "custom_components.sgcc_power.login",
    "custom_components.sgcc_power.synthetic_device",
    "custom_components.sgcc_power.turing.device_token",
    "custom_components.sgcc_power.statistics",
    "custom_components.sgcc_power.sensor",
    "custom_components.sgcc_power.coordinator",
    "custom_components.sgcc_power.compat",
    "custom_components.sgcc_power.diagnostics",
    "custom_components.sgcc_power.config_flow",
)


def main() -> int:
    from importlib.metadata import version

    hass_version = version("homeassistant")
    for module_name in MODULES:
        importlib.import_module(module_name)
        print("OK", module_name)
    print(f"ALL {len(MODULES)} MODULES IMPORT UNDER homeassistant {hass_version}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
