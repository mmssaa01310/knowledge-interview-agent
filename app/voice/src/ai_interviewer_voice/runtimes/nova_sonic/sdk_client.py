import asyncio
from dataclasses import dataclass
from typing import Any

import boto3
from botocore.credentials import ReadOnlyCredentials
from aws_sdk_bedrock_runtime.client import (
    BedrockRuntimeClient,
    InvokeModelWithBidirectionalStreamOperationInput,
)
from aws_sdk_bedrock_runtime.config import Config as BedrockRuntimeConfig
from aws_sdk_bedrock_runtime.models import BidirectionalInputPayloadPart
from aws_sdk_bedrock_runtime.models import InvokeModelWithBidirectionalStreamInputChunk
from smithy_aws_core.identity.chain import create_default_chain
from smithy_http.aio.crt import AWSCRTHTTPClient

from ai_interviewer_voice.runtimes.nova_sonic.protocol.payloads import dumps_event_payload


@dataclass(frozen=True)
class ResolvedBedrockCredentials:
    access_key_id: str
    secret_access_key: str
    session_token: str | None


class BedrockCredentialsResolutionError(RuntimeError):
    pass


def resolve_bedrock_runtime_credentials(region_name: str) -> ResolvedBedrockCredentials:
    session = boto3.Session(region_name=region_name)
    credentials = session.get_credentials()
    if credentials is None:
        raise BedrockCredentialsResolutionError("AWS credentials could not be resolved")
    frozen_credentials: ReadOnlyCredentials = credentials.get_frozen_credentials()
    return ResolvedBedrockCredentials(
        access_key_id=frozen_credentials.access_key,
        secret_access_key=frozen_credentials.secret_key,
        session_token=frozen_credentials.token,
    )


def create_bedrock_runtime_client(
    region_name: str,
    credentials: ResolvedBedrockCredentials | None = None,
) -> BedrockRuntimeClient:
    resolved_credentials = credentials or resolve_bedrock_runtime_credentials(region_name)
    transport = AWSCRTHTTPClient()
    return BedrockRuntimeClient(
        BedrockRuntimeConfig(
            region=region_name,
            transport=transport,
            aws_access_key_id=resolved_credentials.access_key_id,
            aws_secret_access_key=resolved_credentials.secret_access_key,
            aws_session_token=resolved_credentials.session_token,
            aws_credentials_identity_resolver=create_default_chain(transport),
        ),
    )


def build_bidirectional_stream_input(model_id: str) -> InvokeModelWithBidirectionalStreamOperationInput:
    return InvokeModelWithBidirectionalStreamOperationInput(model_id=model_id)


async def open_bidirectional_stream(
    client: BedrockRuntimeClient,
    *,
    model_id: str,
    timeout_seconds: float,
):
    return await asyncio.wait_for(
        client.invoke_model_with_bidirectional_stream(build_bidirectional_stream_input(model_id)),
        timeout=timeout_seconds,
    )


async def send_payload(stream: Any, payload: dict[str, Any]) -> None:
    await stream.input_stream.send(build_json_input_chunk(payload))


async def close_stream(stream: Any) -> None:
    await stream.close()


def build_json_input_chunk(payload: dict[str, Any]) -> InvokeModelWithBidirectionalStreamInputChunk:
    return InvokeModelWithBidirectionalStreamInputChunk(
        value=BidirectionalInputPayloadPart(
            bytes_=dumps_event_payload(payload)
        ),
    )
