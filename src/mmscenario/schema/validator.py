"""Schema validation logic beyond what Pydantic covers automatically."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from pydantic import ValidationError as PydanticValidationError

from .loader import load_full_scenario, load_scenario
from .models import IPActivityDB, L1Pipeline, ScenarioFile


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
            scenario = load_full_scenario(yaml_path)
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
            result.issues.extend(self._check_ip_activity(scenario.ip_activity))
        result.issues.extend(self._check_variants(scenario))

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
                for mode in [inst.default] + inst.modes:
                    if mode.override is not None and not mode.override.reason.strip():
                        issues.append(Issue(
                            "ERROR",
                            f"ip_activity.ip_instances[{inst.id}].{mode.id}._override",
                            "_override.reason must not be empty"
                        ))
        return issues

    def _check_ip_activity(self, ip_activity: IPActivityDB) -> list[Issue]:
        """Warn on duplicate mode IDs and estimated BW values."""
        issues: list[Issue] = []
        for inst in ip_activity.ip_instances:
            # Duplicate mode IDs
            seen: set[str] = set()
            for mode in inst.modes:
                if mode.id in seen:
                    issues.append(Issue(
                        "WARNING",
                        f"ip_activity.ip_instances[{inst.id}].modes",
                        f"Duplicate mode id: '{mode.id}'"
                    ))
                seen.add(mode.id)
            # Estimated BW hint
            for mode in [inst.default] + inst.modes:
                has_bw = mode.bw_read_gbps is not None or mode.bw_write_gbps is not None
                if has_bw and mode.source.value == "estimated":
                    issues.append(Issue(
                        "WARNING",
                        f"ip_activity.ip_instances[{inst.id}].{mode.id}.source",
                        "BW value is 'estimated' — confirm with measurement when possible"
                    ))
        return issues

    def _check_variants(self, scenario: ScenarioFile) -> list[Issue]:
        """Check variant IDs are unique and referenced node/edge IDs exist."""
        issues: list[Issue] = []
        node_ids = {n.id for n in scenario.pipeline.nodes}
        edge_ids = {e.id for e in scenario.pipeline.edges}
        ip_ids = (
            {ip.id for ip in scenario.ip_activity.ip_instances}
            if scenario.ip_activity else set()
        )

        seen_ids: set[str] = set()
        for v in scenario.variants:
            if v.id in seen_ids:
                issues.append(Issue("ERROR", f"variants[{v.id}]", "Duplicate variant id"))
            seen_ids.add(v.id)
            for nid in v.buffers:
                if nid not in node_ids:
                    issues.append(Issue(
                        "WARNING", f"variants[{v.id}].buffers",
                        f"Node '{nid}' not found in pipeline.nodes"
                    ))
            for eid in v.edges:
                if eid not in edge_ids:
                    issues.append(Issue(
                        "WARNING", f"variants[{v.id}].edges",
                        f"Edge '{eid}' not found in pipeline.edges"
                    ))
            for ipid in v.ip_modes:
                if ipid not in ip_ids:
                    issues.append(Issue(
                        "WARNING", f"variants[{v.id}].ip_modes",
                        f"IP '{ipid}' not found in ip_activity.ip_instances"
                    ))
        return issues
