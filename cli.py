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
    from mmscenario.dag import ScenarioPipeline
    from mmscenario.schema import load_full_scenario
    from mmscenario.view import ViewRenderer
    from mmscenario.view.renderer import slugify

    yaml_path = Path(args.yaml_path)
    if not yaml_path.exists():
        logger.error("File not found: %s", yaml_path)
        return 1

    try:
        # Load L0+L1 and auto-merge L2+L3 from traces/ if present
        scenario = load_full_scenario(yaml_path)
    except Exception as exc:
        logger.error("Failed to load scenario: %s", exc)
        return 1

    # Output filename derived from scenario name defined in YAML
    if args.output:
        output_path = Path(args.output)
    else:
        output_path = Path("output") / f"{slugify(scenario.scenario.name)}.html"

    pipeline = ScenarioPipeline(scenario.pipeline)
    cycles = pipeline.detect_cycles()
    if cycles:
        logger.warning("Pipeline has cycles — layout may be incorrect: %s", cycles)

    static_dir = Path(args.static_dir) if hasattr(args, "static_dir") and args.static_dir else Path("static")
    renderer = ViewRenderer(static_dir=static_dir)
    try:
        renderer.render(scenario, pipeline, output_path=output_path)
    except FileNotFoundError as exc:
        logger.error("%s", exc)
        return 1

    logger.info("HTML written to: %s", output_path.resolve())
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
        "parse-trace": cmd_parse_trace,
    }
    sys.exit(handlers[args.command](args))


if __name__ == "__main__":
    main()
