"""Perfetto trace parser for automatic scenario component detection.

Principles:
- NO slice name usage (unreliable across vendor/Android versions)
- Detection based solely on: process/thread tables + android_logs (Exynos tags)
- Undetectable items → _review_flags (no errors, processing continues)
- Exynos-specific: one implementation, not multi-vendor

SQL queries and classification constants live in queries.py.
"""

from __future__ import annotations

import json
import logging
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from .queries import (
    ISP_DUAL_HINTS,
    SQL_ACTIVE_PROCESSES,
    SQL_CODEC_TYPE,
    SQL_HWC_COMPOSITION,
    SQL_ISP_CONFIG,
    SQL_NPU_ACTIVE,
    SW_LAYER_PATTERNS,
)

logger = logging.getLogger(__name__)


@dataclass
class CodecInfo:
    codec_type: str     # H.265 | H.264 | AV1 | unknown
    direction: str      # encode | decode | unknown


@dataclass
class ParseResult:
    active_processes: dict[str, list[str]] = field(default_factory=dict)  # layer → [process names]
    composition_mode: str = "unknown"  # "gpu" | "hw_overlay" | "unknown"
    npu_active: bool = False
    isp_config: str = "unknown"        # "single" | "dual" | "unknown"
    codecs: list[CodecInfo] = field(default_factory=list)
    review_flags: list[str] = field(default_factory=list)


class PerfettoParser:
    """Parse a .perfetto-trace file and detect scenario components."""

    def __init__(self, trace_processor_path: Path) -> None:
        self._tp = Path(trace_processor_path)
        if not self._tp.exists():
            raise FileNotFoundError(f"trace_processor_shell not found: {self._tp}")

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def parse(self, trace_path: Path, scenario_name: str = "scenario") -> ParseResult:
        result = ParseResult()

        result.active_processes = self._detect_active_processes(trace_path)   # P1
        result.composition_mode = self._detect_composition_mode(trace_path)   # P2
        result.npu_active       = self._detect_npu(trace_path)                # P3
        result.isp_config       = self._detect_isp_config(trace_path)         # P4
        result.codecs           = self._detect_codecs(trace_path)             # P5

        # Auto-populate review_flags for undetected items
        for attr, label in [
            ("composition_mode", "composition_mode"),
            ("isp_config", "isp_config"),
        ]:
            if getattr(result, attr) == "unknown":
                result.review_flags.append(f"{label}: could not be auto-detected - set manually")

        if not result.codecs:
            result.review_flags.append("codecs: no MediaCodec logcat found - set manually")

        return result

    def generate_draft_yaml(self, result: ParseResult, template_dir: Path | None = None) -> str:
        """Generate a draft usecase YAML string from ParseResult (P6)."""
        lines: list[str] = [
            "# Auto-generated draft — review all _review_flags before use",
            "scenario:",
            "  category: TBD",
            "  name: TBD",
            "  version: '0.1'",
            "  sw_thread: hal_kernel",
            "  output_period_ms: null   # TBD",
            "  budget_ms: null          # TBD",
            "  _review_flags: []",
            "",
            "# Active processes detected (P1)",
        ]
        for layer, procs in sorted(result.active_processes.items()):
            if procs:
                lines.append(f"#   [{layer}]: {', '.join(procs)}")

        lines += [
            "",
            f"# Composition: {result.composition_mode}",
            f"# NPU active:  {result.npu_active}",
            f"# ISP config:  {result.isp_config}",
        ]
        for c in result.codecs:
            lines.append(f"# Codec:        {c.codec_type} ({c.direction})")

        lines += [
            "",
            "pipeline:",
            "  nodes: []   # TODO: fill based on detected components above",
            "  edges: []",
        ]

        if result.review_flags:
            lines += ["", "# _review_flags (items that require manual input):"]
            for flag in result.review_flags:
                lines.append(f"#   - {flag}")

        return "\n".join(lines) + "\n"

    # ------------------------------------------------------------------
    # Detection methods (P1~P5)
    # ------------------------------------------------------------------

    def _detect_active_processes(self, trace_path: Path) -> dict[str, list[str]]:
        """P1: Active processes → SW layer classification."""
        rows = self._run_sql(trace_path, SQL_ACTIVE_PROCESSES)
        result: dict[str, list[str]] = {"app": [], "framework": [], "hal_kernel": [], "other": []}
        for row in rows:
            name: str = row.get("name", "")
            layer = "other"
            for pattern, lyr in SW_LAYER_PATTERNS.items():
                if pattern.lower() in name.lower():
                    layer = lyr
                    break
            result[layer].append(name)
        return result

    def _detect_composition_mode(self, trace_path: Path) -> str:
        """P2: GPU composition vs HW overlay via Exynos HWC logcat."""
        rows = self._run_sql(trace_path, SQL_HWC_COMPOSITION)
        for row in rows:
            msg: str = row.get("msg", "").lower()
            if "hw_overlay" in msg or "overlay" in msg:
                return "hw_overlay"
            if "gpu" in msg or "gles" in msg:
                return "gpu"
        if not rows:
            logger.debug("No HWC logcat found in trace")
        return "unknown"

    def _detect_npu(self, trace_path: Path) -> bool:
        """P3: NPU presence via process/thread table."""
        rows = self._run_sql(trace_path, SQL_NPU_ACTIVE)
        return bool(rows and int(rows[0].get("cnt", 0)) > 0)

    def _detect_isp_config(self, trace_path: Path) -> str:
        """P4: ISP configuration (single/dual) via CameraHAL logcat."""
        rows = self._run_sql(trace_path, SQL_ISP_CONFIG)
        for row in rows:
            msg = row.get("msg", "").lower()
            if any(h in msg for h in ISP_DUAL_HINTS):
                return "dual"
        return "single" if rows else "unknown"

    def _detect_codecs(self, trace_path: Path) -> list[CodecInfo]:
        """P5: Codec type and direction via MediaCodec logcat."""
        rows = self._run_sql(trace_path, SQL_CODEC_TYPE)
        seen: set[tuple[str, str]] = set()
        result: list[CodecInfo] = []
        for row in rows:
            msg = row.get("msg", "").lower()
            codec_type = "unknown"
            direction = "unknown"
            if "hevc" in msg or "h265" in msg or "h.265" in msg:
                codec_type = "H.265"
            elif "avc" in msg or "h264" in msg or "h.264" in msg:
                codec_type = "H.264"
            elif "av1" in msg:
                codec_type = "AV1"
            if "encoder" in msg or "encode" in msg:
                direction = "encode"
            elif "decoder" in msg or "decode" in msg:
                direction = "decode"
            key = (codec_type, direction)
            if key not in seen:
                seen.add(key)
                result.append(CodecInfo(codec_type=codec_type, direction=direction))
        return result

    # ------------------------------------------------------------------
    # SQL execution via subprocess
    # ------------------------------------------------------------------

    def _run_sql(self, trace_path: Path, sql: str) -> list[dict]:
        """Run SQL against trace file using trace_processor_shell subprocess."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".sql", delete=False, encoding="utf-8") as f:
            f.write(sql)
            sql_file = Path(f.name)
        try:
            cmd = [
                str(self._tp),
                "--query-file", str(sql_file),
                "--output-format", "json",
                str(trace_path),
            ]
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
            if proc.returncode != 0:
                logger.warning("trace_processor_shell error: %s", proc.stderr[:500])
                return []
            return json.loads(proc.stdout) if proc.stdout.strip() else []
        except subprocess.TimeoutExpired:
            logger.warning("trace_processor_shell timed out on query: %s", sql[:80])
            return []
        except json.JSONDecodeError as exc:
            logger.warning("Failed to parse trace_processor_shell output: %s", exc)
            return []
        finally:
            sql_file.unlink(missing_ok=True)
