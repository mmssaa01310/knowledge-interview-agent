from dataclasses import dataclass


@dataclass
class ElasticsearchClient:
    url: str

    def health(self) -> dict[str, str]:
        return {"status": "ok", "backend": "memory" if self.url.startswith("memory://") else self.url}


client = ElasticsearchClient(url="memory://local")
