"""Data transformation layer: pydantic models + DAG → Cytoscape.js elements.

Separates data preparation from HTML rendering so each can be tested independently.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from mmscenario.dag.pipeline import ScenarioPipeline
    from mmscenario.schema.models import ScenarioFile

# Visual style constants (single source of truth for both data_prep and templates)
LAYER_STYLE: dict[str, dict[str, str]] = {
    "app":       {"color": "#8e44ad", "bg": "#F5EEF8", "label": "App"},
    "framework": {"color": "#2980b9", "bg": "#EBF5FB", "label": "Framework"},
    "hal":       {"color": "#16a085", "bg": "#E8F8F5", "label": "HAL"},
    "kernel":    {"color": "#2c3e50", "bg": "#EAECEE", "label": "Kernel"},
    "hw":        {"color": "#E67E22", "bg": "#FDF2E9", "label": "HW"},
    "memory":    {"color": "#27AE60", "bg": "#EAFAF1", "label": "Buffer / Memory"},
}

NODE_TYPE_SHAPE: dict[str, str] = {
    "sw_task": "round-rectangle",
    "hw_ip":   "rectangle",
    "buffer":  "barrel",   # cylinder shape — standard symbol for data buffers
}

EXTERNAL_COLOR = "#7f8c8d"   # gray for SoC-external components (sensor, display, etc.)


def build_cytoscape_elements(
    pipeline: "ScenarioPipeline",
    layout: dict[str, dict[str, float]],
) -> list[dict]:
    """Convert pipeline nodes/edges + pre-computed layout into Cytoscape.js elements.

    Returns a list in Cytoscape.js format:
      [{"data": {...}, "position": {"x": ..., "y": ...}}, ...]  for nodes
      [{"data": {"id": ..., "source": ..., "target": ..., ...}}]  for edges
    """
    elements: list[dict] = []

    for node in pipeline._data.nodes:
        pos = layout.get(node.id, {"x": 0, "y": 0})
        layer_info = LAYER_STYLE.get(node.layer, {})
        # External SoC components use gray; internal use layer color
        color = EXTERNAL_COLOR if node.external else layer_info.get("color", "#999")
        data: dict = {
            "id": node.id,
            "label": node.label,
            "type": node.type.value,
            "layer": node.layer,
            "color": color,
            "shape": NODE_TYPE_SHAPE.get(node.type.value, "rectangle"),
            "comment": node.comment or "",
            "external": node.external,
        }
        # Badge flags — only include when explicitly set (None = not applicable)
        if node.compression is not None:
            data["compression"] = node.compression
        if node.llc is not None:
            data["llc"] = node.llc
        if node.rotation is not None:
            data["rotation"] = node.rotation
        elements.append({"data": data, "position": pos})

    for edge in pipeline._data.edges:
        label_parts: list[str] = []
        if edge.format:
            label_parts.append(edge.format)
        if edge.resolution:
            label_parts.append(edge.resolution)
        if edge.fps:
            label_parts.append(f"{edge.fps}fps")
        elements.append({
            "data": {
                "id": edge.id,
                "source": edge.source,
                "target": edge.target,
                "label": " ".join(label_parts),
                "role": edge.role,
                "fan_out": edge.fan_out,
                "branch_condition": edge.branch_condition or "",
            },
        })

    return elements


def build_scenario_dict(scenario: "ScenarioFile") -> dict:
    """Serialize ScenarioFile to a plain dict for embedding in HTML as JSON."""
    return scenario.model_dump(by_alias=True, exclude_none=True)
