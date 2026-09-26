import os
from abc import ABC, abstractmethod
from collections.abc import Iterable

from pydantic import BaseModel, Field

# Local embeddings (BAAI/bge-small-en-v1.5) are 384-dimensional.
EMBEDDING_DIM = int(os.getenv('CAIRN_TEMPORAL_EMBEDDING_DIM', 384))


class EmbedderConfig(BaseModel):
    embedding_dim: int = Field(default=EMBEDDING_DIM, frozen=True)


class EmbedderClient(ABC):
    @abstractmethod
    async def create(
        self, input_data: str | list[str] | Iterable[int] | Iterable[Iterable[int]]
    ) -> list[float]:
        pass

    async def create_batch(self, input_data_list: list[str]) -> list[list[float]]:
        raise NotImplementedError()
