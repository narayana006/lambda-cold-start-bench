"""Render group statistics as a text table, CSV and an SVG bar chart."""

from __future__ import annotations

import csv
from html import escape
from typing import IO, Optional, Sequence

from .stats import GroupStats, Summary

CSV_FIELDS = [
    "label", "memory_size_mb", "invocations", "cold_starts",
    "init_min_ms", "init_mean_ms", "init_p50_ms", "init_p90_ms", "init_p99_ms", "init_max_ms",
    "cold_duration_p50_ms", "cold_duration_p90_ms", "cold_billed_mean_ms",
    "warm_invocations", "warm_duration_p50_ms", "warm_duration_p90_ms", "warm_billed_mean_ms",
    "max_memory_used_mb",
]


def _fmt(value: Optional[float]) -> str:
    return "-" if value is None else f"{value:.1f}"


def _get(summary: Optional[Summary], field: str) -> Optional[float]:
    return getattr(summary, field) if summary else None


def render_table(groups: Sequence[GroupStats]) -> str:
    header = ["label", "mem MB", "cold n", "init p50", "init p90", "init p99", "init max",
              "cold dur p50", "warm n", "warm dur p50", "warm dur p90", "max mem MB"]
    rows = [header]
    for g in groups:
        rows.append([
            g.label, str(g.memory_size_mb), str(g.cold_count),
            _fmt(_get(g.init, "p50")), _fmt(_get(g.init, "p90")),
            _fmt(_get(g.init, "p99")), _fmt(_get(g.init, "maximum")),
            _fmt(_get(g.cold_duration, "p50")), str(g.warm_count),
            _fmt(_get(g.warm_duration, "p50")), _fmt(_get(g.warm_duration, "p90")),
            str(g.max_memory_used_mb),
        ])
    widths = [max(len(row[i]) for row in rows) for i in range(len(header))]
    lines = []
    for index, row in enumerate(rows):
        cells = [row[0].ljust(widths[0])] + [c.rjust(w) for c, w in zip(row[1:], widths[1:])]
        lines.append("  ".join(cells).rstrip())
        if index == 0:
            lines.append("  ".join("-" * w for w in widths))
    return "\n".join(lines)


def write_csv(groups: Sequence[GroupStats], handle: IO[str]) -> None:
    writer = csv.writer(handle, lineterminator="\n")
    writer.writerow(CSV_FIELDS)

    def num(value: Optional[float]) -> str:
        return "" if value is None else f"{value:.2f}"

    for g in groups:
        writer.writerow([
            g.label, g.memory_size_mb, g.invocations, g.cold_count,
            num(_get(g.init, "minimum")), num(_get(g.init, "mean")), num(_get(g.init, "p50")),
            num(_get(g.init, "p90")), num(_get(g.init, "p99")), num(_get(g.init, "maximum")),
            num(_get(g.cold_duration, "p50")), num(_get(g.cold_duration, "p90")),
            num(_get(g.cold_billed, "mean")),
            g.warm_count, num(_get(g.warm_duration, "p50")), num(_get(g.warm_duration, "p90")),
            num(_get(g.warm_billed, "mean")),
            g.max_memory_used_mb,
        ])


def render_svg(groups: Sequence[GroupStats]) -> str:
    """Horizontal bars: Init Duration p50 (dark) and p90 (light) per group."""
    charted = [g for g in groups if g.init]
    left, bar_area, row_height, top = 230, 420, 44, 50
    width = left + bar_area + 90
    height = top + row_height * max(len(charted), 1) + 20
    scale_max = max((g.init.p90 for g in charted), default=1.0) or 1.0
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
        f'viewBox="0 0 {width} {height}" font-family="Helvetica, Arial, sans-serif" font-size="12">',
        f'<rect width="{width}" height="{height}" fill="#ffffff"/>',
        '<text x="12" y="20" font-size="14" font-weight="bold" fill="#1f2933">'
        'Lambda Init Duration (ms)</text>',
        f'<rect x="{left}" y="28" width="12" height="10" fill="#1f6feb"/>'
        f'<text x="{left + 16}" y="37" fill="#1f2933">p50</text>'
        f'<rect x="{left + 50}" y="28" width="12" height="10" fill="#9ec1f7"/>'
        f'<text x="{left + 66}" y="37" fill="#1f2933">p90</text>',
    ]
    if not charted:
        parts.append(f'<text x="12" y="{top + 20}" fill="#52606d">No cold starts in the input.</text>')
    for index, g in enumerate(charted):
        y = top + index * row_height
        name = escape(f"{g.label} @ {g.memory_size_mb} MB (n={g.cold_count})")
        parts.append(f'<text x="{left - 8}" y="{y + 20}" text-anchor="end" fill="#1f2933">{name}</text>')
        for offset, value, colour in ((0, g.init.p50, "#1f6feb"), (16, g.init.p90, "#9ec1f7")):
            bar = max(1.0, bar_area * value / scale_max)
            parts.append(f'<rect x="{left}" y="{y + 4 + offset}" width="{bar:.1f}" height="14" fill="{colour}"/>')
            parts.append(f'<text x="{left + bar + 6:.1f}" y="{y + 15 + offset}" fill="#1f2933">{value:.1f}</text>')
    parts.append("</svg>")
    return "\n".join(parts) + "\n"
