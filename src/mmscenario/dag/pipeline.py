"""DAG processing for L1 pipeline data.

Layout axes (Perfetto timeline philosophy):
  x-axis = relative time  (topological order, left → right)
  y-axis = layer          (SW top / HW middle / Memory/Buffer bottom)

Grid layout policy
------------------
Within a (topological-level, layer) column, nodes are placed in a
column-first grid when count > MAX_GRID_ROWS.  This bounds the vertical
span of any layer to at most (MAX_GRID_ROWS - 1) * Y_STEP regardless of
how many nodes share the same level, preventing layer overflow.

Dynamic layer spacing
---------------------
LAYER_Y centres are computed at render time from the actual row counts so
layers are packed as tightly as possible while never overlapping.
"""

from __future__ import annotations

import math
from collections import defaultdict
from typing import TYPE_CHECKING

import networkx as nx

if TYPE_CHECKING:
    from mmscenario.schema.models import L1Pipeline

# ── Layer stacking order ───────────────────────────────────────────────────
LAYER_ORDER: list[str] = ["app", "framework", "hal", "kernel", "hw", "memory"]

# Layers after which a larger visual gap is inserted (SW→HW, HW→Memory)
_MAJOR_GAP_AFTER: frozenset[str] = frozenset({"kernel", "hw"})

# ── Layout constants ───────────────────────────────────────────────────────
X_STEP: float = 200.0      # horizontal gap between topological levels
X_GRID_STEP: float = 90.0  # x gap between sub-columns within one grid cell
Y_STEP: float = 80.0       # vertical gap between rows within a layer

MAX_GRID_ROWS: int = 3     # rows before expanding to multi-column grid

# When a column group has multiple sub-columns, odd-numbered sub-columns are
# shifted down by Y_COL_STAGGER so horizontally-adjacent nodes never share the
# same y.  This separates arrow paths that would otherwise overlap on the
# same horizontal line.  Value ≈ half node height (node height = 40 px).
Y_COL_STAGGER: float = 20.0

LAYER_GAP_SW: float = 80.0     # centre-to-centre addition between SW sublayers
LAYER_GAP_MAJOR: float = 130.0 # addition at SW→HW and HW→Memory boundaries


# ── Grid helpers ───────────────────────────────────────────────────────────

def _grid_dims(n: int) -> tuple[int, int]:
    """Return *(n_cols, n_rows)* for a grid holding *n* nodes.

    Columns are added only when rows would exceed MAX_GRID_ROWS, so the
    vertical extent of any column is always ≤ (MAX_GRID_ROWS - 1) * Y_STEP.

    Examples (MAX_GRID_ROWS=3):
        n=1 → (1, 1)   n=2 → (1, 2)   n=3 → (1, 3)
        n=4 → (2, 2)   n=5 → (2, 3)   n=6 → (2, 3)
        n=7 → (3, 3)   n=9 → (3, 3)
    """
    if n <= MAX_GRID_ROWS:
        return 1, n
    n_cols = math.ceil(n / MAX_GRID_ROWS)
    n_rows = math.ceil(n / n_cols)
    return n_cols, n_rows


def _layer_half_height(max_rows: int) -> float:
    """Vertical half-span (px) of a layer band with *max_rows* rows."""
    return (max_rows - 1) / 2.0 * Y_STEP


# ── Dynamic LAYER_Y computation ────────────────────────────────────────────

def _compute_layer_y(
    groups: dict[tuple[int, str], list[str]],
) -> dict[str, float]:
    """Return per-layer Y centre positions derived from actual node counts.

    Algorithm
    ---------
    1. For every (level, layer) group compute grid row count.
    2. Per layer: take the maximum row count across all levels → band height.
    3. Stack layers top-to-bottom using LAYER_GAP_SW / LAYER_GAP_MAJOR between
       adjacent band centres, ensuring no two bands overlap.
    """
    # ── max rows per layer ────────────────────────────────────────────────
    layer_max_rows: dict[str, int] = {L: 1 for L in LAYER_ORDER}
    for (_, layer), nodes in groups.items():
        if layer in layer_max_rows:
            _, rows = _grid_dims(len(nodes))
            layer_max_rows[layer] = max(layer_max_rows[layer], rows)

    # ── stack layers from y = 0 downward ─────────────────────────────────
    layer_y: dict[str, float] = {}
    prev: str | None = None
    for layer in LAYER_ORDER:
        if prev is None:
            layer_y[layer] = 0.0
        else:
            gap = LAYER_GAP_MAJOR if prev in _MAJOR_GAP_AFTER else LAYER_GAP_SW
            layer_y[layer] = (
                layer_y[prev]
                + _layer_half_height(layer_max_rows[prev])
                + gap
                + _layer_half_height(layer_max_rows[layer])
            )
        prev = layer

    return layer_y


# ── Main pipeline class ────────────────────────────────────────────────────

class ScenarioPipeline:
    """networkx-backed DAG for an L1 pipeline."""

    def __init__(self, pipeline_data: "L1Pipeline") -> None:
        self._data = pipeline_data
        self._graph = self._build_graph()
        self._layer_y: dict[str, float] = {L: float(i * 160) for i, L in enumerate(LAYER_ORDER)}

    @property
    def layer_y(self) -> dict[str, float]:
        """Per-layer Y centres last computed by compute_layout().

        Populated after the first call to compute_layout(); returns a
        reasonable static fallback before that.
        """
        return self._layer_y

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
            return list(nx.simple_cycles(self._graph))
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
        result: dict[str, list[str]] = {k: [] for k in LAYER_ORDER}
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
        y-axis: layer  (app top … memory bottom)

        Within a (level, layer) group nodes are placed in a grid:
        - up to MAX_GRID_ROWS nodes → single column, spread vertically
        - more nodes → additional sub-columns spaced X_GRID_STEP apart

        Layer Y centres are computed dynamically so no layer ever overflows
        into an adjacent band regardless of node count.
        """
        if self.detect_cycles():
            return self._fallback_layout()

        # ── Step 1: topological generation → x position ───────────────
        generations: dict[str, int] = {}
        for level, nodes in enumerate(nx.topological_generations(self._graph)):
            for node_id in nodes:
                generations[node_id] = level

        # ── Step 2: group by (level, layer) ───────────────────────────
        groups: dict[tuple[int, str], list[str]] = defaultdict(list)
        for node_id, attrs in self._graph.nodes(data=True):
            layer = attrs.get("layer", "hw")
            lvl = generations.get(node_id, 0)
            groups[(lvl, layer)].append(node_id)

        # ── Step 3: dynamic layer Y centres ───────────────────────────
        layer_y = _compute_layer_y(groups)
        self._layer_y = layer_y  # cache for callers (e.g. renderer)

        # ── Step 4: place nodes in grid cells ─────────────────────────
        # Grid fills column-first (top of left column → bottom of left column
        # → top of next column …) which reads naturally left-to-right.
        layout: dict[str, dict[str, float]] = {}
        layer_nodes_all: dict[str, list[str]] = defaultdict(list)
        for (lvl, layer), node_ids in groups.items():
            base_x = lvl * X_STEP
            base_y = layer_y.get(layer, 0.0)
            n_cols, n_rows = _grid_dims(len(node_ids))

            for i, node_id in enumerate(node_ids):
                col = i // n_rows
                row = i % n_rows
                x = base_x + (col - (n_cols - 1) / 2.0) * X_GRID_STEP
                # Odd sub-columns are shifted down by Y_COL_STAGGER so nodes
                # that share the same grid row never sit on the same y-line,
                # keeping arrow paths visually separated.
                stagger = (col % 2) * Y_COL_STAGGER if n_cols > 1 else 0.0
                y = base_y + (row - (n_rows - 1) / 2.0) * Y_STEP + stagger
                layout[node_id] = {"x": x, "y": y}
            layer_nodes_all[layer].extend(node_ids)

        # ── Step 5: stagger same-y nodes across different x positions ─
        # Nodes that belong to single-node columns in the same layer all
        # land on the exact same y (layer centre).  Arrows between such nodes
        # overlap on one horizontal line.  Sort each y-group by x and apply
        # an alternating +Y_COL_STAGGER offset so consecutive nodes are never
        # on the same horizontal line.
        for layer, nids in layer_nodes_all.items():
            # Group nodes by their current rounded y value
            by_y: dict[int, list[str]] = defaultdict(list)
            for nid in nids:
                by_y[round(layout[nid]["y"])].append(nid)
            for same_y_nodes in by_y.values():
                if len(same_y_nodes) < 2:
                    continue
                # Sort by x so the stagger alternates left-to-right
                same_y_nodes.sort(key=lambda n: layout[n]["x"])
                for i, nid in enumerate(same_y_nodes):
                    layout[nid]["y"] += (i % 2) * Y_COL_STAGGER

        return layout

    def _fallback_layout(self) -> dict[str, dict[str, float]]:
        """Spring layout fallback when cycles exist."""
        pos = nx.spring_layout(self._graph, seed=42)
        scale = 400.0
        return {
            node_id: {"x": float(p[0]) * scale, "y": float(p[1]) * scale}
            for node_id, p in pos.items()
        }
