"""Pre-processor: compact_pipeline syntax → standard pipeline dict.

Compact format lets authors write scenarios in ~55 lines instead of ~330.
The expand_compact() function converts the abbreviated form into the same
dict structure that Pydantic's ScenarioFile.model_validate() expects, so
all downstream code (validator, DAG, renderer) is completely unaffected.

Compact format overview
-----------------------
compact_pipeline:
  nodes:
    <layer>:           # app | framework | hal | kernel | hw | memory
      <id>: "Label"    # string → label only
      <id>: {label: "Label", external: true, compression: false, ...}  # dict

  control:             # SW drives HW — dotted purple edges
    - [nodeA, nodeB, nodeC]   # consecutive pairs → control edges

  data:                # DMA buffer transfers — solid edges
    - [nodeA, nodeB]          # simple chain
    - {path: [nodeA, nodeB], format: NV12, resolution: "3840x2160", fps: 30, fan_out: true}
"""

from __future__ import annotations

# Layer name → (NodeTypeEnum value, sw_thread value or None)
_LAYER_TO_TYPE: dict[str, tuple[str, str | None]] = {
    "app":       ("sw_task", "app"),
    "framework": ("sw_task", "framework"),
    "hal":       ("sw_task", "hal_kernel"),
    "kernel":    ("sw_task", "hal_kernel"),
    "hw":        ("hw_ip",   None),
    "memory":    ("buffer",  None),
}

# Edge attributes that are valid on L1Edge (forwarded from data path dicts)
_EDGE_ATTRS = {"format", "resolution", "fps", "fan_out", "branch_condition"}


def is_compact(data: dict) -> bool:
    """Return True if the YAML dict uses compact_pipeline syntax."""
    return "compact_pipeline" in data


def expand_compact(data: dict) -> dict:
    """Convert compact_pipeline → standard pipeline dict.

    Returns a new dict with 'pipeline' key replacing 'compact_pipeline'.
    All other top-level keys (scenario, ip_activity, bus_memory) are
    preserved unchanged.
    """
    cp = data["compact_pipeline"]

    node_registry: dict[str, dict] = {}  # id → L1Node dict (insertion-ordered)
    edges: list[dict] = []
    seen_pairs: set[tuple[str, str]] = set()  # (src, tgt) dedup

    # ── 1. Build node registry ────────────────────────────────────────────────
    for layer_name, nodes in (cp.get("nodes") or {}).items():
        if layer_name not in _LAYER_TO_TYPE:
            raise ValueError(
                f"compact_pipeline.nodes: unknown layer '{layer_name}'. "
                f"Valid layers: {list(_LAYER_TO_TYPE)}"
            )
        node_type, sw_thread = _LAYER_TO_TYPE[layer_name]

        for node_id, value in (nodes or {}).items():
            if node_id in node_registry:
                continue  # first declaration wins

            if isinstance(value, str):
                attrs: dict = {"label": value}
            elif isinstance(value, dict):
                attrs = dict(value)
            else:
                attrs = {}

            node: dict = {
                "id": node_id,
                "type": node_type,
                "label": attrs.pop("label", _auto_label(node_id)),
                "layer": layer_name,
            }
            if sw_thread is not None:
                node["sw_thread"] = sw_thread
            # Pass through any remaining attrs (external, compression, llc,
            # rotation, comment, …) without modification
            node.update(attrs)
            node_registry[node_id] = node

    # ── 2. Control chains ─────────────────────────────────────────────────────
    for chain in (cp.get("control") or []):
        for i in range(len(chain) - 1):
            _add_edge(edges, seen_pairs, chain[i], chain[i + 1], "control")

    # ── 3. Data paths ─────────────────────────────────────────────────────────
    for item in (cp.get("data") or []):
        if isinstance(item, list):
            path: list[str] = item
            extra: dict = {}
        else:
            path = list(item["path"])
            extra = {k: v for k, v in item.items() if k in _EDGE_ATTRS}

        for i in range(len(path) - 1):
            # Format/resolution/fps/fan_out apply to the first hop only
            hop_extra = extra if i == 0 else {}
            _add_edge(edges, seen_pairs, path[i], path[i + 1], "data", **hop_extra)

    # ── 4. Assemble result ────────────────────────────────────────────────────
    result = {k: v for k, v in data.items() if k != "compact_pipeline"}
    result["pipeline"] = {
        "nodes": list(node_registry.values()),
        "edges": edges,
    }
    return result


# ── Helpers ───────────────────────────────────────────────────────────────────

def _add_edge(
    edges: list[dict],
    seen: set[tuple[str, str]],
    src: str,
    tgt: str,
    role: str,
    **extra: object,
) -> None:
    """Append an edge dict, skipping duplicate (src, tgt) pairs."""
    if (src, tgt) in seen:
        return
    seen.add((src, tgt))
    edge: dict = {
        "id": f"e_{src}_{tgt}",
        "source": src,
        "target": tgt,
        "role": role,
    }
    edge.update(extra)
    edges.append(edge)


def _auto_label(node_id: str) -> str:
    """Convert snake_case id to Title Case label as a fallback.

    enc_buf → "Enc Buf",  v4l2_driver → "V4L2 Driver"
    """
    return " ".join(word.capitalize() for word in node_id.split("_"))
