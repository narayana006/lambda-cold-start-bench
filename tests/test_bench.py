"""The live path, exercised against botocore's Stubber. No request leaves the process."""

import base64
import io

import boto3
import pytest
from botocore.response import StreamingBody
from botocore.stub import ANY, Stubber

from lambda_cold_start_bench.bench import NONCE_VARIABLE, BenchError, make_plan, run_benchmark
from lambda_cold_start_bench.cli import main

FN = "demo-fn"
NAME = {"FunctionName": FN}
ORIGINAL_VARS = {"STAGE": "dev"}


def config(memory=256, status="Successful", variables=ORIGINAL_VARS, **extra):
    result = {"FunctionName": FN, "FunctionArn": f"arn:aws:lambda:us-east-1:123456789012:function:{FN}",
              "Runtime": "python3.13", "MemorySize": memory, "LastUpdateStatus": status, **extra}
    if variables is not None:
        result["Environment"] = {"Variables": variables}
    return result


def invoke_response(memory, init=None, duration=5.0, error=None, log=None):
    line = (f"REPORT RequestId: r-{memory}\tDuration: {duration:.2f} ms\tBilled Duration: {int(duration) + 1} ms\t"
            f"Memory Size: {memory} MB\tMax Memory Used: 40 MB\t")
    if init is not None:
        line += f"Init Duration: {init:.2f} ms\t"
    text = log if log is not None else f"START RequestId: r-{memory} Version: $LATEST\nEND RequestId: r-{memory}\n{line}\n"
    response = {"StatusCode": 200, "LogResult": base64.b64encode(text.encode()).decode(),
                "Payload": StreamingBody(io.BytesIO(b"null"), 4)}
    if error:
        response["FunctionError"] = error
    return response


INVOKE = {"FunctionName": FN, "InvocationType": "RequestResponse", "LogType": "Tail", "Payload": b"{}"}


def update_params(memory, nonce=None, variables=ORIGINAL_VARS):
    merged = dict(variables)
    if nonce is not None:
        merged[NONCE_VARIABLE] = nonce
    return {"FunctionName": FN, "MemorySize": memory, "Environment": {"Variables": merged}}


@pytest.fixture
def stubbed():
    client = boto3.client("lambda", region_name="us-east-1")
    with Stubber(client) as stubber:
        yield client, stubber


def nonces():
    counter = iter(range(1000))
    return lambda: f"n{next(counter)}"


def test_plan_is_read_only(stubbed):
    client, stubber = stubbed
    stubber.add_response("get_function_configuration", config(512), NAME)
    plan = make_plan(client, FN, [256, 1024], count=5, warm=2)
    assert plan.current_memory_mb == 512
    assert plan.memory_sizes == (256, 1024)
    assert plan.configuration_updates == 11
    assert plan.invocations == 30
    assert plan.environment_variable_count == 1
    stubber.assert_no_pending_responses()


def test_run_updates_waits_invokes_and_restores(stubbed):
    client, stubber = stubbed
    stubber.add_response("get_function_configuration", config(256), NAME)
    # first cold start: the update takes one extra poll to finish
    stubber.add_response("update_function_configuration", config(256, "InProgress"), update_params(256, "n0"))
    stubber.add_response("get_function_configuration", config(256, "InProgress"), NAME)
    stubber.add_response("get_function_configuration", config(256), NAME)
    stubber.add_response("invoke", invoke_response(256, init=180.0, duration=20.0), INVOKE)
    stubber.add_response("invoke", invoke_response(256, duration=3.0), INVOKE)
    # second cold start
    stubber.add_response("update_function_configuration", config(256, "InProgress"), update_params(256, "n1"))
    stubber.add_response("get_function_configuration", config(256), NAME)
    stubber.add_response("invoke", invoke_response(256, init=200.0, duration=22.0), INVOKE)
    stubber.add_response("invoke", invoke_response(256, duration=4.0), INVOKE)
    # restore: original memory, original variables, no nonce
    stubber.add_response("update_function_configuration", config(256, "InProgress"), update_params(256))
    stubber.add_response("get_function_configuration", config(256), NAME)

    sleeps = []
    samples = run_benchmark(client, FN, count=2, warm=1, sleep=sleeps.append, nonce=nonces())

    stubber.assert_no_pending_responses()
    assert [(s.forced_cold, s.report.is_cold) for s in samples] == [(True, True), (False, False)] * 2
    assert [s.report.init_duration_ms for s in samples if s.report.is_cold] == [180.0, 200.0]
    assert samples[0].raw_line.startswith("REPORT RequestId: r-256")
    assert sleeps == [1.0]


def test_memory_sizes_are_applied_and_original_is_restored(stubbed):
    client, stubber = stubbed
    stubber.add_response("get_function_configuration", config(512), NAME)
    for index, memory in enumerate((256, 1024)):
        stubber.add_response("update_function_configuration", config(memory), update_params(memory, f"n{index}"))
        stubber.add_response("get_function_configuration", config(memory), NAME)
        stubber.add_response("invoke", invoke_response(memory, init=150.0), INVOKE)
    stubber.add_response("update_function_configuration", config(512), update_params(512))
    stubber.add_response("get_function_configuration", config(512), NAME)
    samples = run_benchmark(client, FN, memory_sizes=[256, 1024], count=1, sleep=lambda s: None, nonce=nonces())
    stubber.assert_no_pending_responses()
    assert [s.memory_size_mb for s in samples] == [256, 1024]
    assert [s.report.memory_size_mb for s in samples] == [256, 1024]


def test_function_without_environment_is_restored_with_no_variables(stubbed):
    client, stubber = stubbed
    stubber.add_response("get_function_configuration", config(128, variables=None), NAME)
    stubber.add_response("update_function_configuration", config(128), update_params(128, "n0", variables={}))
    stubber.add_response("get_function_configuration", config(128), NAME)
    stubber.add_response("invoke", invoke_response(128, init=90.0), INVOKE)
    stubber.add_response("update_function_configuration", config(128), update_params(128, variables={}))
    stubber.add_response("get_function_configuration", config(128), NAME)
    run_benchmark(client, FN, count=1, sleep=lambda s: None, nonce=nonces())
    stubber.assert_no_pending_responses()


def test_configuration_is_restored_when_invoke_fails(stubbed):
    client, stubber = stubbed
    stubber.add_response("get_function_configuration", config(256), NAME)
    stubber.add_response("update_function_configuration", config(256), update_params(256, "n0"))
    stubber.add_response("get_function_configuration", config(256), NAME)
    stubber.add_client_error("invoke", "TooManyRequestsException", "Rate exceeded", expected_params=INVOKE)
    stubber.add_response("update_function_configuration", config(256), update_params(256))
    stubber.add_response("get_function_configuration", config(256), NAME)
    with pytest.raises(client.exceptions.TooManyRequestsException):
        run_benchmark(client, FN, count=3, sleep=lambda s: None, nonce=nonces())
    stubber.assert_no_pending_responses()


def test_failed_update_is_reported_and_restore_is_attempted(stubbed):
    client, stubber = stubbed
    stubber.add_response("get_function_configuration", config(256), NAME)
    stubber.add_response("update_function_configuration", config(256, "InProgress"), update_params(256, "n0"))
    stubber.add_response("get_function_configuration",
                         config(256, "Failed", LastUpdateStatusReason="KMS key is disabled"), NAME)
    stubber.add_response("update_function_configuration", config(256), update_params(256))
    stubber.add_response("get_function_configuration", config(256), NAME)
    with pytest.raises(BenchError, match="KMS key is disabled"):
        run_benchmark(client, FN, count=1, sleep=lambda s: None, nonce=nonces())
    stubber.assert_no_pending_responses()


def test_restore_failure_tells_the_user_what_to_fix(stubbed):
    client, stubber = stubbed
    stubber.add_response("get_function_configuration", config(256), NAME)
    stubber.add_response("update_function_configuration", config(256), update_params(256, "n0"))
    stubber.add_response("get_function_configuration", config(256), NAME)
    stubber.add_response("invoke", invoke_response(256, init=100.0), INVOKE)
    stubber.add_client_error("update_function_configuration", "ResourceConflictException", "update in progress")
    with pytest.raises(BenchError, match=f"256 MB and remove the {NONCE_VARIABLE}"):
        run_benchmark(client, FN, count=1, sleep=lambda s: None, nonce=nonces())


def test_refuses_to_start_when_a_previous_nonce_is_still_set(stubbed):
    client, stubber = stubbed
    stubber.add_response("get_function_configuration", config(256, variables={NONCE_VARIABLE: "old"}), NAME)
    with pytest.raises(BenchError, match="already set"):
        run_benchmark(client, FN, count=1, sleep=lambda s: None)
    stubber.assert_no_pending_responses()  # nothing was updated or invoked


def test_missing_report_line_is_an_error_and_still_restores(stubbed):
    client, stubber = stubbed
    stubber.add_response("get_function_configuration", config(256), NAME)
    stubber.add_response("update_function_configuration", config(256), update_params(256, "n0"))
    stubber.add_response("get_function_configuration", config(256), NAME)
    stubber.add_response("invoke", invoke_response(256, log="just some output\n"), INVOKE)
    stubber.add_response("update_function_configuration", config(256), update_params(256))
    stubber.add_response("get_function_configuration", config(256), NAME)
    with pytest.raises(BenchError, match="REPORT line"):
        run_benchmark(client, FN, count=1, sleep=lambda s: None, nonce=nonces())
    stubber.assert_no_pending_responses()


def cli(argv, client):
    out, err = io.StringIO(), io.StringIO()
    code = main(argv, client_factory=lambda region, profile: client, sleep=lambda s: None, out=out, err=err)
    return code, out.getvalue(), err.getvalue()


def test_cli_run_is_a_dry_run_by_default(stubbed):
    client, stubber = stubbed
    stubber.add_response("get_function_configuration", config(256), NAME)
    code, out, _ = cli(["run", "--function", FN, "--count", "4", "--memory", "256,1024"], client)
    assert code == 0
    assert "Dry run: nothing was changed" in out
    assert "9 configuration updates" in out and "8 invocations" in out
    stubber.assert_no_pending_responses()  # only the one read happened


def test_cli_run_apply_prints_summary_and_saves_log(stubbed, tmp_path):
    client, stubber = stubbed
    stubber.add_response("get_function_configuration", config(256), NAME)
    stubber.add_response("get_function_configuration", config(256), NAME)
    stubber.add_response("update_function_configuration", config(256),
                         {"FunctionName": FN, "MemorySize": 256, "Environment": ANY})
    stubber.add_response("get_function_configuration", config(256), NAME)
    stubber.add_response("invoke", invoke_response(256, init=180.0, duration=20.0, error="Unhandled"), INVOKE)
    stubber.add_response("invoke", invoke_response(256, duration=3.0), INVOKE)
    stubber.add_response("update_function_configuration", config(256), update_params(256))
    stubber.add_response("get_function_configuration", config(256), NAME)
    log = tmp_path / "run.log"
    code, out, err = cli(["run", "--function", FN, "--count", "1", "--warm", "1", "--apply",
                          "--save-log", str(log)], client)
    assert code == 0, err
    assert "256 MB cold: init 180.0 ms duration 20.0 ms [function error: Unhandled]" in out
    assert "Configuration restored to 256 MB" in out
    assert len(log.read_text().splitlines()) == 2
    stubber.assert_no_pending_responses()
    # the saved log can be fed back to analyze
    out2 = io.StringIO()
    assert main(["analyze", str(log)], out=out2) == 0
    assert "2 invocations, 1 cold starts, 1 warm" in out2.getvalue()


def test_cli_warns_when_a_forced_invocation_was_not_cold(stubbed):
    client, stubber = stubbed
    stubber.add_response("get_function_configuration", config(256), NAME)
    stubber.add_response("get_function_configuration", config(256), NAME)
    stubber.add_response("update_function_configuration", config(256))
    stubber.add_response("get_function_configuration", config(256), NAME)
    stubber.add_response("invoke", invoke_response(256, duration=3.0), INVOKE)
    stubber.add_response("update_function_configuration", config(256), update_params(256))
    stubber.add_response("get_function_configuration", config(256), NAME)
    code, _, err = cli(["run", "--function", FN, "--count", "1", "--apply"], client)
    assert code == 0
    assert "reported no Init Duration" in err


def test_cli_reports_aws_errors_without_a_traceback(stubbed):
    client, stubber = stubbed
    stubber.add_client_error("get_function_configuration", "ResourceNotFoundException", "Function not found")
    code, _, err = cli(["run", "--function", FN], client)
    assert code == 1
    assert "Function not found" in err
