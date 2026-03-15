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
    from mmscenario.schema.models import ScenarioFile


def slugify(name: str) -> str:
    """Convert a scenario name to a safe filename stem.

    "UHD30 Video Recording" → "uhd30_video_recording"
    """
    slug = name.lower()
    slug = re.sub(r"[^a-z0-9]+", "_", slug)
    return slug.strip("_")


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
    ) -> None:
        layout = pipeline.compute_layout()
        elements = build_cytoscape_elements(pipeline, layout)
        scenario_dict = build_scenario_dict(scenario)
        cytoscape_bundle = self._load_cytoscape_bundle()

        template = self._env.get_template("base.html.j2")
        html = template.render(
            scenario=scenario_dict,
            elements=elements,
            cytoscape_bundle=cytoscape_bundle,
            layer_style=LAYER_STYLE,
        )
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(html, encoding="utf-8")

    def _load_cytoscape_bundle(self) -> str:
        bundle_path = self._static_dir / "cytoscape.min.js"
        if not bundle_path.exists():
            raise FileNotFoundError(
                f"cytoscape.min.js not found at {bundle_path}. "
                "Download it from https://unpkg.com/cytoscape/dist/cytoscape.min.js "
                f"and place it at {bundle_path}"
            )
        return bundle_path.read_text(encoding="utf-8")
