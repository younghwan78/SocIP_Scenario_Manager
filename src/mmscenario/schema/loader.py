"""YAML loading with ruamel.yaml (comment-preserving) and pydantic validation.

File conventions (new):
  usecase/<project>/<name>.yaml             → L0+L1 (+variants)
  traces/<project>/<name>/ip_activity.yaml  → unified IP+BW activity DB

Backward compat (old format still loaded if new file absent):
  traces/<name>/l2_ip_activity.yaml  → L2 (ip_activity)
  traces/<name>/l3_bus_memory.yaml   → L3 (bus_memory)
"""

from __future__ import annotations

import logging
from pathlib import Path

from ruamel.yaml import YAML

from .compact import expand_compact, is_compact
from .models import (
    IPActivityDB, IPModeSpec, IPSpec,
    L2Activity, L3Memory,
    ScenarioFile,
)

logger = logging.getLogger(__name__)


def _yaml_load(path: Path) -> dict:
    yaml = YAML()
    yaml.preserve_quotes = True
    with open(path, encoding="utf-8") as f:
        return yaml.load(f) or {}


def load_scenario(path: Path) -> ScenarioFile:
    """Load a usecase YAML (L0 + L1 + variants). ip_activity will be None."""
    data = _yaml_load(path)
    if is_compact(data):
        data = expand_compact(data)
    return ScenarioFile.model_validate(data)


def _traces_scenario_dir(usecase_path: Path) -> Path:
    """Derive the traces directory that corresponds to a usecase YAML.

    scenarios/usecase/projectA/video_recording.yaml
        → scenarios/traces/projectA/video_recording/

    scenarios/usecase/video_recording.yaml
        → scenarios/traces/video_recording/
    """
    parts = list(usecase_path.parts[:-1])  # drop filename
    for i, p in enumerate(parts):
        if p.lower() == "usecase":
            parts[i] = "traces"
            break
    return Path(*parts) / usecase_path.stem


def load_ip_activity(traces_dir: Path) -> IPActivityDB | None:
    """Load unified ip_activity.yaml; fall back to legacy l2+l3 files."""
    new_path = traces_dir / "ip_activity.yaml"
    if new_path.exists():
        data = _yaml_load(new_path)
        return IPActivityDB.model_validate(data)

    # Backward compat: load old l2_ip_activity.yaml and convert
    l2_path = traces_dir / "l2_ip_activity.yaml"
    if l2_path.exists():
        logger.debug("Loading legacy l2_ip_activity.yaml from %s", traces_dir)
        data = _yaml_load(l2_path)
        l2 = L2Activity.model_validate(data["ip_activity"])
        l3: L3Memory | None = None
        l3_path = traces_dir / "l3_bus_memory.yaml"
        if l3_path.exists():
            data3 = _yaml_load(l3_path)
            l3 = L3Memory.model_validate(data3["bus_memory"])
        return _legacy_to_ipactivitydb(l2, l3)

    return None


def _legacy_to_ipactivitydb(l2: L2Activity, l3: L3Memory | None) -> IPActivityDB:
    """Convert old L2Activity (+optional L3Memory) to IPActivityDB."""
    # Build BW lookup from L3: "ISP_0_read" / "ISP_0_write" → bw values
    bw_read: dict[str, float] = {}
    bw_write: dict[str, float] = {}
    if l3:
        for entry in l3.bus_entries:
            eid = entry.id.lower()
            # Convention: <ip_id>_read or <ip_id>_write
            if eid.endswith("_read") and entry.bw_read_gbps is not None:
                bw_read[entry.id[:-5]] = entry.bw_read_gbps
            elif eid.endswith("_write") and entry.bw_write_gbps is not None:
                bw_write[entry.id[:-6]] = entry.bw_write_gbps

    specs: list[IPSpec] = []
    for inst in l2.ip_instances:
        default = IPModeSpec(
            id="default",
            freq_mhz=inst.freq_mhz,
            bw_read_gbps=bw_read.get(inst.id),
            bw_write_gbps=bw_write.get(inst.id),
            source=inst.source,
        )
        modes = [
            IPModeSpec(
                id=v.condition.replace(" ", "_").lower(),
                condition=v.condition,
                freq_mhz=v.freq_mhz,
                source=inst.source,
            )
            for v in inst.variants
        ]
        specs.append(IPSpec(id=inst.id, default=default, modes=modes,
                            review_flags=inst.review_flags))
    return IPActivityDB(ip_instances=specs)


# Keep old load_traces signature for callers that pass explicit traces_dir
def load_traces(
    traces_dir: Path,
    scenario_name: str,
) -> tuple[IPActivityDB | None, None]:
    """Legacy API: load traces from traces_dir/<scenario_name>/.

    Returns (IPActivityDB | None, None) — second element kept for compat.
    """
    return load_ip_activity(traces_dir / scenario_name), None


def load_full_scenario(
    usecase_path: Path,
    traces_dir: Path | None = None,
) -> ScenarioFile:
    """Load L0+L1+variants from usecase YAML, then merge ip_activity from traces/.

    Traces directory is derived automatically from usecase_path unless overridden.
    """
    scenario = load_scenario(usecase_path)

    if traces_dir is not None:
        # Explicit override: use old convention traces_dir/<stem>/
        ip_db = load_ip_activity(traces_dir / usecase_path.stem)
    else:
        ip_db = load_ip_activity(_traces_scenario_dir(usecase_path))

    if ip_db is not None:
        scenario = scenario.model_copy(update={"ip_activity": ip_db})

    return scenario
