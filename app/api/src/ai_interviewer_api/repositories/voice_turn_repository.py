from ai_interviewer_api.repositories.store import store


TABLE = "voice_turns"


def get(turn_id: str) -> dict | None:
    return store.get(TABLE, turn_id)


def save(item: dict) -> dict:
    return store.upsert(TABLE, item)


def list_for_session(tenant_id: str, voice_session_id: str) -> list[dict]:
    return [
        row
        for row in store.list(TABLE, tenant_id)
        if row.get("voiceSessionId") == voice_session_id
    ]
