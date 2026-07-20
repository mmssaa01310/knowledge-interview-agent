from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import boto3


@dataclass(frozen=True)
class IceServer:
    urls: tuple[str, ...]
    username: str | None = None
    credential: str | None = None


@dataclass(frozen=True)
class IceServerConfig:
    ice_servers: tuple[IceServer, ...]
    expires_at: datetime


class IceServerService:
    def __init__(
        self,
        *,
        aws_region: str,
        kvs_turn_channel_arn: str | None = None,
        cache_ttl_seconds: float = 240.0,
    ) -> None:
        self._aws_region = aws_region
        self._kvs_turn_channel_arn = kvs_turn_channel_arn
        self._cache_ttl_seconds = cache_ttl_seconds
        self._cached_config: IceServerConfig | None = None

    async def get_ice_servers(self) -> IceServerConfig:
        now = datetime.now(UTC)
        if self._cached_config is not None and self._cached_config.expires_at > now:
            return self._cached_config

        servers = [
            IceServer(
                urls=(f"stun:stun.kinesisvideo.{self._aws_region}.amazonaws.com:443",),
            )
        ]
        expires_at = now + timedelta(seconds=min(self._cache_ttl_seconds, 240.0))

        if self._kvs_turn_channel_arn:
            turn_servers, turn_expiry = await self._load_turn_servers()
            servers.extend(turn_servers)
            expires_at = min(expires_at, turn_expiry)

        config = IceServerConfig(
            ice_servers=tuple(servers),
            expires_at=expires_at,
        )
        self._cached_config = config
        return config

    async def _load_turn_servers(self) -> tuple[list[IceServer], datetime]:
        kvs = boto3.client("kinesisvideo", region_name=self._aws_region)
        endpoint_response = kvs.get_signaling_channel_endpoint(
            ChannelARN=self._kvs_turn_channel_arn,
            SingleMasterChannelEndpointConfiguration={
                "Protocols": ["TURN"],
                "Role": "MASTER",
            },
        )
        endpoint = None
        for item in endpoint_response.get("ResourceEndpointList", []):
            if item.get("Protocol") == "TURN":
                endpoint = item.get("ResourceEndpoint")
                break
        if endpoint is None:
            return [], datetime.now(UTC) + timedelta(seconds=60)

        signaling = boto3.client(
            "kinesis-video-signaling",
            region_name=self._aws_region,
            endpoint_url=endpoint,
        )
        response = signaling.get_ice_server_config(ChannelARN=self._kvs_turn_channel_arn)
        ttl_seconds = float(response.get("IceServerList", [{}])[0].get("Ttl") or 300)
        expires_at = datetime.now(UTC) + timedelta(seconds=min(ttl_seconds, self._cache_ttl_seconds))
        servers: list[IceServer] = []
        for item in response.get("IceServerList", []):
            uris = tuple(str(uri) for uri in item.get("Uris", []))
            if not uris:
                continue
            servers.append(
                IceServer(
                    urls=uris,
                    username=item.get("Username"),
                    credential=item.get("Password"),
                )
            )
        return servers, expires_at
