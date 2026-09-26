import logging
from time import time

from cairn.engines.temporal.embedder.client import EmbedderClient

logger = logging.getLogger(__name__)


async def generate_embedding(embedder: EmbedderClient, text: str):
    start = time()

    text = text.replace('\n', ' ')
    embedding = await embedder.create(input_data=[text])

    end = time()
    logger.debug(f'embedded text of length {len(text)} in {end - start} ms')

    return embedding
