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
    "memory":    980.0,   # pushed down for HW↔buffer clearance
}

X_STEP = 200.0   # horizontal gap between topological levels
Y_STEP = 80.0    # vertical gap between nodes in the same layer

# Post-processing constants
Y_MEM_STEP = 60.0   # y spacing between sorted memory (buffer) nodes
Y_HW_ALT   = 40.0   # alternation offset for HW nodes (±)


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
        y-axis: layer  (app=0 … kernel=480 … hw=680 … memory=980)

        Within the same (x, layer) column, nodes are spread vertically by
        Y_STEP to avoid overlap.

        Post-processing:
        - memory layer: nodes sorted by x get unique, evenly-spaced y values
          (Y_MEM_STEP apart) so taxi-routed edges never share the same y as
          intermediate buffer nodes → eliminates the main crossing artifact.
        - hw layer: nodes sorted by x get alternating ±Y_HW_ALT offsets to
          break the "all IPs on the same horizontal line" pattern.
        """
        if self.detect_cycles():
            return self._fallback_layout()

        # ── Step 1: topological generation → x position ───────────────
        generations: dict[str, int] = {}
        for level, nodes in enumerate(nx.topological_generations(self._graph)):
            for node_id in nodes:
                generations[node_id] = level

        # ── Step 2: group by (level, layer); spread multi-node columns ─
        from collections import defaultdict
        groups: dict[tuple[int, str], list[str]] = defaultdict(list)
        for node_id, attrs in self._graph.nodes(data=True):
            layer = attrs.get("layer", "hw")
            lvl = generations.get(node_id, 0)
            groups[(lvl, layer)].append(node_id)

        layout: dict[str, dict[str, float]] = {}
        layer_nodes_map: dict[str, list[str]] = defaultdict(list)
        for (lvl, layer), node_ids in groups.items():
            base_x = lvl * X_STEP
            base_y = LAYER_Y.get(layer, 300.0)
            n = len(node_ids)
            for i, node_id in enumerate(node_ids):
                offset = (i - (n - 1) / 2) * Y_STEP
                layout[node_id] = {"x": base_x, "y": base_y + offset}
            layer_nodes_map[layer].extend(node_ids)

        # ── Step 3: post-process memory layer ─────────────────────────
        # Sort all buffer nodes by (x, current_y) and assign unique y
        # values centred on LAYER_Y["memory"].  With each buffer at a
        # distinct y, horizontal taxi segments can no longer share a y
        # level with another buffer node → the key crossing artifact gone.
        mem_nodes = layer_nodes_map.get("memory", [])
        if mem_nodes:
            sorted_mem = sorted(mem_nodes,
                                key=lambda n: (layout[n]["x"], layout[n]["y"]))
            n = len(sorted_mem)
            base_y = LAYER_Y["memory"]
            for i, node_id in enumerate(sorted_mem):
                layout[node_id]["y"] = base_y + (i - (n - 1) / 2) * Y_MEM_STEP

        # ── Step 4: post-process hw layer ─────────────────────────────
        # When all HW IPs land on even topological generations they all
        # get the same y.  Alternating ±Y_HW_ALT by sorted-x position
        # staggers them visually without violating layer boundaries.
        hw_nodes = layer_nodes_map.get("hw", [])
        if hw_nodes:
            sorted_hw = sorted(hw_nodes,
                               key=lambda n: (layout[n]["x"], layout[n]["y"]))
            base_y = LAYER_Y["hw"]
            for i, node_id in enumerate(sorted_hw):
                alt = Y_HW_ALT * (1 if i % 2 else -1)
                layout[node_id]["y"] = base_y + alt

        return layout

    def _fallback_layout(self) -> dict[str, dict[str, float]]:
        """Spring layout fallback when cycles exist."""
        pos = nx.spring_layout(self._graph, seed=42)
        scale = 400.0
        return {
            node_id: {"x": float(p[0]) * scale, "y": float(p[1]) * scale}
            for node_id, p in pos.items()
        }
