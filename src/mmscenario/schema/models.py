"""Pydantic v2 models for the 4-layer scenario data structure (L0~L3)."""

from __future__ import annotations

from enum import Enum
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field, model_validator


# ---------------------------------------------------------------------------
# Common enums and meta fields
# ---------------------------------------------------------------------------

class SourceEnum(str, Enum):
    calculated = "calculated"
    estimated = "estimated"
    measured = "measured"


class NodeTypeEnum(str, Enum):
    sw_task = "sw_task"
    hw_ip = "hw_ip"
    buffer = "buffer"


class SeverityEnum(str, Enum):
    high = "high"
    medium = "medium"
    low = "low"


class Override(BaseModel):
    """Manual correction wrapper — reason is required."""
    value: Any
    reason: str


class ReviewFlag(BaseModel):
    field: str
    reason: str


# ---------------------------------------------------------------------------
# L0 · Scenario / Task Graph
# ---------------------------------------------------------------------------

class RiskItem(BaseModel):
    severity: SeverityEnum
    description: str


class Dependency(BaseModel):
    task_id: str
    type: Literal["sequential", "buffer_share"]
    buffer_id: Optional[str] = None


class L0Scenario(BaseModel):
    category: str
    name: str
    version: str
    description: Optional[str] = None
    sw_thread: Literal["app", "framework", "hal_kernel"]
    output_period_ms: float
    budget_ms: float
    pipeline_latency_frames: Optional[int] = None
    dependencies: list[Dependency] = Field(default_factory=list)
    risks: list[RiskItem] = Field(default_factory=list)
    review_flags: list[ReviewFlag] = Field(default_factory=list, alias="_review_flags")

    model_config = {"populate_by_name": True}


# ---------------------------------------------------------------------------
# L1 · Pipeline (DAG)
# ---------------------------------------------------------------------------

class L1Node(BaseModel):
    id: str
    type: NodeTypeEnum
    label: str
    layer: Literal["app", "framework", "hal", "kernel", "hw", "memory"]
    sw_thread: Optional[Literal["app", "framework", "hal_kernel"]] = None
    comment: Optional[str] = None
    external: bool = False        # True for components outside SoC boundary (sensor, display, etc.)
    compression: Optional[bool] = None  # Buffer: AFBC/SBWC compression enabled → badge 'C'
    llc: Optional[bool] = None          # Buffer: Last Level Cache usage → badge 'L'
    rotation: Optional[bool] = None     # Buffer: DPU rotates this buffer → badge 'R'


class L1Edge(BaseModel):
    id: str
    source: str
    target: str
    role: Literal["data", "control"] = "data"
    # role=data:    buffer DMA transfer (solid line)
    # role=control: SW drives HW — ioctl/Codec2/DRM commit (dotted line)
    format: Optional[str] = None        # NV12, P010, etc. (data edges only)
    resolution: Optional[str] = None    # e.g. "3840x2160"
    fps: Optional[int] = None
    fan_out: bool = False
    branch_condition: Optional[str] = None


class L1Pipeline(BaseModel):
    nodes: list[L1Node]
    edges: list[L1Edge]

    @model_validator(mode="after")
    def node_ids_unique(self) -> "L1Pipeline":
        ids = [n.id for n in self.nodes]
        seen: set[str] = set()
        for nid in ids:
            if nid in seen:
                raise ValueError(f"Duplicate node id: '{nid}'")
            seen.add(nid)
        return self


# ---------------------------------------------------------------------------
# IP Activity DB  (unified L2 + L3 — replaces separate L2Activity / L3Memory)
# ---------------------------------------------------------------------------

class IPModeSpec(BaseModel):
    """Operating parameters for one IP mode (default or a named variant)."""
    id: str = "default"
    condition: Optional[str] = None       # human-readable condition description
    freq_mhz: Optional[float] = None
    power_mA: Optional[float] = None
    bw_read_gbps: Optional[float] = None
    bw_write_gbps: Optional[float] = None
    exec_time_ms: Optional[float] = None  # wall-clock time to process one frame
    source: SourceEnum = SourceEnum.estimated
    override: Optional[Override] = Field(None, alias="_override")

    model_config = {"populate_by_name": True}


class IPSpec(BaseModel):
    """One IP block with its default operating point and optional named modes."""
    id: str
    default: IPModeSpec
    modes: list[IPModeSpec] = Field(default_factory=list)
    review_flags: list[ReviewFlag] = Field(default_factory=list, alias="_review_flags")

    model_config = {"populate_by_name": True}

    def get_mode(self, mode_id: str) -> IPModeSpec:
        """Return the named mode, or default if mode_id is 'default' or not found."""
        if mode_id == "default":
            return self.default
        for m in self.modes:
            if m.id == mode_id:
                return m
        return self.default


class IPActivityDB(BaseModel):
    """Combined IP activity + BW database (replaces separate l2 + l3 files)."""
    ip_instances: list[IPSpec]


# ---------------------------------------------------------------------------
# Legacy L2 / L3 models — kept for backward-compat loading of old trace files
# ---------------------------------------------------------------------------

class IPVariant(BaseModel):
    condition: str
    freq_mhz: Optional[float] = None
    voltage_mv: Optional[int] = None
    active_ratio: Optional[float] = None


class L2IPInstance(BaseModel):
    id: str
    freq_mhz: Optional[float] = None
    voltage_mv: Optional[int] = None
    active_ratio: Optional[float] = None
    source: SourceEnum
    variants: list[IPVariant] = Field(default_factory=list)
    review_flags: list[ReviewFlag] = Field(default_factory=list, alias="_review_flags")
    override: Optional[Override] = Field(None, alias="_override")

    model_config = {"populate_by_name": True}


class L2Activity(BaseModel):
    ip_instances: list[L2IPInstance]


class L3BusEntry(BaseModel):
    id: str
    bw_read_gbps: Optional[float] = None
    bw_write_gbps: Optional[float] = None
    latency_budget_us: Optional[float] = None
    source: SourceEnum
    override: Optional[Override] = Field(None, alias="_override")

    model_config = {"populate_by_name": True}


class L3Memory(BaseModel):
    bus_entries: list[L3BusEntry]


# ---------------------------------------------------------------------------
# DPU Composition (display layer layout)
# ---------------------------------------------------------------------------

class Size(BaseModel):
    """Width × height only — used where origin is always (0, 0), e.g. display resolution."""
    w: int
    h: int


class Rect(BaseModel):
    """Positioned rectangle — used for source_crop and display_frame."""
    x: int = 0
    y: int = 0
    w: int
    h: int


class DpuPlane(BaseModel):
    name: str
    buffer: str                  # references a pipeline node id
    source_crop: Rect            # region of the source buffer to read (pre-transform)
    display_frame: Rect          # destination rectangle on the physical display (post-transform)
    transform: Literal[
        "NONE", "ROT_90", "ROT_180", "ROT_270", "FLIP_H", "FLIP_V"
    ] = "NONE"
    z_order: int = 0            # lower = drawn first (background)
    plane_alpha: float = 1.0


class DpuComposition(BaseModel):
    display_id: str = "display"          # must match a pipeline node id
    display_name: Optional[str] = None  # human-readable name (e.g. "Main Display", "Cover Display")
    display_size: Size                   # physical panel resolution
    planes: list[DpuPlane]


# ---------------------------------------------------------------------------
# Scenario Variants
# ---------------------------------------------------------------------------

class ScenarioVariant(BaseModel):
    """Resolution / mode variant of a scenario sharing the same pipeline topology."""
    id: str                              # e.g. "FHD30", "UHD30", "8K30"
    name: str                            # e.g. "FHD 30fps (1920×1080)"
    output_period_ms: Optional[float] = None
    budget_ms: Optional[float] = None
    # node_id → field overrides for buffer nodes (label, compression, llc, rotation, ...)
    buffers: dict[str, dict] = Field(default_factory=dict)
    # edge_id → field overrides (format, resolution, fps, fan_out)
    edges: dict[str, dict] = Field(default_factory=dict)
    # ip_id → mode_id in IPSpec.modes ("default" or named mode)
    ip_modes: dict[str, str] = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# Top-level scenario file
# ---------------------------------------------------------------------------

class ScenarioFile(BaseModel):
    scenario: L0Scenario
    pipeline: L1Pipeline
    ip_activity: Optional[IPActivityDB] = None
    dpu_compositions: list[DpuComposition] = Field(default_factory=list)
    # ^ list enables foldable / multi-display scenarios (display0, display1, …)
    variants: list[ScenarioVariant] = Field(default_factory=list)


# Loading functions live in schema/loader.py to keep this file model-only.
