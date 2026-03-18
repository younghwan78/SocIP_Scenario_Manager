"""HTML view renderer using Jinja2 + Cytoscape.js.

Layout: x = relative time (topological order), y = layer (SW/HW/Buffer).
Cytoscape.js is embedded inline from static/cytoscape.min.js.

Data preparation (models → Cytoscape elements) is handled by data_prep.py.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import TYPE_CHECKING

from jinja2 import Environment, FileSystemLoader, select_autoescape

from .data_prep import LAYER_STYLE, build_cytoscape_elements, build_scenario_dict

if TYPE_CHECKING:
    from mmscenario.dag.pipeline import ScenarioPipeline
    from mmscenario.schema.models import ScenarioFile, ScenarioVariant


def slugify(name: str) -> str:
    """Convert a scenario name to a safe filename stem.

    "UHD30 Video Recording" → "uhd30_video_recording"
    """
    slug = name.lower()
    slug = re.sub(r"[^a-z0-9]+", "_", slug)
    return slug.strip("_")


def _apply_variant(scenario: "ScenarioFile", variant: "ScenarioVariant") -> "ScenarioFile":
    """Return a new ScenarioFile with variant overrides applied (original unchanged)."""
    # L0 timing
    sc_updates: dict = {}
    if variant.output_period_ms is not None:
        sc_updates["output_period_ms"] = variant.output_period_ms
    if variant.budget_ms is not None:
        sc_updates["budget_ms"] = variant.budget_ms

    # Buffer node overrides (label, compression, llc, rotation, ...)
    nodes = [
        n.model_copy(update=variant.buffers[n.id]) if n.id in variant.buffers else n
        for n in scenario.pipeline.nodes
    ]

    # Edge overrides (format, resolution, fps, fan_out)
    edges = [
        e.model_copy(update=variant.edges[e.id]) if e.id in variant.edges else e
        for e in scenario.pipeline.edges
    ]

    return scenario.model_copy(update={
        "scenario": scenario.scenario.model_copy(update=sc_updates) if sc_updates else scenario.scenario,
        "pipeline": scenario.pipeline.model_copy(update={"nodes": nodes, "edges": edges}),
    })


class ViewRenderer:
    def __init__(self, static_dir: Path | None = None, template_dir: Path | None = None) -> None:
        base = Path(__file__).parent
        self._static_dir = static_dir or (Path.cwd() / "static")
        template_path = template_dir or (base / "templates")
        self._env = Environment(
            loader=FileSystemLoader(str(template_path)),
            autoescape=select_autoescape(["html"]),
        )
        self._env.filters["tojson"] = lambda v, **kw: json.dumps(v, ensure_ascii=False, **kw)

    def render(
        self,
        scenario: "ScenarioFile",
        pipeline: "ScenarioPipeline",
        output_path: Path,
        active_variant: dict | None = None,
        all_variants: list[dict] | None = None,
    ) -> None:
        """Render one HTML page.

        active_variant: {"id": ..., "name": ...} for the currently rendered variant.
        all_variants:   [{"id":..., "name":..., "html_path":...}, ...] for the nav dropdown.
        """
        layout = pipeline.compute_layout()
        elements = build_cytoscape_elements(pipeline, layout, scenario.dpu_compositions or None)
        scenario_dict = build_scenario_dict(scenario)
        cytoscape_bundle = self._load_cytoscape_bundle()

        template = self._env.get_template("base.html.j2")
        html = template.render(
            scenario=scenario_dict,
            elements=elements,
            cytoscape_bundle=cytoscape_bundle,
            layer_style=LAYER_STYLE,
            active_variant=active_variant,
            all_variants=all_variants or [],
        )
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(html, encoding="utf-8")

    def render_all_variants(
        self,
        scenario: "ScenarioFile",
        output_dir: Path,
        base_slug: str,
        variant_html_dir: str = "",
    ) -> list[dict]:
        """Render one HTML per variant and return variant manifest list.

        Returns: [{"id": ..., "name": ..., "html_path": ...}, ...]
        variant_html_dir: relative prefix for html_path in manifest (e.g. "scenarios/projectA")
        """
        from mmscenario.dag.pipeline import ScenarioPipeline

        if not scenario.variants:
            pipeline = ScenarioPipeline(scenario.pipeline)
            slug_html = f"{base_slug}.html"
            out = output_dir / slug_html
            self.render(scenario, pipeline, output_path=out)
            rel = f"{variant_html_dir}/{slug_html}".lstrip("/")
            return [{"id": "", "name": scenario.scenario.name, "html_path": rel}]

        manifest: list[dict] = []
        # Build full variant nav list first (for dropdown links)
        def _variant_filename(vid: str) -> str:
            return f"{base_slug}_{slugify(vid)}.html"

        all_variants_nav = [
            {
                "id": v.id,
                "name": v.name,
                "html_path": (f"{variant_html_dir}/{_variant_filename(v.id)}").lstrip("/"),
            }
            for v in scenario.variants
        ]

        for variant in scenario.variants:
            applied = _apply_variant(scenario, variant)
            # IP activity: select the mode specified by ip_modes for each IP
            if applied.ip_activity and variant.ip_modes:
                updated_ips = []
                for spec in applied.ip_activity.ip_instances:
                    mode_id = variant.ip_modes.get(spec.id, "default")
                    active_mode = spec.get_mode(mode_id)
                    # Attach active mode as the 'active_mode' attribute via a wrapper
                    updated_ips.append(spec)
                # Store ip_modes in scenario dict via active_variant info
            pipeline = ScenarioPipeline(applied.pipeline)
            fname = _variant_filename(variant.id)
            out = output_dir / fname
            active_v = {"id": variant.id, "name": variant.name,
                        "ip_modes": variant.ip_modes}
            self.render(
                applied, pipeline, output_path=out,
                active_variant=active_v,
                all_variants=all_variants_nav,
            )
            manifest.append(all_variants_nav[[v.id for v in scenario.variants].index(variant.id)])

        return manifest

    def _load_cytoscape_bundle(self) -> str:
        bundle_path = self._static_dir / "cytoscape.min.js"
        if not bundle_path.exists():
            raise FileNotFoundError(
                f"cytoscape.min.js not found at {bundle_path}. "
                "Download it from https://unpkg.com/cytoscape/dist/cytoscape.min.js "
                f"and place it at {bundle_path}"
            )
        return bundle_path.read_text(encoding="utf-8")
