import json

from lambda_cold_start_bench.report import parse_line, parse_lines

COLD = ("REPORT RequestId: 8f5a2c1e-1111-4222-8333-444455556666\tDuration: 23.10 ms\t"
        "Billed Duration: 24 ms\tMemory Size: 256 MB\tMax Memory Used: 49 MB\tInit Duration: 187.42 ms\t")
WARM = ("REPORT RequestId: 8f5a2c1e-1111-4222-8333-444455557777\tDuration: 1.95 ms\t"
        "Billed Duration: 2 ms\tMemory Size: 256 MB\tMax Memory Used: 50 MB\t")


def test_cold_line():
    report = parse_line(COLD)
    assert report.request_id == "8f5a2c1e-1111-4222-8333-444455556666"
    assert report.duration_ms == 23.10
    assert report.billed_duration_ms == 24
    assert report.memory_size_mb == 256
    assert report.max_memory_used_mb == 49
    assert report.init_duration_ms == 187.42
    assert report.is_cold


def test_warm_line_has_no_init_duration():
    report = parse_line(WARM)
    assert report.init_duration_ms is None
    assert not report.is_cold
    assert report.duration_ms == 1.95


def test_duration_is_not_confused_with_billed_or_init_duration():
    line = ("REPORT RequestId: abc\tInit Duration: 300.00 ms\tBilled Duration: 8 ms\t"
            "Duration: 7.50 ms\tMemory Size: 128 MB\tMax Memory Used: 40 MB")
    report = parse_line(line)
    assert report.duration_ms == 7.50
    assert report.billed_duration_ms == 8
    assert report.init_duration_ms == 300.0


def test_prefix_and_spaces_instead_of_tabs():
    line = "2026-01-05T10:00:00.123Z 2026/01/05/[$LATEST]abcd " + COLD.replace("\t", "  ")
    report = parse_line(line)
    assert report.init_duration_ms == 187.42
    assert report.memory_size_mb == 256


def test_xray_fields_and_timeout_status():
    line = ("REPORT RequestId: abc\tDuration: 3000.00 ms\tBilled Duration: 3000 ms\t"
            "Memory Size: 128 MB\tMax Memory Used: 36 MB\tStatus: timeout\t"
            "XRAY TraceId: 1-5d9d007f-0a8c7fd02xmpl480aed55ef0\tSegmentId: 3d752xmpl1bbe37e\tSampled: true")
    report = parse_line(line)
    assert report.status == "timeout"
    assert report.duration_ms == 3000.0


def test_other_lines_are_ignored():
    assert parse_line("START RequestId: abc Version: $LATEST") is None
    assert parse_line("END RequestId: abc") is None
    assert parse_line("") is None
    assert parse_line("REPORT RequestId: abc\tDuration: oops") is None


def test_json_platform_report():
    record = {
        "time": "2026-01-05T10:00:00.123Z",
        "type": "platform.report",
        "record": {
            "requestId": "abc",
            "status": "success",
            "metrics": {"durationMs": 12.5, "billedDurationMs": 13, "memorySizeMB": 512,
                        "maxMemoryUsedMB": 80, "initDurationMs": 240.7},
        },
    }
    report = parse_line(json.dumps(record))
    assert report.request_id == "abc"
    assert report.init_duration_ms == 240.7
    assert report.memory_size_mb == 512
    del record["record"]["metrics"]["initDurationMs"]
    assert not parse_line(json.dumps(record)).is_cold
    assert parse_line('{"type": "platform.report", "record": {}}') is None
    assert parse_line('{"type": "platform.report"') is None


def test_parse_lines_keeps_order():
    reports = list(parse_lines(["START RequestId: x", COLD, "noise", WARM]))
    assert [r.is_cold for r in reports] == [True, False]
