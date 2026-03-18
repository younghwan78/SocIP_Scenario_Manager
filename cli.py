"""CLI entry point for the Multimedia Scenario DB toolchain.

Usage:
  python cli.py validate <yaml_path>
  python cli.py render   <yaml_path> [--output <html_path>]
  python cli.py parse-trace <trace_path> [--scenario <name>] [--tp-path <path>] [--output-dir <dir>]
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

logging.basicConfig(format="%(levelname)s: %(message)s", level=logging.INFO)
logger = logging.getLogger(__name__)


def cmd_validate(args: argparse.Namespace) -> int:
    from mmscenario.schema import SchemaValidator, load_full_scenario
    from mmscenario.schema.loader import load_traces

    yaml_path = Path(args.yaml_path)
    if not yaml_path.exists():
        logger.error("File not found: %s", yaml_path)
        return 1

    # Load L0+L1 and auto-merge L2+L3 from traces/ if present
    validator = SchemaValidator()
    result = validator.validate(yaml_path)
    result.print_report()

    traces_dir = yaml_path.parent.parent / "traces"
    l2, l3 = load_traces(traces_dir, yaml_path.stem)
    if l2 or l3:
        logger.info("Loaded traces: %s%s",
                    "L2 " if l2 else "", "L3" if l3 else "")

    return 0 if result.is_valid else 1


def cmd_render(args: argparse.Namespace) -> int:
    from mmscenario.schema import load_full_scenario
    from mmscenario.view import ViewRenderer
    from mmscenario.view.renderer import slugify

    yaml_path = Path(args.yaml_path)
    if not yaml_path.exists():
        logger.error("File not found: %s", yaml_path)
        return 1

    try:
        scenario = load_full_scenario(yaml_path)
    except Exception as exc:
        logger.error("Failed to load scenario: %s", exc)
        return 1

    static_dir = Path(args.static_dir) if hasattr(args, "static_dir") and args.static_dir else Path("static")
    renderer = ViewRenderer(static_dir=static_dir)
    base_slug = slugify(scenario.scenario.name)

    # Determine output directory (project-aware)
    if args.output:
        # --output given: single-file mode (ignores variants)
        from mmscenario.dag import ScenarioPipeline
        output_path = Path(args.output)
        pipeline = ScenarioPipeline(scenario.pipeline)
        cycles = pipeline.detect_cycles()
        if cycles:
            logger.warning("Pipeline has cycles — layout may be incorrect: %s", cycles)
        try:
            renderer.render(scenario, pipeline, output_path=output_path)
        except FileNotFoundError as exc:
            logger.error("%s", exc)
            return 1
        logger.info("HTML written to: %s", output_path.resolve())
    else:
        # Auto output dir: output/<project>/ if scenario is under a project subdir
        rel = yaml_path.relative_to(yaml_path.parent.parent) \
              if yaml_path.parent.name != "usecase" else yaml_path.relative_to(yaml_path.parent)
        # Determine project subfolder
        parts = yaml_path.parts
        project_dir = Path("output")
        for i, p in enumerate(parts):
            if p.lower() == "usecase" and i + 1 < len(parts) - 1:
                project_dir = Path("output") / parts[i + 1]
                break
        try:
            manifest = renderer.render_all_variants(
                scenario, output_dir=project_dir, base_slug=base_slug,
            )
        except FileNotFoundError as exc:
            logger.error("%s", exc)
            return 1
        for m in manifest:
            logger.info("HTML written to: %s", (project_dir / Path(m["html_path"]).name).resolve())
    return 0


def cmd_parse_trace(args: argparse.Namespace) -> int:
    from mmscenario.perfetto import PerfettoParser

    trace_path = Path(args.trace_path)
    if not trace_path.exists():
        logger.error("Trace file not found: %s", trace_path)
        return 1

    tp_path = Path(args.tp_path)
    try:
        parser = PerfettoParser(tp_path)
    except FileNotFoundError as exc:
        logger.error("%s", exc)
        return 1

    scenario_name = args.scenario or trace_path.stem
    result = parser.parse(trace_path, scenario_name)

    print(f"\n=== Detection Results: {scenario_name} ===")
    print(f"Composition mode : {result.composition_mode}")
    print(f"NPU active       : {result.npu_active}")
    print(f"ISP config       : {result.isp_config}")
    for codec in result.codecs:
        print(f"Codec            : {codec.codec_type} ({codec.direction})")
    print("\nActive processes:")
    for layer, procs in sorted(result.active_processes.items()):
        if procs:
            print(f"  [{layer}]: {', '.join(procs[:8])}{'...' if len(procs) > 8 else ''}")
    if result.review_flags:
        print("\n_review_flags (requires manual input):")
        for flag in result.review_flags:
            print(f"  ⚠  {flag}")

    # Write draft YAML
    output_dir = Path(args.output_dir) if args.output_dir else Path("scenarios/usecase")
    output_dir.mkdir(parents=True, exist_ok=True)
    draft_path = output_dir / f"draft_{scenario_name}.yaml"
    draft_yaml = parser.generate_draft_yaml(result)
    draft_path.write_text(draft_yaml, encoding="utf-8")
    logger.info("Draft YAML written to: %s", draft_path.resolve())

    return 0


def cmd_build_site(args: argparse.Namespace) -> int:
    from mmscenario.view.site import build_site

    return build_site(
        usecase_dir=Path(args.usecase_dir),
        output_dir=Path(args.output),
        static_dir=Path(args.static_dir),
        include_all=args.include_all,
    )


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="mmscenario", description="Multimedia Scenario DB CLI")
    sub = p.add_subparsers(dest="command", required=True)

    # validate
    v = sub.add_parser("validate", help="Validate a scenario YAML file")
    v.add_argument("yaml_path", help="Path to scenario YAML")

    # render
    r = sub.add_parser("render", help="Render scenario as interactive HTML")
    r.add_argument("yaml_path", help="Path to scenario YAML")
    r.add_argument("--output", help="Output HTML path (default: output/<scenario-name>.html)")
    r.add_argument("--static-dir", default="static", help="Directory containing cytoscape.min.js")

    # build-site
    bs = sub.add_parser("build-site", help="Build GitHub Pages site from all scenarios")
    bs.add_argument("--usecase-dir",  default="scenarios/usecase",
                    help="Root directory to scan for scenario YAML files (default: scenarios/usecase)")
    bs.add_argument("--output",       default="docs",
                    help="Output directory for generated site (default: docs)")
    bs.add_argument("--static-dir",   default="static",
                    help="Directory containing cytoscape.min.js (default: static)")
    bs.add_argument("--include-all",  action="store_true",
                    help="Include _compact and draft_ files (excluded by default)")

    # parse-trace
    pt = sub.add_parser("parse-trace", help="Parse Perfetto trace → detect components → draft YAML")
    pt.add_argument("trace_path", help="Path to .perfetto-trace file")
    pt.add_argument("--scenario", help="Scenario name for output file")
    pt.add_argument("--tp-path", required=True, help="Path to trace_processor_shell binary")
    pt.add_argument("--output-dir", default="scenarios/usecase", help="Output directory for draft YAML")

    return p


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    handlers = {
        "validate":    cmd_validate,
        "render":      cmd_render,
        "build-site":  cmd_build_site,
        "parse-trace": cmd_parse_trace,
    }
    sys.exit(handlers[args.command](args))


if __name__ == "__main__":
    main()
