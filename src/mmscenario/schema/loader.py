"""YAML loading with ruamel.yaml (comment-preserving) and pydantic validation.

File conventions:
  usecase/<name>.yaml          → L0 (scenario) + L1 (pipeline)
  traces/<name>/l2_ip_activity.yaml  → L2 (ip_activity)
  traces/<name>/l3_bus_memory.yaml   → L3 (bus_memory)
"""

from __future__ import annotations

from pathlib import Path

from ruamel.yaml import YAML

from .compact import expand_compact, is_compact
from .models import L2Activity, L3Memory, ScenarioFile


def _yaml_load(path: Path) -> dict:
    yaml = YAML()
    yaml.preserve_quotes = True
    with open(path, encoding="utf-8") as f:
        return yaml.load(f) or {}


def load_scenario(path: Path) -> ScenarioFile:
    """Load a usecase YAML (L0 + L1 only). ip_activity and bus_memory will be None."""
    data = _yaml_load(path)
    if is_compact(data):
        data = expand_compact(data)
    return ScenarioFile.model_validate(data)


def load_traces(
    traces_dir: Path,
    scenario_name: str,
) -> tuple[L2Activity | None, L3Memory | None]:
    """Load L2 and L3 from traces/<scenario_name>/ directory.

    Returns (None, None) if the directory or files don't exist.
    Each file is optional independently.
    """
    base = traces_dir / scenario_name
    l2: L2Activity | None = None
    l3: L3Memory | None = None

    l2_path = base / "l2_ip_activity.yaml"
    if l2_path.exists():
        data = _yaml_load(l2_path)
        l2 = L2Activity.model_validate(data["ip_activity"])

    l3_path = base / "l3_bus_memory.yaml"
    if l3_path.exists():
        data = _yaml_load(l3_path)
        l3 = L3Memory.model_validate(data["bus_memory"])

    return l2, l3


def load_full_scenario(
    usecase_path: Path,
    traces_dir: Path | None = None,
) -> ScenarioFile:
    """Load L0+L1 from usecase YAML, then merge L2+L3 from traces/ if present.

    traces_dir defaults to <usecase_path.parent.parent>/traces/
    scenario_name is derived from the usecase YAML filename stem.
    """
    scenario = load_scenario(usecase_path)

    if traces_dir is None:
        traces_dir = usecase_path.parent.parent / "traces"

    scenario_name = usecase_path.stem
    l2, l3 = load_traces(traces_dir, scenario_name)

    if l2 is not None:
        scenario = scenario.model_copy(update={"ip_activity": l2})
    if l3 is not None:
        scenario = scenario.model_copy(update={"bus_memory": l3})

    return scenario
