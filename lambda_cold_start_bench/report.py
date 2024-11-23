"""Parse the REPORT line Lambda writes at the end of every invocation.

Two shapes are understood:

* the plain-text line, e.g.
  ``REPORT RequestId: <id>\tDuration: 3.21 ms\tBilled Duration: 4 ms\t
  Memory Size: 256 MB\tMax Memory Used: 71 MB\tInit Duration: 187.45 ms``
* the JSON ``platform.report`` record written when a function uses the JSON
  log format.

``Init Duration`` is only present when the invocation ran in a freshly
initialised execution environment, so its presence is what marks a cold start.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Iterable, Iterator, Optional

_REQUEST_ID = re.compile(r"REPORT RequestId:\s*(\S+)")
_NUMBER = r"([0-9]+(?:\.[0-9]+)?)"
_DURATION = re.compile(r"(?<!Billed )(?<!Init )(?<!Restore )Duration:\s*" + _NUMBER + r"\s*ms")
_BILLED = re.compile(r"Billed Duration:\s*" + _NUMBER + r"\s*ms")
_MEMORY_SIZE = re.compile(r"Memory Size:\s*" + _NUMBER + r"\s*MB")
_MAX_MEMORY = re.compile(r"Max Memory Used:\s*" + _NUMBER + r"\s*MB")
_INIT = re.compile(r"Init Duration:\s*" + _NUMBER + r"\s*ms")
_STATUS = re.compile(r"Status:\s*(\w+)")


@dataclass(frozen=True)
class Report:
    """One invocation, as described by its REPORT line."""

    request_id: str
    duration_ms: float
    billed_duration_ms: float
    memory_size_mb: int
    max_memory_used_mb: int
    init_duration_ms: Optional[float] = None
    status: Optional[str] = None

    @property
    def is_cold(self) -> bool:
        return self.init_duration_ms is not None


def _parse_text(line: str) -> Optional[Report]:
    request = _REQUEST_ID.search(line)
    if not request:
        return None
    rest = line[request.end():]
    duration = _DURATION.search(rest)
    billed = _BILLED.search(rest)
    size = _MEMORY_SIZE.search(rest)
    used = _MAX_MEMORY.search(rest)
    if not (duration and billed and size and used):
        return None
    init = _INIT.search(rest)
    status = _STATUS.search(rest)
    return Report(
        request_id=request.group(1),
        duration_ms=float(duration.group(1)),
        billed_duration_ms=float(billed.group(1)),
        memory_size_mb=int(float(size.group(1))),
        max_memory_used_mb=int(float(used.group(1))),
        init_duration_ms=float(init.group(1)) if init else None,
        status=status.group(1) if status else None,
    )


def _parse_json(line: str) -> Optional[Report]:
    start = line.find("{")
    if start < 0:
        return None
    try:
        doc = json.loads(line[start:])
    except ValueError:
        return None
    if not isinstance(doc, dict) or doc.get("type") != "platform.report":
        return None
    record = doc.get("record")
    metrics = record.get("metrics") if isinstance(record, dict) else None
    if not isinstance(metrics, dict):
        return None
    try:
        init = metrics.get("initDurationMs")
        return Report(
            request_id=str(record.get("requestId", "")),
            duration_ms=float(metrics["durationMs"]),
            billed_duration_ms=float(metrics["billedDurationMs"]),
            memory_size_mb=int(metrics["memorySizeMB"]),
            max_memory_used_mb=int(metrics["maxMemoryUsedMB"]),
            init_duration_ms=float(init) if init is not None else None,
            status=record.get("status"),
        )
    except (KeyError, TypeError, ValueError):
        return None


def parse_line(line: str) -> Optional[Report]:
    """Return a Report for a REPORT line, or None for any other line.

    The line may carry a prefix (a timestamp or log stream name), as it does
    in output from ``aws logs tail`` or a console export.
    """
    if "REPORT RequestId:" in line:
        return _parse_text(line)
    if "platform.report" in line:
        return _parse_json(line)
    return None


def parse_lines(lines: Iterable[str]) -> Iterator[Report]:
    for line in lines:
        report = parse_line(line)
        if report is not None:
            yield report
