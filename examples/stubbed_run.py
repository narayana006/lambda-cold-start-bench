"""Show what `lambda-cold-start-bench run --apply` prints, without an AWS account.

The Lambda client is a real boto3 client wrapped in botocore's Stubber, so
every API call is answered from the canned responses below and nothing is sent
over the network. The timings are made up; they only demonstrate the output.

    python examples/stubbed_run.py
"""

import base64
import io
import os
import sys

import boto3
from botocore.response import StreamingBody
from botocore.stub import ANY, Stubber

from lambda_cold_start_bench.cli import main

FUNCTION = "demo-fn"
ARN = "arn:aws:lambda:us-east-1:123456789012:function:demo-fn"
SIZES = (256, 1024)
COUNT = 3
INIT_MS = {256: (187.42, 179.80, 201.13), 1024: (172.05, 168.91, 175.66)}
DURATION_MS = {256: (23.10, 21.84, 25.02), 1024: (8.73, 9.12, 8.40)}


def configuration(memory, status="Successful"):
    return {
        "FunctionName": FUNCTION,
        "FunctionArn": ARN,
        "Runtime": "python3.13",
        "MemorySize": memory,
        "Environment": {"Variables": {"STAGE": "dev", "TABLE_NAME": "demo-table"}},
        "LastUpdateStatus": status,
    }


def invoke_response(memory, index):
    request_id = f"00000000-0000-4000-8000-{memory:08d}{index:04d}"
    duration = DURATION_MS[memory][index]
    report = (
        f"START RequestId: {request_id} Version: $LATEST\n"
        f"END RequestId: {request_id}\n"
        f"REPORT RequestId: {request_id}\tDuration: {duration:.2f} ms\t"
        f"Billed Duration: {int(duration) + 1} ms\tMemory Size: {memory} MB\t"
        f"Max Memory Used: 49 MB\tInit Duration: {INIT_MS[memory][index]:.2f} ms\t\n"
    )
    return {
        "StatusCode": 200,
        "LogResult": base64.b64encode(report.encode()).decode(),
        "Payload": StreamingBody(io.BytesIO(b"null"), 4),
    }


def stubbed_client():
    os.environ.update(AWS_ACCESS_KEY_ID="testing", AWS_SECRET_ACCESS_KEY="testing",
                      AWS_SESSION_TOKEN="testing")
    client = boto3.client("lambda", region_name="us-east-1")
    stubber = Stubber(client)
    name = {"FunctionName": FUNCTION}
    stubber.add_response("get_function_configuration", configuration(256), name)  # plan
    stubber.add_response("get_function_configuration", configuration(256), name)  # original
    for memory in SIZES:
        for index in range(COUNT):
            stubber.add_response(
                "update_function_configuration", configuration(memory, "InProgress"),
                {"FunctionName": FUNCTION, "MemorySize": memory, "Environment": ANY})
            stubber.add_response("get_function_configuration", configuration(memory, "InProgress"), name)
            stubber.add_response("get_function_configuration", configuration(memory), name)
            stubber.add_response("invoke", invoke_response(memory, index), {
                "FunctionName": FUNCTION, "InvocationType": "RequestResponse",
                "LogType": "Tail", "Payload": b"{}"})
    stubber.add_response(
        "update_function_configuration", configuration(256, "InProgress"),
        {"FunctionName": FUNCTION, "MemorySize": 256,
         "Environment": {"Variables": {"STAGE": "dev", "TABLE_NAME": "demo-table"}}})
    stubber.add_response("get_function_configuration", configuration(256), name)
    stubber.activate()
    return client


if __name__ == "__main__":
    client = stubbed_client()
    sys.exit(main(
        ["run", "--function", FUNCTION, "--count", str(COUNT), "--memory", "256,1024", "--apply"],
        client_factory=lambda region, profile: client,
        sleep=lambda seconds: None,
    ))
