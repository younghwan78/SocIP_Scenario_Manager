"""Schema validation logic beyond what Pydantic covers automatically."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from pydantic import ValidationError as PydanticValidationError

from .loader import load_scenario
from .models import L1Pipeline, L2Activity, L3Memory, ScenarioFile


@dataclass
class Issue:
    level: str      # "ERROR" | "WARNING"
    location: str   # e.g. "pipeline.edges[2].source"
    message: str

    def __str__(self) -> str:
        return f"[{self.level}] {self.location}: {self.message}"


@dataclass
class ValidationResult:
    issues: list[Issue] = field(default_factory=list)

    @property
    def errors(self) -> list[Issue]:
        return [i for i in self.issues if i.level == "ERROR"]

    @property
    def warnings(self) -> list[Issue]:
        return [i for i in self.issues if i.level == "WARNING"]

    @property
    def is_valid(self) -> bool:
        return len(self.errors) == 0

    def print_report(self) -> None:
        import sys
        out = sys.stdout if sys.stdout.encoding and sys.stdout.encoding.lower() in ("utf-8", "utf-16") else None
        def _print(s: str) -> None:
            try:
                print(s)
            except UnicodeEncodeError:
                print(s.encode("ascii", errors="replace").decode("ascii"))

        if not self.issues:
            _print("OK - no issues found.")
            return
        for issue in self.issues:
            _print(str(issue))
        _print(f"\n{len(self.errors)} error(s), {len(self.warnings)} warning(s).")


class SchemaValidator:
    def validate(self, yaml_path: Path) -> ValidationResult:
        result = ValidationResult()

        # --- Parse (Pydantic handles required fields + type errors) ---
        try:
            scenario = load_scenario(yaml_path)
        except PydanticValidationError as exc:
            for err in exc.errors():
                loc = " → ".join(str(p) for p in err["loc"])
                result.issues.append(Issue("ERROR", loc, err["msg"]))
            return result
        except Exception as exc:
            result.issues.append(Issue("ERROR", str(yaml_path), f"Failed to load YAML: {exc}"))
            return result

        # --- Additional checks ---
        result.issues.extend(self._check_referential_integrity(scenario.pipeline))
        result.issues.extend(self._check_override_reasons(scenario))
        if scenario.ip_activity:
            result.issues.extend(self._check_variant_consistency(scenario.ip_activity))
        if scenario.bus_memory:
            result.issues.extend(self._check_l3_sources(scenario.bus_memory))

        # Cycle detection (delegated to DAG module)
        try:
            from mmscenario.dag.pipeline import ScenarioPipeline
            pipeline = ScenarioPipeline(scenario.pipeline)
            cycles = pipeline.detect_cycles()
            for cycle in cycles:
                result.issues.append(Issue(
                    "ERROR", "pipeline",
                    f"Cycle detected: {' → '.join(cycle)}"
                ))
            for node_id in pipeline.detect_isolated_nodes():
                result.issues.append(Issue(
                    "WARNING", f"pipeline.nodes[{node_id}]",
                    "Isolated node (no edges)"
                ))
        except ImportError:
            pass  # DAG module not yet available

        return result

    def _check_referential_integrity(self, pipeline: L1Pipeline) -> list[Issue]:
        issues: list[Issue] = []
        node_ids = {n.id for n in pipeline.nodes}
        for i, edge in enumerate(pipeline.edges):
            if edge.source not in node_ids:
                issues.append(Issue(
                    "ERROR", f"pipeline.edges[{i}].source",
                    f"Node '{edge.source}' not found in pipeline.nodes"
                ))
            if edge.target not in node_ids:
                issues.append(Issue(
                    "ERROR", f"pipeline.edges[{i}].target",
                    f"Node '{edge.target}' not found in pipeline.nodes"
                ))
        return issues

    def _check_override_reasons(self, scenario: ScenarioFile) -> list[Issue]:
        """_override entries must include a reason."""
        issues: list[Issue] = []
        if scenario.ip_activity:
            for inst in scenario.ip_activity.ip_instances:
                if inst.override is not None and not inst.override.reason.strip():
                    issues.append(Issue(
                        "ERROR", f"ip_activity.ip_instances[{inst.id}]._override",
                        "_override.reason must not be empty"
                    ))
        if scenario.bus_memory:
            for entry in scenario.bus_memory.bus_entries:
                if entry.override is not None and not entry.override.reason.strip():
                    issues.append(Issue(
                        "ERROR", f"bus_memory.bus_entries[{entry.id}]._override",
                        "_override.reason must not be empty"
                    ))
        return issues

    def _check_l3_sources(self, bus_memory: L3Memory) -> list[Issue]:
        """Warn if BW values are present but source is not 'measured'."""
        issues: list[Issue] = []
        for entry in bus_memory.bus_entries:
            has_bw = entry.bw_read_gbps is not None or entry.bw_write_gbps is not None
            if has_bw and entry.source == "estimated":
                issues.append(Issue(
                    "WARNING", f"bus_memory.bus_entries[{entry.id}].source",
                    "BW value is 'estimated' — confirm with measurement when possible"
                ))
        return issues

    def _check_variant_consistency(self, ip_activity: L2Activity) -> list[Issue]:
        """Warn on duplicate variant conditions for the same IP instance."""
        issues: list[Issue] = []
        for inst in ip_activity.ip_instances:
            seen: set[str] = set()
            for variant in inst.variants:
                cond = variant.condition.strip()
                if cond in seen:
                    issues.append(Issue(
                        "WARNING", f"ip_activity.ip_instances[{inst.id}].variants",
                        f"Duplicate variant condition: '{cond}'"
                    ))
                seen.add(cond)
        return issues
