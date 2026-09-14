from types import SimpleNamespace

from ai_interviewer_voice.runtimes.nova_sonic.preflight import NovaSonicPreflightService


class FakeStsClient:
    def get_caller_identity(self) -> dict:
        return {
            "Account": "123456789012",
            "Arn": "arn:aws:iam::123456789012:user/test-user",
        }


class FakeBedrockClient:
    def list_foundation_models(self) -> dict:
        return {
            "modelSummaries": [
                {
                    "modelId": "amazon.nova-2-sonic-v1:0",
                    "modelLifecycle": {
                        "status": "ACTIVE",
                    },
                }
            ]
        }


class FakeRuntimeClient:
    async def invoke_model_with_bidirectional_stream(self, input):  # pragma: no cover - surface only
        return SimpleNamespace()


def test_nova_preflight_reports_model_and_runtime_support() -> None:
    service = NovaSonicPreflightService(
        sts_client=FakeStsClient(),
        bedrock_client=FakeBedrockClient(),
        bedrock_runtime_client=FakeRuntimeClient(),
        region_name="ap-northeast-1",
    )

    result = service.run("amazon.nova-2-sonic-v1:0")

    assert result.account_id == "123456789012"
    assert result.model_available is True
    assert result.model_status == "ACTIVE"
    assert result.runtime_operation_available is True
    assert result.runtime_method_available is True
