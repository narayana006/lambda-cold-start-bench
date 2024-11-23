import pytest

from lambda_cold_start_bench.report import Report
from lambda_cold_start_bench.stats import group_stats, percentile, summarize


def test_percentile_interpolates():
    values = [10, 20, 30, 40]
    assert percentile(values, 0) == 10
    assert percentile(values, 100) == 40
    assert percentile(values, 50) == 25
    assert percentile(values, 90) == pytest.approx(37)
    assert percentile([7], 99) == 7


def test_percentile_does_not_need_sorted_input():
    assert percentile([40, 10, 30, 20], 50) == 25


def test_percentile_rejects_bad_input():
    with pytest.raises(ValueError):
        percentile([], 50)
    with pytest.raises(ValueError):
        percentile([1], 101)


def test_summarize():
    summary = summarize([1, 2, 3, 4, 5])
    assert (summary.n, summary.minimum, summary.maximum, summary.mean, summary.p50) == (5, 1, 5, 3, 3)
    assert summarize([]) is None


def _report(memory, duration, init=None, used=50):
    return Report("id", duration, int(duration) + 1, memory, used, init)


def test_group_stats_splits_cold_and_warm_per_label_and_memory():
    labelled = [
        ("a", _report(256, 20, init=180)),
        ("a", _report(256, 30, init=220, used=64)),
        ("a", _report(256, 5)),
        ("a", _report(1024, 9, init=170)),
        ("b", _report(256, 4)),
    ]
    groups = {(g.label, g.memory_size_mb): g for g in group_stats(labelled)}
    assert set(groups) == {("a", 256), ("a", 1024), ("b", 256)}
    a256 = groups[("a", 256)]
    assert a256.invocations == 3
    assert a256.cold_count == 2 and a256.warm_count == 1
    assert a256.init.p50 == 200
    assert a256.cold_duration.mean == 25
    assert a256.warm_duration.p50 == 5
    assert a256.max_memory_used_mb == 64
    only_warm = groups[("b", 256)]
    assert only_warm.init is None and only_warm.cold_count == 0
