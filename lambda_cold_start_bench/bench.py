"""Force cold starts on an existing Lambda function and collect REPORT lines.

How a cold start is forced: the function's configuration is updated with a
throwaway environment variable (and, when memory sizes are being compared, a
different MemorySize). Lambda discards the existing execution environments of
``$LATEST`` after a configuration change, so the next invocation has to
initialise a new one. The original memory size and environment variables are
written back when the run ends, including when it ends with an error.

Every function here takes the Lambda client as an argument; nothing in this
module creates one.
"""

from __future__ import annotations

import base64
import time
import uuid
from dataclasses import dataclass
from typing import Any, Callable, Optional, Sequence

from .report import Report, parse_line

NONCE_VARIABLE = "COLD_START_BENCH_NONCE"


class BenchError(Exception):
    pass


@dataclass(frozen=True)
class Plan:
    function_name: str
    function_arn: str
    runtime: str
    current_memory_mb: int
    environment_variable_count: int
    memory_sizes: tuple[int, ...]
    count: int
    warm: int

    @property
    def configuration_updates(self) -> int:
        return len(self.memory_sizes) * self.count + 1  # +1 restores the original

    @property
    def invocations(self) -> int:
        return len(self.memory_sizes) * self.count * (1 + self.warm)


@dataclass(frozen=True)
class Sample:
    memory_size_mb: int
    forced_cold: bool   # True for the first invocation after a configuration update
    report: Report
    raw_line: str
    function_error: Optional[str] = None


def make_plan(client: Any, function_name: str, memory_sizes: Sequence[int] = (),
              count: int = 10, warm: int = 0) -> Plan:
    """Read the function's configuration and describe what a run would do.

    Makes one read-only call (GetFunctionConfiguration).
    """
    config = client.get_function_configuration(FunctionName=function_name)
    sizes = tuple(memory_sizes) or (config["MemorySize"],)
    return Plan(
        function_name=config.get("FunctionName", function_name),
        function_arn=config.get("FunctionArn", ""),
        runtime=config.get("Runtime") or config.get("PackageType", "unknown"),
        current_memory_mb=config["MemorySize"],
        environment_variable_count=len(config.get("Environment", {}).get("Variables", {})),
        memory_sizes=sizes,
        count=count,
        warm=warm,
    )


def _wait_until_updated(client: Any, function_name: str, sleep: Callable[[float], None],
                        poll_interval: float, max_polls: int) -> None:
    for _ in range(max_polls):
        config = client.get_function_configuration(FunctionName=function_name)
        status = config.get("LastUpdateStatus", "Successful")
        if status == "Successful":
            return
        if status == "Failed":
            reason = config.get("LastUpdateStatusReason", "no reason given")
            raise BenchError(f"configuration update failed: {reason}")
        sleep(poll_interval)
    raise BenchError(f"configuration update did not finish after {max_polls} checks")


def _invoke(client: Any, function_name: str, payload: bytes, memory: int, forced_cold: bool) -> Sample:
    response = client.invoke(
        FunctionName=function_name,
        InvocationType="RequestResponse",
        LogType="Tail",
        Payload=payload,
    )
    body = response.get("Payload")
    if hasattr(body, "read"):
        body.read()
    tail = base64.b64decode(response.get("LogResult", "")).decode("utf-8", "replace")
    found = [(parse_line(line), line) for line in tail.splitlines()]
    found = [(report, line) for report, line in found if report is not None]
    if not found:
        raise BenchError("the invocation's log tail did not contain a REPORT line")
    report, line = found[-1]
    return Sample(memory, forced_cold, report, line.strip(), response.get("FunctionError"))


def run_benchmark(
    client: Any,
    function_name: str,
    memory_sizes: Sequence[int] = (),
    count: int = 10,
    warm: int = 0,
    payload: bytes = b"{}",
    sleep: Callable[[float], None] = time.sleep,
    nonce: Callable[[], str] = lambda: uuid.uuid4().hex,
    poll_interval: float = 1.0,
    max_polls: int = 120,
    on_sample: Optional[Callable[[Sample], None]] = None,
) -> list[Sample]:
    """Run the benchmark. This changes the function's configuration and invokes it.

    For each memory size, ``count`` times: update the configuration, wait for
    the update to finish, invoke once (the forced cold start), then invoke
    ``warm`` more times. The original MemorySize and environment variables are
    restored at the end.
    """
    original = client.get_function_configuration(FunctionName=function_name)
    original_memory = original["MemorySize"]
    original_variables = dict(original.get("Environment", {}).get("Variables", {}))
    if NONCE_VARIABLE in original_variables:
        raise BenchError(
            f"{NONCE_VARIABLE} is already set on {function_name}; a previous run may not have "
            "restored the configuration. Remove the variable and try again."
        )
    sizes = tuple(memory_sizes) or (original_memory,)
    samples: list[Sample] = []
    changed = False
    try:
        for memory in sizes:
            for _ in range(count):
                changed = True
                client.update_function_configuration(
                    FunctionName=function_name,
                    MemorySize=memory,
                    Environment={"Variables": {**original_variables, NONCE_VARIABLE: nonce()}},
                )
                _wait_until_updated(client, function_name, sleep, poll_interval, max_polls)
                for index in range(1 + warm):
                    sample = _invoke(client, function_name, payload, memory, forced_cold=index == 0)
                    samples.append(sample)
                    if on_sample:
                        on_sample(sample)
    finally:
        if changed:
            try:
                client.update_function_configuration(
                    FunctionName=function_name,
                    MemorySize=original_memory,
                    Environment={"Variables": original_variables},
                )
                _wait_until_updated(client, function_name, sleep, poll_interval, max_polls)
            except Exception as error:
                raise BenchError(
                    f"could not restore {function_name}: {error}. Set MemorySize back to "
                    f"{original_memory} MB and remove the {NONCE_VARIABLE} environment variable by hand."
                ) from error
    return samples
