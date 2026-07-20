from __future__ import annotations

from dataclasses import dataclass

import boto3
from botocore.client import BaseClient
from aws_sdk_bedrock_runtime.client import BedrockRuntimeClient
from aws_sdk_bedrock_runtime.config import Config as BedrockRuntimeConfig


@dataclass(frozen=True)
class NovaSonicPreflightResult:
    account_id: str
    caller_arn: str
    region_name: str
    model_id: str
    model_available: bool
    model_status: str | None
    runtime_operation_available: bool
    runtime_method_available: bool


class NovaSonicPreflightService:
    def __init__(
        self,
        *,
        sts_client: BaseClient | None = None,
        bedrock_client: BaseClient | None = None,
        bedrock_runtime_client: BedrockRuntimeClient | None = None,
        region_name: str = "ap-northeast-1",
    ) -> None:
        self._region_name = region_name
        self._sts_client = sts_client or boto3.client("sts", region_name=region_name)
        self._bedrock_client = bedrock_client or boto3.client("bedrock", region_name=region_name)
        self._bedrock_runtime_client = bedrock_runtime_client or BedrockRuntimeClient(
            BedrockRuntimeConfig(region=region_name),
        )

    def run(self, model_id: str) -> NovaSonicPreflightResult:
        caller = self._sts_client.get_caller_identity()
        model_summaries = self._bedrock_client.list_foundation_models().get("modelSummaries", [])
        matched = next((item for item in model_summaries if item.get("modelId") == model_id), None)

        return NovaSonicPreflightResult(
            account_id=str(caller.get("Account") or ""),
            caller_arn=str(caller.get("Arn") or ""),
            region_name=self._region_name,
            model_id=model_id,
            model_available=matched is not None,
            model_status=(matched or {}).get("modelLifecycle", {}).get("status"),
            runtime_operation_available=hasattr(
                self._bedrock_runtime_client,
                "invoke_model_with_bidirectional_stream",
            ),
            runtime_method_available=hasattr(
                self._bedrock_runtime_client,
                "invoke_model_with_bidirectional_stream",
            ),
        )
