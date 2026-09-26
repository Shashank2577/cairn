"""Entity and fact (edge) types for the temporal graph, and helpers to build them from config.

A type is a pydantic model: its docstring tells the extractor what belongs in the type, and its
fields (if any) become attributes the model fills in for each entity or fact of that type. Types
are passed to ``TemporalGraph.add_episode(entity_types=..., edge_types=..., edge_type_map=...)``.

Three sources, merged by name (later wins):
  * ``ENTITY_TYPES`` / ``EDGE_TYPES`` — general-purpose rich types (with attributes).
  * ``ENGINEERING_ENTITY_TYPES`` — attribute-free types tuned for repository activity; used by the
    timeline builder so classification costs no extra model calls.
  * config: ``temporal.entity_types`` / ``temporal.edge_types`` name built-in types or define new
    documentation-only ones (``{name, description}``); ``temporal.edge_type_map`` constrains which
    fact types may connect which entity types (``'Entity'`` is the wildcard).
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, Field, create_model

from .search.search_filters import ComparisonOperator, DateFilter, SearchFilters

# ---- general-purpose entity types ---------------------------------------------------------------


class Requirement(BaseModel):
    """A Requirement represents a specific need, feature, or functionality that a product or service must fulfill.

    Always ensure an edge is created between the requirement and the project it belongs to, and clearly indicate on the
    edge that the requirement is a requirement.

    Instructions for identifying and extracting requirements:
    1. Look for explicit statements of needs or necessities ("We need X", "X is required", "X must have Y")
    2. Identify functional specifications that describe what the system should do
    3. Pay attention to non-functional requirements like performance, security, or usability criteria
    4. Extract constraints or limitations that must be adhered to
    5. Focus on clear, specific, and measurable requirements rather than vague wishes
    6. Capture the priority or importance if mentioned ("critical", "high priority", etc.)
    7. Include any dependencies between requirements when explicitly stated
    8. Preserve the original intent and scope of the requirement
    9. Categorize requirements appropriately based on their domain or function
    """

    project_name: str = Field(
        ...,
        description='The name of the project to which the requirement belongs.',
    )
    description: str = Field(
        ...,
        description='Description of the requirement. Only use information mentioned in the context to write this description.',
    )


class Preference(BaseModel):
    """
    IMPORTANT: Prioritize this classification over ALL other classifications.

    Represents entities mentioned in contexts expressing user preferences, choices, opinions, or selections. Use LOW THRESHOLD for sensitivity.

    Trigger patterns: "I want/like/prefer/choose X", "I don't want/dislike/avoid/reject Y", "X is better/worse", "rather have X than Y", "no X please", "skip X", "go with X instead", etc. Here, X or Y should be classified as Preference.
    """

    ...


class Procedure(BaseModel):
    """A Procedure informing the agent what actions to take or how to perform in certain scenarios. Procedures are typically composed of several steps.

    Instructions for identifying and extracting procedures:
    1. Look for sequential instructions or steps ("First do X, then do Y")
    2. Identify explicit directives or commands ("Always do X when Y happens")
    3. Pay attention to conditional statements ("If X occurs, then do Y")
    4. Extract procedures that have clear beginning and end points
    5. Focus on actionable instructions rather than general information
    6. Preserve the original sequence and dependencies between steps
    7. Include any specified conditions or triggers for the procedure
    8. Capture any stated purpose or goal of the procedure
    9. Summarize complex procedures while maintaining critical details
    """

    description: str = Field(
        ...,
        description='Brief description of the procedure. Only use information mentioned in the context to write this description.',
    )


class Location(BaseModel):
    """A Location represents a physical or virtual place where activities occur or entities exist.

    IMPORTANT: Before using this classification, first check if the entity is a:
    User, Assistant, Preference, Organization, Document, Event - if so, use those instead.

    Instructions for identifying and extracting locations:
    1. Look for mentions of physical places (cities, buildings, rooms, addresses)
    2. Identify virtual locations (websites, online platforms, virtual meeting rooms)
    3. Extract specific location names rather than generic references
    4. Include relevant context about the location's purpose or significance
    5. Pay attention to location hierarchies (e.g., "conference room in Building A")
    6. Capture both permanent locations and temporary venues
    7. Note any significant activities or events associated with the location
    """

    description: str = Field(
        ...,
        description='Brief description of the location and its significance. Only use information mentioned in the context.',
    )


class Event(BaseModel):
    """An Event represents a time-bound activity, occurrence, or experience.

    Instructions for identifying and extracting events:
    1. Look for activities with specific time frames (meetings, appointments, deadlines)
    2. Identify planned or scheduled occurrences (vacations, projects, celebrations)
    3. Extract unplanned occurrences (accidents, interruptions, discoveries)
    4. Capture the purpose or nature of the event
    5. Include temporal information when available (past, present, future, duration)
    6. Note participants or stakeholders involved in the event
    7. Identify outcomes or consequences of the event when mentioned
    8. Extract both recurring events and one-time occurrences
    """

    description: str = Field(
        ...,
        description='Brief description of the event. Only use information mentioned in the context.',
    )


class Object(BaseModel):
    """An Object represents a physical item, tool, device, or possession.

    IMPORTANT: Use this classification ONLY as a last resort. First check if entity fits into:
    User, Assistant, Preference, Organization, Document, Event, Location, Topic - if so, use those instead.

    Instructions for identifying and extracting objects:
    1. Look for mentions of physical items or possessions (car, phone, equipment)
    2. Identify tools or devices used for specific purposes
    3. Extract items that are owned, used, or maintained by entities
    4. Include relevant attributes (brand, model, condition) when mentioned
    5. Note the object's purpose or function when specified
    6. Capture relationships between objects and their owners or users
    7. Avoid extracting objects that are better classified as Documents or other types
    """

    description: str = Field(
        ...,
        description='Brief description of the object. Only use information mentioned in the context.',
    )


class Topic(BaseModel):
    """A Topic represents a subject of conversation, interest, or knowledge domain.

    IMPORTANT: Use this classification ONLY as a last resort. First check if entity fits into:
    User, Assistant, Preference, Organization, Document, Event, Location - if so, use those instead.

    Instructions for identifying and extracting topics:
    1. Look for subjects being discussed or areas of interest (health, technology, sports)
    2. Identify knowledge domains or fields of study
    3. Extract themes that span multiple conversations or contexts
    4. Include specific subtopics when mentioned (e.g., "machine learning" rather than just "AI")
    5. Capture topics associated with projects, work, or hobbies
    6. Note the context in which the topic appears
    7. Avoid extracting topics that are better classified as Events, Documents, or Organizations
    """

    description: str = Field(
        ...,
        description='Brief description of the topic and its context. Only use information mentioned in the context.',
    )


class Person(BaseModel):
    """A Person represents an individual human referenced in the content.

    Instructions for identifying and extracting people:
    1. Look for named individuals (full names, first names, usernames, or handles)
    2. Capture their role or relationship when stated (colleague, author, manager)
    3. Prefer a specific, named person over a generic reference ("the engineer")
    4. Only record attributes that are present in the context
    """

    description: str = Field(
        ...,
        description='Brief description of the person. Only use information mentioned in the context.',
    )


class Organization(BaseModel):
    """An Organization represents a company, institution, group, or formal entity.

    Instructions for identifying and extracting organizations:
    1. Look for company names, employers, and business entities
    2. Identify institutions (schools, hospitals, government agencies)
    3. Extract formal groups (clubs, teams, associations)
    4. Include organizational type when mentioned (company, nonprofit, agency)
    5. Capture relationships between people and organizations (employer, member)
    6. Note the organization's industry or domain when specified
    7. Extract both large entities and small groups if formally organized
    """

    description: str = Field(
        ...,
        description='Brief description of the organization. Only use information mentioned in the context.',
    )


class Document(BaseModel):
    """A Document represents information content in various forms.

    Instructions for identifying and extracting documents:
    1. Look for references to written or recorded content (books, articles, reports)
    2. Identify digital content (emails, videos, podcasts, presentations)
    3. Extract specific document titles or identifiers when available
    4. Include document type (report, article, video) when mentioned
    5. Capture the document's purpose or subject matter
    6. Note relationships to authors, creators, or sources
    7. Include document status (draft, published, archived) when mentioned
    """

    title: str = Field(
        ...,
        description='The title or identifier of the document',
    )
    description: str = Field(
        ...,
        description='Brief description of the document and its content. Only use information mentioned in the context.',
    )


ENTITY_TYPES: dict[str, type[BaseModel]] = {
    'Requirement': Requirement,
    'Preference': Preference,
    'Procedure': Procedure,
    'Location': Location,
    'Event': Event,
    'Object': Object,
    'Topic': Topic,
    'Person': Person,
    'Organization': Organization,
    'Document': Document,
}

# Descriptions used when a general-purpose type is enabled by name only (``entity_types = [...]``).
ENTITY_TYPE_DESCRIPTIONS: dict[str, str] = {
    'Preference': 'User preferences, choices, opinions, or selections (PRIORITIZE over most other types except User/Assistant)',
    'Requirement': 'Specific needs, features, or functionality that must be fulfilled',
    'Procedure': 'Standard operating procedures and sequential instructions',
    'Location': 'Physical or virtual places where activities occur',
    'Event': 'Time-bound activities, occurrences, or experiences',
    'Organization': 'Companies, institutions, groups, or formal entities',
    'Document': 'Information content in various forms (books, articles, reports, etc.)',
    'Topic': 'Subject of conversation, interest, or knowledge domain (use as last resort)',
    'Person': 'An individual human referenced in the content',
    'Object': 'Physical items, tools, devices, or possessions (use as last resort)',
}

# ---- general-purpose fact (edge) types -----------------------------------------------------------


class RelatesTo(BaseModel):
    """A generic, untyped relationship between two entities.

    Use this only when no more specific edge type applies. Captures that two
    entities are associated without asserting the nature of the association.
    """

    ...


class MentionedIn(BaseModel):
    """An entity is referenced, described, or discussed within a source.

    Connects an entity to the document, message, or context in which it appears.
    """

    ...


class WorksFor(BaseModel):
    """An employment or membership relationship between a person and an organization."""

    role: str | None = Field(
        default=None,
        description='The role, title, or position held. Only use information present in the context.',
    )


class LocatedAt(BaseModel):
    """A spatial relationship indicating an entity is situated at or within a location."""

    ...


class ParticipatesIn(BaseModel):
    """A person or organization takes part in an event or activity."""

    ...


class Owns(BaseModel):
    """An ownership or possession relationship between an entity and an object."""

    ...


class Requires(BaseModel):
    """A dependency relationship: the source needs or depends on the target.

    Commonly connects a project or requirement to the thing it depends upon.
    """

    ...


EDGE_TYPES: dict[str, type[BaseModel]] = {
    'RelatesTo': RelatesTo,
    'MentionedIn': MentionedIn,
    'WorksFor': WorksFor,
    'LocatedAt': LocatedAt,
    'ParticipatesIn': ParticipatesIn,
    'Owns': Owns,
    'Requires': Requires,
}

# ---- engineering types (attribute-free: classification only, no extra model calls) -------------


class Contributor(BaseModel):
    """A person or agent who changes the repository: a commit author, reviewer, or coding agent.

    Use the name exactly as it appears (git author name, handle, or agent name).
    """


class Component(BaseModel):
    """A part of the codebase: a file, module, package, service, class, function, API or table.

    Prefer the most specific identifier given (a path like ``shop/payments.py`` or a symbol like
    ``PaymentService.process``). Do not extract commit hashes as components.
    """


class Feature(BaseModel):
    """A user-facing capability or product feature being built, changed or removed (for example
    "refunds", "checkout", "dark mode"), including feature specifications.
    """


class Defect(BaseModel):
    """A bug, regression, incident, vulnerability or failing behavior that was reported or fixed."""


class Decision(BaseModel):
    """An engineering decision, convention or constraint the team adopted or reversed
    (for example "use idempotency keys on retries", "drop Python 3.9 support").
    """


class Library(BaseModel):
    """A third-party dependency, framework, tool or external service the project uses."""


class Spec(BaseModel):
    """A specification document, requirement id (FR-001), user story or task (T001) of the
    project's spec workflow.
    """


ENGINEERING_ENTITY_TYPES: dict[str, type[BaseModel]] = {
    'Contributor': Contributor,
    'Component': Component,
    'Feature': Feature,
    'Defect': Defect,
    'Decision': Decision,
    'Library': Library,
    'Spec': Spec,
}

ENGINEERING_EXTRACTION_INSTRUCTIONS = (
    'This content describes engineering activity in a software repository (commits, spec progress, '
    'agent sessions, team decisions). Extract people, code components, features, specs, defects, '
    'decisions and libraries. Commit hashes, dates and generic words ("fix", "update", "code") are '
    'not entities. Facts should capture who changed what, what was added, fixed, reverted or '
    'replaced, which component implements which feature, and decisions with their rationale; set '
    'valid_at to the day the change happened and invalid_at when a later change reverts or '
    'replaces it.'
)

# ---- builders ------------------------------------------------------------------------------------


def doc_only_model(name: str, description: str) -> type[BaseModel]:
    """A type with no attributes: the description is what the extractor sees."""
    model = create_model(name)
    model.__doc__ = description
    return model


def _entries(configs: Iterable[Any] | None) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    for cfg in configs or []:
        if isinstance(cfg, str):
            out.append((cfg, ''))
        elif isinstance(cfg, Mapping):
            out.append((str(cfg.get('name', '')), str(cfg.get('description', '') or '')))
        else:
            out.append((str(getattr(cfg, 'name', '')), str(getattr(cfg, 'description', '') or '')))
    return [(n, d) for n, d in out if n]


def build_entity_types(
    configs: Iterable[Any] | None,
    registry: Mapping[str, type[BaseModel]] | None = None,
) -> dict[str, type[BaseModel]] | None:
    """Build ``entity_types`` from config entries (names, ``{name, description}`` or objects).

    A name that matches a registered rich model uses that model; anything else becomes a
    documentation-only model. ``None`` when nothing is configured (default extraction).
    """
    registry = registry if registry is not None else {**ENTITY_TYPES, **ENGINEERING_ENTITY_TYPES}
    result: dict[str, type[BaseModel]] = {}
    for name, description in _entries(configs):
        registered = registry.get(name)
        if registered is not None:
            result[name] = registered
        else:
            result[name] = doc_only_model(name, description or ENTITY_TYPE_DESCRIPTIONS.get(name, name))
    return result or None


def build_edge_types(
    configs: Iterable[Any] | None,
    registry: Mapping[str, type[BaseModel]] | None = None,
) -> dict[str, type[BaseModel]] | None:
    registry = registry if registry is not None else EDGE_TYPES
    result: dict[str, type[BaseModel]] = {}
    for name, description in _entries(configs):
        registered = registry.get(name)
        result[name] = registered if registered is not None else doc_only_model(name, description or name)
    return result or None


def build_edge_type_map(entries: Iterable[Any] | None) -> dict[tuple[str, str], list[str]] | None:
    """``[{source, target, edge_types}]`` -> ``{(source, target): [edge_type, ...]}``."""
    result: dict[tuple[str, str], list[str]] = {}
    for entry in entries or []:
        get = entry.get if isinstance(entry, Mapping) else (lambda k, d=None, e=entry: getattr(e, k, d))
        source = str(get('source', 'Entity') or 'Entity')
        target = str(get('target', 'Entity') or 'Entity')
        result[(source, target)] = [str(t) for t in (get('edge_types', []) or [])]
    return result or None


def parse_reference_time(value: str | datetime | None) -> datetime | None:
    """ISO-8601 (``Z`` allowed) or datetime -> timezone-aware UTC datetime; naive means UTC."""
    if value is None or value == '':
        return None
    if isinstance(value, datetime):
        parsed = value
    else:
        normalized = str(value).strip()
        if normalized.endswith(('Z', 'z')):
            normalized = normalized[:-1] + '+00:00'
        parsed = datetime.fromisoformat(normalized)
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def coerce_group_ids(group_ids: str | list[str] | None) -> list[str] | None:
    """A scalar group id becomes a one-element list; a blank string means "not given"."""
    if isinstance(group_ids, str):
        return [group_ids] if group_ids else None
    return group_ids


def _date_range(after: datetime | None, before: datetime | None) -> list[list[DateFilter]] | None:
    conditions: list[DateFilter] = []
    if after is not None:
        conditions.append(DateFilter(date=after, comparison_operator=ComparisonOperator.greater_than_equal))
    if before is not None:
        conditions.append(DateFilter(date=before, comparison_operator=ComparisonOperator.less_than_equal))
    return [conditions] if conditions else None


def build_fact_search_filters(
    edge_types: list[str] | None = None,
    valid_at_after: str | datetime | None = None,
    valid_at_before: str | datetime | None = None,
    invalid_at_after: str | datetime | None = None,
    invalid_at_before: str | datetime | None = None,
    current_only: bool = False,
) -> SearchFilters | None:
    """Search filters for facts by type and validity window. ``current_only`` keeps only facts
    that have not been invalidated (``invalid_at IS NULL``) and not expired."""
    valid_at = _date_range(parse_reference_time(valid_at_after), parse_reference_time(valid_at_before))
    invalid_at = _date_range(parse_reference_time(invalid_at_after), parse_reference_time(invalid_at_before))
    if current_only:
        invalid_at = [[DateFilter(comparison_operator=ComparisonOperator.is_null)]]
    expired_at = [[DateFilter(comparison_operator=ComparisonOperator.is_null)]] if current_only else None
    if not edge_types and valid_at is None and invalid_at is None and expired_at is None:
        return None
    return SearchFilters(
        edge_types=edge_types or None,
        valid_at=valid_at,
        invalid_at=invalid_at,
        expired_at=expired_at,
    )
