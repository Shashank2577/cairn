from pydantic import BaseModel, ConfigDict

from cairn.engines.temporal.cross_encoder import CrossEncoderClient
from cairn.engines.temporal.driver.driver import GraphDriver
from cairn.engines.temporal.embedder import EmbedderClient
from cairn.engines.temporal.llm_client import LLMClient
from cairn.engines.temporal.tracer import Tracer


class EngineClients(BaseModel):
    driver: GraphDriver
    llm_client: LLMClient
    embedder: EmbedderClient
    cross_encoder: CrossEncoderClient
    tracer: Tracer

    model_config = ConfigDict(arbitrary_types_allowed=True)
