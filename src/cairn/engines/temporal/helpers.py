import asyncio
import os
import re
from collections.abc import Coroutine
from datetime import datetime, timezone
from typing import Any

import numpy as np
from numpy._typing import NDArray
from pydantic import BaseModel

from cairn.engines.temporal.driver.driver import GraphProvider
from cairn.engines.temporal.errors import GroupIdValidationError, NodeLabelValidationError

SAFE_CYPHER_IDENTIFIER_PATTERN = re.compile(r'^[A-Za-z_][A-Za-z0-9_]*$')

USE_PARALLEL_RUNTIME = bool(os.getenv('CAIRN_TEMPORAL_PARALLEL_RUNTIME', False))
SEMAPHORE_LIMIT = int(os.getenv('CAIRN_TEMPORAL_SEMAPHORE_LIMIT', 20))
DEFAULT_PAGE_LIMIT = 20

# Content chunking configuration for entity extraction
# Density-based chunking: only chunk high-density content (many entities per token)
# This targets the failure case (large entity-dense inputs) while preserving
# context for prose/narrative content
CHUNK_TOKEN_SIZE = int(os.getenv('CAIRN_TEMPORAL_CHUNK_TOKEN_SIZE', 3000))
CHUNK_OVERLAP_TOKENS = int(os.getenv('CAIRN_TEMPORAL_CHUNK_OVERLAP_TOKENS', 200))
# Minimum tokens before considering chunking - short content processes fine regardless of density
CHUNK_MIN_TOKENS = int(os.getenv('CAIRN_TEMPORAL_CHUNK_MIN_TOKENS', 1000))
# Entity density threshold: chunk if estimated density > this value
# For JSON: elements per 1000 tokens > threshold * 1000 (e.g., 0.15 = 150 elements/1000 tokens)
# For Text: capitalized words per 1000 tokens > threshold * 500 (e.g., 0.15 = 75 caps/1000 tokens)
# Higher values = more conservative (less chunking), targets P95+ density cases
# Examples that trigger chunking at 0.15: AWS cost data (12mo), bulk data imports, entity-dense JSON
# Examples that DON'T chunk at 0.15: meeting transcripts, news articles, documentation
CHUNK_DENSITY_THRESHOLD = float(os.getenv('CAIRN_TEMPORAL_CHUNK_DENSITY_THRESHOLD', 0.15))


def parse_db_date(input_date: Any) -> datetime | None:
    # Server drivers return their own temporal types (e.g. neo4j.time.DateTime); they all
    # expose ``to_native()``. The embedded store returns naive UTC ``datetime`` values and
    # some stores return ISO strings. Everything leaves here timezone-aware in UTC so that
    # comparisons between stored and freshly extracted times never mix naive and aware values.
    if input_date is None:
        return None
    if not isinstance(input_date, str | datetime) and hasattr(input_date, 'to_native'):
        input_date = input_date.to_native()
    elif isinstance(input_date, str):
        input_date = datetime.fromisoformat(input_date.replace('Z', '+00:00'))
    if isinstance(input_date, datetime):
        if input_date.tzinfo is None:
            return input_date.replace(tzinfo=timezone.utc)
        return input_date.astimezone(timezone.utc)
    return input_date


def get_default_group_id(provider: GraphProvider) -> str:
    """
    This function differentiates the default group id based on the database type.
    For most databases, the default group id is an empty string, while there are database types that require a specific default group id.
    """
    if provider == GraphProvider.FALKORDB:
        return '_'
    else:
        return ''


def lucene_sanitize(query: str) -> str:
    # Escape special characters from a query before passing into Lucene
    # + - && || ! ( ) { } [ ] ^ " ~ * ? : \ /
    escape_map = str.maketrans(
        {
            '+': r'\+',
            '-': r'\-',
            '&': r'\&',
            '|': r'\|',
            '!': r'\!',
            '(': r'\(',
            ')': r'\)',
            '{': r'\{',
            '}': r'\}',
            '[': r'\[',
            ']': r'\]',
            '^': r'\^',
            '"': r'\"',
            '~': r'\~',
            '*': r'\*',
            '?': r'\?',
            ':': r'\:',
            '\\': r'\\',
            '/': r'\/',
            'O': r'\O',
            'R': r'\R',
            'N': r'\N',
            'T': r'\T',
            'A': r'\A',
            'D': r'\D',
        }
    )

    sanitized = query.translate(escape_map)
    return sanitized


def normalize_l2(embedding: list[float]) -> NDArray:
    embedding_array = np.array(embedding)
    norm = np.linalg.norm(embedding_array, 2, axis=0, keepdims=True)
    # Zero vectors stay as they are (divide by 1, not 0).
    return embedding_array / np.where(norm == 0, 1.0, norm)


# Use this instead of asyncio.gather() to bound coroutines
async def semaphore_gather(
    *coroutines: Coroutine,
    max_coroutines: int | None = None,
) -> list[Any]:
    semaphore = asyncio.Semaphore(max_coroutines or SEMAPHORE_LIMIT)

    async def _wrap_coroutine(coroutine):
        async with semaphore:
            return await coroutine

    return await asyncio.gather(*(_wrap_coroutine(coroutine) for coroutine in coroutines))


def validate_group_id(group_id: str | None) -> bool:
    """
    Validate that a group_id contains only ASCII alphanumeric characters, dashes, and underscores.

    Args:
        group_id: The group_id to validate

    Returns:
        True if valid, False otherwise

    Raises:
        GroupIdValidationError: If group_id contains invalid characters
    """

    # Allow empty string (default case)
    if not group_id:
        return True

    # Check if string contains only ASCII alphanumeric characters, dashes, or underscores
    # Pattern matches: letters (a-z, A-Z), digits (0-9), hyphens (-), and underscores (_)
    if not re.match(r'^[a-zA-Z0-9_-]+$', group_id):
        raise GroupIdValidationError(group_id)

    return True


def validate_group_ids(group_ids: list[str] | None) -> bool:
    """Validate a list of group ids used by search paths."""

    if group_ids is None:
        return True

    for group_id in group_ids:
        validate_group_id(group_id)

    return True


def validate_node_labels(node_labels: list[str] | None) -> bool:
    """Validate that node labels are safe to interpolate into Cypher label expressions."""

    if not node_labels:
        return True

    invalid_labels = [
        label for label in node_labels if not SAFE_CYPHER_IDENTIFIER_PATTERN.match(label)
    ]
    if invalid_labels:
        raise NodeLabelValidationError(invalid_labels)

    return True


def validate_excluded_entity_types(
    excluded_entity_types: list[str] | None, entity_types: dict[str, type[BaseModel]] | None = None
) -> bool:
    """
    Validate that excluded entity types are valid type names.

    Args:
        excluded_entity_types: List of entity type names to exclude
        entity_types: Dictionary of available custom entity types

    Returns:
        True if valid

    Raises:
        ValueError: If any excluded type names are invalid
    """
    if not excluded_entity_types:
        return True

    # Build set of available type names
    available_types = {'Entity'}  # Default type is always available
    if entity_types:
        available_types.update(entity_types.keys())

    # Check for invalid type names
    invalid_types = set(excluded_entity_types) - available_types
    if invalid_types:
        raise ValueError(
            f'Invalid excluded entity types: {sorted(invalid_types)}. Available types: {sorted(available_types)}'
        )

    return True
