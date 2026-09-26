from pydantic import BaseModel

from cairn.engines.temporal.errors import EntityTypeValidationError
from cairn.engines.temporal.nodes import EntityNode


def validate_entity_types(
    entity_types: dict[str, type[BaseModel]] | None,
) -> bool:
    if entity_types is None:
        return True

    entity_node_field_names = EntityNode.model_fields.keys()

    for entity_type_name, entity_type_model in entity_types.items():
        entity_type_field_names = entity_type_model.model_fields.keys()
        for entity_type_field_name in entity_type_field_names:
            if entity_type_field_name in entity_node_field_names:
                raise EntityTypeValidationError(entity_type_name, entity_type_field_name)

    return True
