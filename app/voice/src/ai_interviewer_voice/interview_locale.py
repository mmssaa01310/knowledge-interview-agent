from __future__ import annotations

from dataclasses import dataclass


class UnsupportedVoiceLocaleError(ValueError):
    """Raised when the selected voice provider cannot synthesize the locale."""


@dataclass(frozen=True)
class VoiceLocaleConfig:
    interview_locale: str
    transcribe_language_code: str
    polly_language_code: str
    polly_voice_id: str


_VOICE_LOCALES = {
    "ja-JP": VoiceLocaleConfig(
        interview_locale="ja-JP",
        transcribe_language_code="ja-JP",
        polly_language_code="ja-JP",
        polly_voice_id="Kazuha",
    ),
    "en-US": VoiceLocaleConfig(
        interview_locale="en-US",
        transcribe_language_code="en-US",
        polly_language_code="en-US",
        polly_voice_id="Joanna",
    ),
    "zh-CN": VoiceLocaleConfig(
        interview_locale="zh-CN",
        transcribe_language_code="zh-CN",
        # Amazon Polly uses cmn-CN for Mandarin Chinese.
        polly_language_code="cmn-CN",
        polly_voice_id="Zhiyu",
    ),
    "pt-BR": VoiceLocaleConfig(
        interview_locale="pt-BR",
        transcribe_language_code="pt-BR",
        polly_language_code="pt-BR",
        polly_voice_id="Camila",
    ),
}


def resolve_transcribe_polly_locale(locale: str | None) -> VoiceLocaleConfig:
    normalized = (locale or "ja-JP").strip()
    config = _VOICE_LOCALES.get(normalized)
    if config is None:
        raise UnsupportedVoiceLocaleError(f"Unsupported voice locale: {normalized}")
    return config


def localized_runtime_texts(locale: str) -> dict[str, str]:
    return {
        "ja-JP": {
            "listen_ack": "はい。",
            "processing_ack": "回答を確認しています。",
            "long_processing": "確認に少し時間がかかっています。",
            "timeout": "処理に時間がかかっています。もう一度お願いします。",
            "error": "処理に失敗しました。もう一度お願いします。",
            "unauthorized": "認証を確認できませんでした。セッションを終了します。",
        },
        "en-US": {
            "listen_ack": "Okay.",
            "processing_ack": "I am checking your answer.",
            "long_processing": "This is taking a little longer than expected.",
            "timeout": "This is taking a while. Please try again.",
            "error": "Something went wrong. Please try again.",
            "unauthorized": "I could not verify the session. The session will end.",
        },
        "zh-CN": {
            "listen_ack": "好的。",
            "processing_ack": "我正在确认您的回答。",
            "long_processing": "确认时间比预想的稍长。",
            "timeout": "处理需要一些时间。请再试一次。",
            "error": "处理失败。请再试一次。",
            "unauthorized": "无法验证身份。会话将结束。",
        },
        "pt-BR": {
            "listen_ack": "Certo.",
            "processing_ack": "Estou verificando sua resposta.",
            "long_processing": "Isso está demorando um pouco mais do que o esperado.",
            "timeout": "O processamento está demorando. Tente novamente.",
            "error": "O processamento falhou. Tente novamente.",
            "unauthorized": "Não foi possível verificar a sessão. A sessão será encerrada.",
        },
    }[locale]
