"""An embedder whose vectors come from word overlap: similar sentences are near, no key needed."""

from app.config import settings


class WordEmbedder:
    def __init__(self, dimensions: int = 0):
        self.dimensions = dimensions or settings.embedding_dimensions
        self.calls: list[list[str]] = []

    async def embed(self, texts: list[str]) -> list[list[float]]:
        self.calls.append(list(texts))
        out = []
        for text in texts:
            vector = [0.0] * self.dimensions
            for word in text.lower().replace(".", "").split():
                vector[sum(map(ord, word)) % self.dimensions] += 1.0
            norm = sum(v * v for v in vector) ** 0.5 or 1.0
            out.append([v / norm for v in vector])
        return out
