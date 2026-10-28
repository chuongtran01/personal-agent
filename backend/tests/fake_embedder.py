"""An embedder that needs no API key: the same text always gets the same vector."""

import hashlib
import math

from app.config import settings


class FakeEmbedder:
    def __init__(self, dimensions: int = 0):
        self.dimensions = dimensions or settings.embedding_dimensions
        self.calls: list[list[str]] = []

    async def embed(self, texts: list[str]) -> list[list[float]]:
        self.calls.append(list(texts))
        return [self._vector(text) for text in texts]

    def _vector(self, text: str) -> list[float]:
        values: list[float] = []
        counter = 0
        while len(values) < self.dimensions:
            digest = hashlib.sha256(f"{counter}:{text}".encode()).digest()
            values.extend(byte / 255 - 0.5 for byte in digest)
            counter += 1
        values = values[: self.dimensions]
        norm = math.sqrt(sum(v * v for v in values))
        return [v / norm for v in values]
