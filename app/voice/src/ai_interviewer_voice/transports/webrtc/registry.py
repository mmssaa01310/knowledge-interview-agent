from __future__ import annotations

import asyncio


class DuplicatePeerConnectionError(RuntimeError):
    pass


class PeerConnectionRegistry:
    def __init__(self) -> None:
        self._items: dict[str, object] = {}
        self._lock = asyncio.Lock()

    async def create(self, voice_session_id: str, peer_connection: object) -> object:
        async with self._lock:
            if voice_session_id in self._items:
                raise DuplicatePeerConnectionError(voice_session_id)
            self._items[voice_session_id] = peer_connection
            return peer_connection

    async def get(self, voice_session_id: str) -> object | None:
        async with self._lock:
            return self._items.get(voice_session_id)

    async def remove(self, voice_session_id: str) -> object | None:
        async with self._lock:
            return self._items.pop(voice_session_id, None)
