import csv
import io
from pathlib import Path

from lambda_cold_start_bench.cli import main

EXAMPLES = Path(__file__).resolve().parent.parent / "examples"


def run(argv):
    out, err = io.StringIO(), io.StringIO()
    code = main(argv, out=out, err=err)
    return code, out.getvalue(), err.getvalue()


def test_analyze_example_logs(tmp_path):
    csv_path, chart_path = tmp_path / "out.csv", tmp_path / "out.svg"
    code, out, _ = run([
        "analyze",
        str(EXAMPLES / "python3.13-256mb.log"),
        str(EXAMPLES / "python3.13-1024mb.log"),
        str(EXAMPLES / "nodejs22.x-256mb.log"),
        "--csv", str(csv_path), "--chart", str(chart_path),
    ])
    assert code == 0
    assert "144 invocations, 36 cold starts, 108 warm" in out
    assert "python3.13-1024mb" in out and "nodejs22.x-256mb" in out
    rows = list(csv.DictReader(csv_path.open()))
    assert [(r["label"], r["memory_size_mb"], r["cold_starts"]) for r in rows] == [
        ("nodejs22.x-256mb", "256", "12"),
        ("python3.13-1024mb", "1024", "12"),
        ("python3.13-256mb", "256", "12"),
    ]
    assert float(rows[0]["init_p50_ms"]) <= float(rows[0]["init_p90_ms"]) <= float(rows[0]["init_max_ms"])
    svg = chart_path.read_text()
    assert svg.startswith("<svg") and svg.count("<rect") == 3 + 2 * 3
    assert "python3.13-256mb @ 256 MB (n=12)" in svg


def test_label_can_be_given_explicitly(tmp_path):
    log = tmp_path / "x.log"
    log.write_text("REPORT RequestId: a\tDuration: 2.00 ms\tBilled Duration: 2 ms\t"
                   "Memory Size: 128 MB\tMax Memory Used: 30 MB\tInit Duration: 99.00 ms\n")
    code, out, _ = run(["analyze", f"my-fn={log}"])
    assert code == 0
    assert "my-fn" in out and "99.0" in out


def test_warm_only_input_shows_dashes(tmp_path):
    log = tmp_path / "warm.log"
    log.write_text("REPORT RequestId: a\tDuration: 2.00 ms\tBilled Duration: 2 ms\t"
                   "Memory Size: 128 MB\tMax Memory Used: 30 MB\n")
    code, out, _ = run(["analyze", str(log), "--chart", str(tmp_path / "c.svg")])
    assert code == 0
    assert "1 invocations, 0 cold starts, 1 warm" in out
    assert "No cold starts" in (tmp_path / "c.svg").read_text()


def test_no_report_lines_is_an_error(tmp_path):
    log = tmp_path / "empty.log"
    log.write_text("hello\n")
    code, _, err = run(["analyze", str(log)])
    assert code == 1
    assert "no REPORT lines" in err


def test_missing_file(tmp_path):
    code, _, err = run(["analyze", str(tmp_path / "nope.log")])
    assert code == 2
    assert "cannot read" in err
