"""Provider-neutral vector retrieval with a deterministic offline baseline."""

from __future__ import annotations

import hashlib
import json
import math
import re
from collections.abc import Sequence
from typing import Protocol

from agentforge.core.approval import ApprovalCapability, ApprovalCheckpoint
from agentforge.llm.errors import ProviderResponseError
from agentforge.llm.transport import HttpTransport, UrllibHttpTransport
from agentforge.tools.repository import RepositoryInspector, RepositoryToolError

_TOKEN_PATTERN = re.compile(r"[A-Za-z0-9]+")
_CAMEL_BOUNDARY = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")
_STOP_WORDS = frozenset(
    {
        "a",
        "an",
        "and",
        "are",
        "for",
        "from",
        "in",
        "is",
        "of",
        "on",
        "the",
        "to",
        "where",
        "with",
    }
)


class EmbeddingProvider(Protocol):
    """Turn text into equal-length numeric vectors."""

    @property
    def dimensions(self) -> int: ...

    def embed(self, texts: Sequence[str]) -> tuple[tuple[float, ...], ...]: ...


class OpenAICompatibleEmbeddingProvider:
    """Call a learned HTTPS embedding model only with transmission approval."""

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        model: str,
        dimensions: int,
        approval: ApprovalCheckpoint,
        timeout_seconds: float = 60,
        transport: HttpTransport | None = None,
    ) -> None:
        if not base_url.startswith("https://"):
            raise ValueError("Embedding-provider base URL must use HTTPS")
        if not api_key.strip() or not model.strip():
            raise ValueError("Embedding API key and model must be non-empty")
        if dimensions <= 0 or timeout_seconds <= 0:
            raise ValueError("Embedding dimensions and timeout must be greater than zero")
        self.base_url = base_url.rstrip("/")
        self.model = model
        self._dimensions = dimensions
        self._approval = approval
        self._timeout_seconds = timeout_seconds
        self._api_key = api_key
        self._transport = transport or UrllibHttpTransport()

    @property
    def dimensions(self) -> int:
        return self._dimensions

    def embed(self, texts: Sequence[str]) -> tuple[tuple[float, ...], ...]:
        if not texts:
            return ()
        if any(not isinstance(text, str) or not text.strip() for text in texts):
            raise ValueError("Embedding inputs must be non-empty text")
        self._approval.require(ApprovalCapability.CODE_TRANSMISSION)
        response = self._transport.post(
            f"{self.base_url}/embeddings",
            headers={
                "Authorization": f"Bearer {self._api_key}",
                "Content-Type": "application/json",
                "User-Agent": "AgentForge/0.1",
            },
            body=json.dumps(
                {"model": self.model, "input": list(texts)}, separators=(",", ":")
            ).encode("utf-8"),
            timeout_seconds=self._timeout_seconds,
        )
        if response.status_code >= 400:
            raise ProviderResponseError(
                f"Embedding provider returned HTTP {response.status_code}"
            )
        try:
            document = json.loads(response.body)
            data = sorted(document["data"], key=lambda item: item["index"])
            vectors = tuple(
                tuple(float(value) for value in item["embedding"]) for item in data
            )
        except (UnicodeDecodeError, json.JSONDecodeError, KeyError, TypeError, ValueError) as error:
            raise ProviderResponseError("Embedding provider returned malformed data") from error
        if len(vectors) != len(texts) or any(
            len(vector) != self.dimensions for vector in vectors
        ):
            raise ProviderResponseError("Embedding provider returned unexpected dimensions")
        if any(not math.isfinite(value) for vector in vectors for value in vector):
            raise ProviderResponseError("Embedding provider returned non-finite values")
        return vectors


class HashingEmbeddingProvider:
    """Create stable lexical vectors locally, with no model or network dependency."""

    def __init__(self, *, dimensions: int = 256) -> None:
        if dimensions < 16:
            raise ValueError("Embedding dimensions must be at least 16")
        self._dimensions = dimensions

    @property
    def dimensions(self) -> int:
        return self._dimensions

    def embed(self, texts: Sequence[str]) -> tuple[tuple[float, ...], ...]:
        vectors: list[tuple[float, ...]] = []
        for text in texts:
            values = [0.0] * self.dimensions
            for token in self._tokens(text):
                digest = hashlib.blake2b(token.encode("utf-8"), digest_size=8).digest()
                slot = int.from_bytes(digest, "big") % self.dimensions
                values[slot] += 1.0
            magnitude = math.sqrt(sum(value * value for value in values))
            if magnitude:
                values = [value / magnitude for value in values]
            vectors.append(tuple(values))
        return tuple(vectors)

    @staticmethod
    def _tokens(text: str) -> tuple[str, ...]:
        separated = _CAMEL_BOUNDARY.sub(" ", text).replace("_", " ")
        return tuple(
            token
            for match in _TOKEN_PATTERN.finditer(separated)
            if (token := match.group(0).casefold()) not in _STOP_WORDS
        )


class RepositoryEmbeddingIndex:
    """Rank safe repository text files by cosine similarity to a query."""

    def __init__(
        self,
        inspector: RepositoryInspector,
        provider: EmbeddingProvider,
        *,
        maximum_results: int = 10,
        maximum_document_characters: int = 50_000,
    ) -> None:
        if maximum_results <= 0:
            raise ValueError("Semantic result limit must be greater than zero")
        if maximum_document_characters <= 0:
            raise ValueError("Semantic document limit must be greater than zero")
        if provider.dimensions <= 0:
            raise ValueError("Embedding provider dimensions must be greater than zero")
        self._inspector = inspector
        self._provider = provider
        self.maximum_results = maximum_results
        self.maximum_document_characters = maximum_document_characters
        self._scope: str | None = None
        self._paths: tuple[str, ...] = ()
        self._vectors: tuple[tuple[float, ...], ...] = ()

    def search(self, query: str, *, path: str = ".") -> list[dict[str, object]]:
        if not query.strip():
            raise RepositoryToolError("Semantic search query cannot be empty")
        self._build(path)
        query_vectors = self._provider.embed((query,))
        self._validate_vectors(query_vectors, 1)
        query_vector = query_vectors[0]
        ranked = sorted(
            (
                (
                    sum(
                        left * right
                        for left, right in zip(query_vector, vector, strict=True)
                    ),
                    file,
                )
                for file, vector in zip(self._paths, self._vectors, strict=True)
            ),
            key=lambda item: (-item[0], item[1]),
        )
        return [
            {"path": file, "score": round(score, 6)}
            for score, file in ranked[: self.maximum_results]
            if score > 0
        ]

    def refresh(self) -> None:
        self._scope = None
        self._paths = ()
        self._vectors = ()

    def _build(self, path: str) -> None:
        if path == self._scope and self._vectors:
            return
        paths = tuple(self._inspector.list_files(path))
        documents: list[str] = []
        kept_paths: list[str] = []
        for file in paths:
            try:
                content = self._inspector.read_file(file)
            except RepositoryToolError:
                continue
            documents.append(f"{file}\n{content[: self.maximum_document_characters]}")
            kept_paths.append(file)
        vectors = self._provider.embed(tuple(documents))
        self._validate_vectors(vectors, len(documents))
        self._scope = path
        self._paths = tuple(kept_paths)
        self._vectors = vectors

    def _validate_vectors(
        self, vectors: tuple[tuple[float, ...], ...], expected_count: int
    ) -> None:
        if len(vectors) != expected_count:
            raise RepositoryToolError("Embedding provider returned the wrong vector count")
        if any(len(vector) != self._provider.dimensions for vector in vectors):
            raise RepositoryToolError("Embedding provider returned the wrong dimensions")
