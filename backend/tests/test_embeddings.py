from types import SimpleNamespace

import pytest
from fake_embedder import FakeEmbedder

from app.memory.embeddings import OpenAIEmbedder


class FakeClient:
    """Stands in for AsyncOpenAI: one vector per input, sized as asked, and a record of calls."""

    def __init__(self, size: int = 4):
        self.size = size
        self.batches: list[list[str]] = []
        self.embeddings = SimpleNamespace(create=self._create)

    async def _create(self, model: str, input: list[str]):
        self.batches.append(input)
        data = [SimpleNamespace(embedding=[float(len(text))] * self.size) for text in input]
        return SimpleNamespace(data=data)


def embedder(client, **kwargs):
    return OpenAIEmbedder(client=client, dimensions=4, **kwargs)


async def test_one_vector_per_text_in_order():
    vectors = await embedder(FakeClient()).embed(["a", "bbb", "cc"])

    assert [v[0] for v in vectors] == [1.0, 3.0, 2.0]


async def test_texts_go_out_in_batches():
    client = FakeClient()

    vectors = await embedder(client, batch_size=2).embed(["a", "b", "c", "d", "e"])

    assert [len(batch) for batch in client.batches] == [2, 2, 1]
    assert len(vectors) == 5


async def test_nothing_to_embed_makes_no_call():
    client = FakeClient()

    assert await embedder(client).embed([]) == []
    assert client.batches == []


async def test_a_vector_of_the_wrong_size_is_refused():
    with pytest.raises(ValueError, match="expected 4"):
        await embedder(FakeClient(size=3)).embed(["a"])


def test_a_missing_key_is_reported_before_any_call(monkeypatch):
    monkeypatch.setattr("app.memory.embeddings.settings.openai_api_key", "")

    with pytest.raises(RuntimeError, match="OPENAI_API_KEY"):
        OpenAIEmbedder()


async def test_the_fake_embedder_is_deterministic_and_has_the_column_size():
    fake = FakeEmbedder()

    first, again, other = await fake.embed(["lives in Porto", "lives in Porto", "likes tea"])

    assert first == again != other
    assert len(first) == 1536
    assert sum(v * v for v in first) == pytest.approx(1.0)
