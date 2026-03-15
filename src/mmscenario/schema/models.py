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
    external: bool = False  # True for components outside SoC boundary (sensor, display, etc.)


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
# L2 · IP Activity
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


# ---------------------------------------------------------------------------
# L3 · Bus / Memory
# ---------------------------------------------------------------------------

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
# Top-level scenario file
# ---------------------------------------------------------------------------

class ScenarioFile(BaseModel):
    scenario: L0Scenario
    pipeline: L1Pipeline
    ip_activity: Optional[L2Activity] = None
    bus_memory: Optional[L3Memory] = None


# Loading functions live in schema/loader.py to keep this file model-only.
