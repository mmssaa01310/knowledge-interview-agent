from ai_interviewer_voice.runtimes.transcribe_polly import transcribe_stream
from ai_interviewer_voice.runtimes.transcribe_polly.config import (
    TranscribePollyRuntimeConfig,
)
from ai_interviewer_voice.runtimes.nova_sonic import sdk_client


def test_resolve_bedrock_runtime_credentials_uses_frozen_credentials(monkeypatch) -> None:
    class FakeCredentials:
        def get_frozen_credentials(self):
            class Frozen:
                access_key = "test-access-key"
                secret_key = "test-secret-key"
                token = "test-session-token"

            return Frozen()

    class FakeSession:
        def __init__(self, *, region_name=None) -> None:
            self.region_name = region_name

        def get_credentials(self):
            return FakeCredentials()

    monkeypatch.setattr(sdk_client.boto3, "Session", FakeSession)

    resolved = sdk_client.resolve_bedrock_runtime_credentials("ap-northeast-1")

    assert resolved.access_key_id == "test-access-key"
    assert resolved.secret_access_key == "test-secret-key"
    assert resolved.session_token == "test-session-token"


def test_resolve_bedrock_runtime_credentials_fails_when_unavailable(monkeypatch) -> None:
    class FakeSession:
        def __init__(self, *, region_name=None) -> None:
            self.region_name = region_name

        def get_credentials(self):
            return None

    monkeypatch.setattr(sdk_client.boto3, "Session", FakeSession)

    try:
        sdk_client.resolve_bedrock_runtime_credentials("ap-northeast-1")
    except sdk_client.BedrockCredentialsResolutionError as exc:
        assert str(exc) == "AWS credentials could not be resolved"
    else:  # pragma: no cover - assertion path
        raise AssertionError("Expected BedrockCredentialsResolutionError")


def test_create_transcribe_client_uses_frozen_credentials_and_resolver(monkeypatch) -> None:
    class FakeCredentials:
        def get_frozen_credentials(self):
            class Frozen:
                access_key = "test-access-key"
                secret_key = "test-secret-key"
                token = "test-session-token"

            return Frozen()

    class FakeSession:
        def __init__(self, *, region_name=None) -> None:
            self.region_name = region_name

        def get_credentials(self):
            return FakeCredentials()

    transport = object()
    resolver = object()
    captured = {}

    class FakeTranscribeStreamingClient:
        def __init__(self, config) -> None:
            captured["config"] = config

    monkeypatch.setattr(transcribe_stream.boto3, "Session", FakeSession)
    monkeypatch.setattr(transcribe_stream, "AWSCRTHTTPClient", lambda: transport)
    monkeypatch.setattr(
        transcribe_stream,
        "create_default_chain",
        lambda actual_transport: resolver,
    )
    monkeypatch.setattr(
        transcribe_stream,
        "TranscribeStreamingClient",
        FakeTranscribeStreamingClient,
    )

    config = TranscribePollyRuntimeConfig()
    client = transcribe_stream._create_transcribe_client(config)

    assert isinstance(client, FakeTranscribeStreamingClient)
    sdk_config = captured["config"]
    assert sdk_config.region == config.aws_region
    assert sdk_config.transport is transport
    assert sdk_config.aws_access_key_id == "test-access-key"
    assert sdk_config.aws_secret_access_key == "test-secret-key"
    assert sdk_config.aws_session_token == "test-session-token"
    assert sdk_config.aws_credentials_identity_resolver is resolver
