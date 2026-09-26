class TemporalGraphError(Exception):
    """Base exception class for the temporal graph engine."""


class EdgeNotFoundError(TemporalGraphError):
    """Raised when an edge is not found."""

    def __init__(self, uuid: str):
        self.message = f'edge {uuid} not found'
        super().__init__(self.message)


class EdgesNotFoundError(TemporalGraphError):
    """Raised when a list of edges is not found."""

    def __init__(self, uuids: list[str]):
        self.message = f'None of the edges for {uuids} were found.'
        super().__init__(self.message)


class GroupsEdgesNotFoundError(TemporalGraphError):
    """Raised when no edges are found for a list of group ids."""

    def __init__(self, group_ids: list[str]):
        self.message = f'no edges found for group ids {group_ids}'
        super().__init__(self.message)


class GroupsNodesNotFoundError(TemporalGraphError):
    """Raised when no nodes are found for a list of group ids."""

    def __init__(self, group_ids: list[str]):
        self.message = f'no nodes found for group ids {group_ids}'
        super().__init__(self.message)


class NodeNotFoundError(TemporalGraphError):
    """Raised when a node is not found."""

    def __init__(self, uuid: str):
        self.message = f'node {uuid} not found'
        super().__init__(self.message)


class SearchRerankerError(TemporalGraphError):
    """Raised when a node is not found."""

    def __init__(self, text: str):
        self.message = text
        super().__init__(self.message)


class EntityTypeValidationError(TemporalGraphError):
    """Raised when an entity type uses protected attribute names."""

    def __init__(self, entity_type: str, entity_type_attribute: str):
        self.message = f'{entity_type_attribute} cannot be used as an attribute for {entity_type} as it is a protected attribute name.'
        super().__init__(self.message)


class GroupIdValidationError(TemporalGraphError):
    """Raised when a group_id contains invalid characters."""

    def __init__(self, group_id: str):
        self.message = f'group_id "{group_id}" must contain only alphanumeric characters, dashes, or underscores'
        super().__init__(self.message)


class NodeLabelValidationError(TemporalGraphError, ValueError):
    """Raised when a node label contains invalid characters."""

    def __init__(self, node_labels: list[str]):
        label_list = ', '.join(f'"{label}"' for label in node_labels)
        self.message = (
            'node_labels must start with a letter or underscore and contain only '
            f'alphanumeric characters or underscores: {label_list}'
        )
        super().__init__(self.message)
