"""Flatten Pydantic's serialization schema for the provider's JSON schema subset."""

from copy import deepcopy

from pydantic import BaseModel


def output_schema(model: type[BaseModel]) -> dict:
    schema = model.model_json_schema(mode="serialization")
    definitions = schema.pop("$defs", {})

    def inline(node):
        if isinstance(node, list):
            return [inline(value) for value in node]
        if not isinstance(node, dict):
            return node
        if "$ref" in node:
            return inline(deepcopy(definitions[node["$ref"].split("/")[-1]]))
        # Decimal serialization schemas are strings, never binary floating point.
        return {
            key: inline(value)
            for key, value in node.items()
            if key not in {"title", "default", "pattern"}
            and not (key == "additionalProperties" and value is False)
        }

    result = inline(schema)

    # Require explicit output for defaulted fields as well: absent facts must be null.
    def require_fields(node):
        if isinstance(node, dict):
            if "properties" in node:
                node["required"] = list(node["properties"])
            for value in node.values():
                require_fields(value)
        elif isinstance(node, list):
            for value in node:
                require_fields(value)

    require_fields(result)
    return result
