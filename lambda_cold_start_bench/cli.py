"""Command line interface."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any, Callable, Optional, Sequence

from . import __version__
from .bench import BenchError, NONCE_VARIABLE, make_plan, run_benchmark
from .output import render_svg, render_table, write_csv
from .report import Report, parse_lines
from .stats import GroupStats, group_stats


def _labelled_path(argument: str) -> tuple[str, Path]:
    """``label=path`` or just ``path`` (the label is then the file name without suffix)."""
    if "=" in argument and not Path(argument).exists():
        label, _, path = argument.partition("=")
        return label, Path(path)
    return Path(argument).stem, Path(argument)


def _memory_list(argument: str) -> list[int]:
    try:
        sizes = [int(part) for part in argument.split(",") if part.strip()]
    except ValueError:
        raise argparse.ArgumentTypeError("expected comma-separated whole numbers of MB, e.g. 256,1024")
    for size in sizes:
        if not 128 <= size <= 10240:
            raise argparse.ArgumentTypeError(f"{size} is outside Lambda's 128-10240 MB range")
    return sizes


def _write_outputs(groups: Sequence[GroupStats], csv_path: Optional[str], chart_path: Optional[str], out) -> None:
    if csv_path:
        with open(csv_path, "w", newline="", encoding="utf-8") as handle:
            write_csv(groups, handle)
        print(f"wrote {csv_path}", file=out)
    if chart_path:
        Path(chart_path).write_text(render_svg(groups), encoding="utf-8")
        print(f"wrote {chart_path}", file=out)


def _analyze(args, out, err) -> int:
    labelled: list[tuple[str, Report]] = []
    for argument in args.logs:
        label, path = _labelled_path(argument)
        try:
            with open(path, encoding="utf-8", errors="replace") as handle:
                reports = list(parse_lines(handle))
        except OSError as error:
            print(f"error: cannot read {path}: {error.strerror}", file=err)
            return 2
        if not reports:
            print(f"warning: no REPORT lines found in {path}", file=err)
        labelled.extend((label, report) for report in reports)
    if not labelled:
        print("error: no REPORT lines found in any input", file=err)
        return 1
    groups = sorted(group_stats(labelled), key=lambda g: (g.label, g.memory_size_mb))
    cold = sum(g.cold_count for g in groups)
    print(f"{len(labelled)} invocations, {cold} cold starts, {len(labelled) - cold} warm. Times in ms.\n", file=out)
    print(render_table(groups), file=out)
    _write_outputs(groups, args.csv, args.chart, out)
    return 0


def _default_client_factory(region: Optional[str], profile: Optional[str]) -> Any:
    try:
        import boto3
    except ImportError as error:
        raise BenchError('the "run" command needs boto3: pip install boto3') from error
    return boto3.Session(region_name=region, profile_name=profile).client("lambda")


def _run(args, out, err, client_factory, sleep) -> int:
    payload = b"{}"
    if args.payload_file:
        payload = Path(args.payload_file).read_bytes()
    client = client_factory(args.region, args.profile)
    plan = make_plan(client, args.function, args.memory or (), args.count, args.warm)
    sizes = ", ".join(f"{size} MB" for size in plan.memory_sizes)
    print(f"Function:        {plan.function_name} ({plan.runtime})", file=out)
    print(f"ARN:             {plan.function_arn}", file=out)
    print(f"Current memory:  {plan.current_memory_mb} MB", file=out)
    print(f"Memory sizes:    {sizes}", file=out)
    print(f"Plan:            {plan.configuration_updates} configuration updates "
          f"(the last one restores the original), {plan.invocations} invocations", file=out)
    print(f"Each update sets MemorySize and rewrites the function's {plan.environment_variable_count} "
          f"environment variables plus {NONCE_VARIABLE}.", file=out)
    if not args.apply:
        print("\nDry run: nothing was changed and the function was not invoked. "
              "Re-run with --apply to run the benchmark.", file=out)
        return 0

    print("", file=out)

    def progress(sample) -> None:
        kind = "cold" if sample.report.is_cold else "warm"
        init = f" init {sample.report.init_duration_ms:.1f} ms" if sample.report.is_cold else ""
        failed = f" [function error: {sample.function_error}]" if sample.function_error else ""
        print(f"  {sample.memory_size_mb} MB {kind}:{init} duration {sample.report.duration_ms:.1f} ms{failed}", file=out)

    kwargs = {"sleep": sleep} if sleep else {}
    samples = run_benchmark(client, args.function, args.memory or (), args.count, args.warm,
                            payload, on_sample=progress, **kwargs)
    print(f"\nConfiguration restored to {plan.current_memory_mb} MB with the original environment variables.\n", file=out)

    not_cold = sum(1 for s in samples if s.forced_cold and not s.report.is_cold)
    if not_cold:
        print(f"warning: {not_cold} invocation(s) right after a configuration update reported no "
              "Init Duration, so they are counted as warm.", file=err)
    if args.save_log:
        Path(args.save_log).write_text("".join(s.raw_line + "\n" for s in samples), encoding="utf-8")
        print(f"wrote {args.save_log}", file=out)
    groups = sorted(group_stats((plan.function_name, s.report) for s in samples),
                    key=lambda g: g.memory_size_mb)
    print(render_table(groups), file=out)
    _write_outputs(groups, args.csv, args.chart, out)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="lambda-cold-start-bench",
        description="Measure AWS Lambda cold starts from REPORT log lines.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    commands = parser.add_subparsers(dest="command", required=True)

    analyze = commands.add_parser(
        "analyze", help="summarise REPORT lines from log files (offline, no AWS access)")
    analyze.add_argument("logs", nargs="+", metavar="[LABEL=]FILE",
                         help="log file containing REPORT lines; the label defaults to the file name")
    analyze.add_argument("--csv", metavar="PATH", help="also write the summary as CSV")
    analyze.add_argument("--chart", metavar="PATH", help="also write an SVG chart of Init Duration")

    run = commands.add_parser(
        "run", help="force cold starts on an existing function (dry run unless --apply)")
    run.add_argument("--function", required=True, help="function name or ARN ($LATEST is benchmarked)")
    run.add_argument("--count", type=int, default=10, help="cold starts per memory size (default 10)")
    run.add_argument("--memory", type=_memory_list, metavar="MB[,MB...]",
                     help="memory sizes to compare; default is the function's current size only")
    run.add_argument("--warm", type=int, default=0,
                     help="extra warm invocations after each cold start (default 0)")
    run.add_argument("--payload-file", metavar="PATH", help="JSON event to send (default {})")
    run.add_argument("--apply", action="store_true",
                     help="really update the configuration and invoke the function")
    run.add_argument("--save-log", metavar="PATH", help="write the collected REPORT lines to a file")
    run.add_argument("--csv", metavar="PATH", help="also write the summary as CSV")
    run.add_argument("--chart", metavar="PATH", help="also write an SVG chart of Init Duration")
    run.add_argument("--region", help="AWS region (default: from your AWS configuration)")
    run.add_argument("--profile", help="AWS profile name (default: default credential chain)")
    return parser


def main(argv: Optional[Sequence[str]] = None,
         client_factory: Callable[[Optional[str], Optional[str]], Any] = _default_client_factory,
         sleep: Optional[Callable[[float], None]] = None,
         out=None, err=None) -> int:
    out = out or sys.stdout
    err = err or sys.stderr
    args = build_parser().parse_args(argv)
    if args.command == "analyze":
        return _analyze(args, out, err)
    if args.count < 1 or args.warm < 0:
        print("error: --count must be at least 1 and --warm cannot be negative", file=err)
        return 2
    try:
        return _run(args, out, err, client_factory, sleep)
    except BenchError as error:
        print(f"error: {error}", file=err)
        return 1
    except Exception as error:  # botocore errors: show the message, not a traceback
        if type(error).__module__.startswith("botocore"):
            print(f"error: {error}", file=err)
            return 1
        raise


if __name__ == "__main__":
    sys.exit(main())
