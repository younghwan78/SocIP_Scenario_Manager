"""DAG processing for L1 pipeline data.

Layout axes (Perfetto timeline philosophy):
  x-axis = relative time  (topological order, left → right)
  y-axis = layer          (SW top / HW middle / Memory/Buffer bottom)
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import networkx as nx

if TYPE_CHECKING:
    from mmscenario.schema.models import L1Pipeline

# Y-axis position per layer (SW sublayers top → HW → Memory bottom)
LAYER_Y: dict[str, float] = {
    "app":       0.0,
    "framework": 160.0,
    "hal":       320.0,
    "kernel":    480.0,
    "hw":        680.0,   # extra gap to visually separate SW stack from HW
    "memory":    860.0,
}

X_STEP = 200.0   # horizontal gap between topological levels
Y_STEP = 80.0    # vertical gap between nodes in the same layer


class ScenarioPipeline:
    """networkx-backed DAG for an L1 pipeline."""

    def __init__(self, pipeline_data: "L1Pipeline") -> None:
        self._data = pipeline_data
        self._graph = self._build_graph()

    # ------------------------------------------------------------------
    # Graph construction
    # ------------------------------------------------------------------

    def _build_graph(self) -> nx.DiGraph:
        g = nx.DiGraph()
        for node in self._data.nodes:
            g.add_node(node.id, **node.model_dump())
        for edge in self._data.edges:
            g.add_edge(edge.source, edge.target, **edge.model_dump())
        return g

    @property
    def graph(self) -> nx.DiGraph:
        return self._graph

    # ------------------------------------------------------------------
    # Validity checks
    # ------------------------------------------------------------------

    def detect_cycles(self) -> list[list[str]]:
        """Return list of cycles (each cycle is a list of node ids)."""
        try:
            cycles = list(nx.simple_cycles(self._graph))
            return cycles
        except Exception:
            return []

    def detect_isolated_nodes(self) -> list[str]:
        """Nodes with no edges at all."""
        return [n for n in self._graph.nodes if self._graph.degree(n) == 0]

    def detect_unreachable_nodes(self, source_nodes: list[str] | None = None) -> list[str]:
        """Nodes not reachable from any source_nodes (nodes with in-degree 0)."""
        if source_nodes is None:
            source_nodes = [n for n in self._graph.nodes if self._graph.in_degree(n) == 0]
        reachable: set[str] = set()
        for src in source_nodes:
            reachable.update(nx.descendants(self._graph, src))
            reachable.add(src)
        return [n for n in self._graph.nodes if n not in reachable]

    # ------------------------------------------------------------------
    # Traversal queries
    # ------------------------------------------------------------------

    def upstream(self, node_id: str) -> list[str]:
        """All ancestors of node_id."""
        return list(nx.ancestors(self._graph, node_id))

    def downstream(self, node_id: str) -> list[str]:
        """All descendants of node_id."""
        return list(nx.descendants(self._graph, node_id))

    def fanout_downstream(self, buffer_id: str) -> list[str]:
        """Direct successors of a fan-out buffer node."""
        return list(self._graph.successors(buffer_id))

    def nodes_by_layer(self) -> dict[str, list[str]]:
        """Group node ids by their layer attribute."""
        result: dict[str, list[str]] = {k: [] for k in LAYER_Y}
        for node_id, attrs in self._graph.nodes(data=True):
            layer = attrs.get("layer", "hw")
            result.setdefault(layer, []).append(node_id)
        return result

    # ------------------------------------------------------------------
    # Layout computation (x = time, y = layer)
    # ------------------------------------------------------------------

    def compute_layout(self) -> dict[str, dict[str, float]]:
        """Compute (x, y) positions for Cytoscape.js preset layout.

        x-axis: topological generation (relative time, left → right)
        y-axis: layer  (sw=0, hw=300, memory=600)

        Within the same (x, layer) group, nodes are spread vertically
        by Y_STEP to avoid overlap.
        """
        # Handle potential cycles gracefully
        if self.detect_cycles():
            return self._fallback_layout()

        # Assign topological generation (level) to each node
        generations: dict[str, int] = {}
        for level, nodes in enumerate(nx.topological_generations(self._graph)):
            for node_id in nodes:
                generations[node_id] = level

        # Group by (level, layer) to spread y within same column
        from collections import defaultdict
        groups: dict[tuple[int, str], list[str]] = defaultdict(list)
        for node_id, attrs in self._graph.nodes(data=True):
            layer = attrs.get("layer", "hw")
            lvl = generations.get(node_id, 0)
            groups[(lvl, layer)].append(node_id)

        layout: dict[str, dict[str, float]] = {}
        for (lvl, layer), node_ids in groups.items():
            base_x = lvl * X_STEP
            base_y = LAYER_Y.get(layer, 300.0)
            n = len(node_ids)
            for i, node_id in enumerate(node_ids):
                if n == 1 and layer in ("hw", "memory"):
                    # Single node in this (gen, layer) bucket: alternate y by
                    # generation parity so adjacent-generation nodes in the
                    # same layer end up at different y positions.
                    # e.g. GPU (gen 3, odd) at base+40, ISP (gen 4, even) at base-40
                    alt = (Y_STEP / 2) * (1 if lvl % 2 else -1)
                    offset: float = alt
                else:
                    # Multiple nodes in same column/layer: spread symmetrically
                    offset = (i - (n - 1) / 2) * Y_STEP
                layout[node_id] = {"x": base_x, "y": base_y + offset}

        return layout

    def _fallback_layout(self) -> dict[str, dict[str, float]]:
        """Spring layout fallback when cycles exist."""
        pos = nx.spring_layout(self._graph, seed=42)
        scale = 400.0
        return {
            node_id: {"x": float(p[0]) * scale, "y": float(p[1]) * scale}
            for node_id, p in pos.items()
        }
