"""Small, bounded declarative schema subset; never resolves references."""
import json
import math


_SCALAR_TYPES = (str, int, float, bool, type(None))
_SUPPORTED_TYPES = {"string", "number", "integer", "boolean", "null", "object", "array"}
_MAX_SCHEMA_BYTES = 32 * 1024
_MAX_DEPTH = 12
_MAX_NODES = 256
_MAX_PROPERTIES = 100
_MAX_UNION_BRANCHES = 4
_MAX_ENUM_VALUES = 100
_MAX_TOTAL_ENUM_VALUES = 256
_MAX_ENUM_STRING_BYTES = 16 * 1024
_MAX_PATTERN_LENGTH = 256
_MAX_BOUND = 100_000


def validate_schema(value: dict) -> dict:
    """Validate the Gateway's bounded, product-neutral Structured Outputs subset."""
    node_count = 0
    enum_count = 0
    enum_string_bytes = 0

    def visit(node: object, depth: int, *, root: bool = False) -> None:
        nonlocal node_count, enum_count, enum_string_bytes
        node_count += 1
        if depth > _MAX_DEPTH or node_count > _MAX_NODES or not isinstance(node, dict):
            raise ValueError("schema exceeds structural limits")

        if "anyOf" in node:
            if root or set(node) != {"anyOf"}:
                raise ValueError("unsupported schema union")
            branches = node["anyOf"]
            if not isinstance(branches, list) or not 2 <= len(branches) <= _MAX_UNION_BRANCHES:
                raise ValueError("schema union exceeds branch limits")
            for branch in branches:
                visit(branch, depth + 1)
            return

        declared_type = node.get("type")
        kinds = _schema_types(declared_type)
        kind = next((item for item in kinds if item != "null"), "null")
        allowed = {"type", "enum", "const"}

        if kind == "object":
            if kinds != {"object"}:
                raise ValueError("objects cannot use nullable type declarations")
            allowed |= {"properties", "required", "additionalProperties"}
            properties, required = node.get("properties"), node.get("required")
            if (
                not isinstance(properties, dict)
                or len(properties) > _MAX_PROPERTIES
                or not isinstance(required, list)
                or any(not isinstance(item, str) for item in required)
                or len(required) != len(set(required))
                or set(required) != set(properties)
                or node.get("additionalProperties") is not False
            ):
                raise ValueError("objects must require every property and forbid extras")
            for name, child in properties.items():
                if not isinstance(name, str) or not 1 <= len(name) <= 128:
                    raise ValueError("invalid property name")
                visit(child, depth + 1)
        elif kind == "array":
            if kinds != {"array"}:
                raise ValueError("arrays cannot use nullable type declarations")
            allowed |= {"items", "minItems", "maxItems"}
            visit(node.get("items"), depth + 1)
            _validate_integer_bounds(node, "minItems", "maxItems")
        elif kind == "string":
            allowed |= {"pattern", "minLength", "maxLength"}
            pattern = node.get("pattern")
            if pattern is not None and (
                not isinstance(pattern, str)
                or len(pattern) > _MAX_PATTERN_LENGTH
                or any(ord(character) < 32 for character in pattern)
            ):
                raise ValueError("invalid schema pattern")
            _validate_integer_bounds(node, "minLength", "maxLength")
        elif kind in {"number", "integer"}:
            allowed |= {"minimum", "maximum", "multipleOf"}
            _validate_number_bounds(node)

        enum = node.get("enum")
        if enum is not None:
            if not isinstance(enum, list) or not 1 <= len(enum) <= _MAX_ENUM_VALUES:
                raise ValueError("schema enum exceeds value limits")
            canonical_values: set[str] = set()
            for item in enum:
                _validate_scalar(item, kinds)
                canonical = json.dumps(item, ensure_ascii=True, allow_nan=False, sort_keys=True)
                if canonical in canonical_values:
                    raise ValueError("schema enum values must be unique")
                canonical_values.add(canonical)
                enum_count += 1
                if isinstance(item, str):
                    enum_string_bytes += len(item.encode("utf-8"))
            if enum_count > _MAX_TOTAL_ENUM_VALUES or enum_string_bytes > _MAX_ENUM_STRING_BYTES:
                raise ValueError("schema enum exceeds aggregate limits")

        if "const" in node:
            _validate_scalar(node["const"], kinds)
            if isinstance(node["const"], str):
                enum_string_bytes += len(node["const"].encode("utf-8"))
                if enum_string_bytes > _MAX_ENUM_STRING_BYTES:
                    raise ValueError("schema constants exceed aggregate limits")

        if set(node) - allowed:
            raise ValueError("unsupported schema keyword")

    if not isinstance(value, dict) or value.get("type") != "object" or "anyOf" in value:
        raise ValueError("schema root must be an object")
    if len(json.dumps(value, ensure_ascii=True, allow_nan=False).encode()) > _MAX_SCHEMA_BYTES:
        raise ValueError("schema exceeds size limit")
    visit(value, 1, root=True)
    return value


def _schema_types(value: object) -> set[str]:
    if isinstance(value, str):
        kinds = {value}
    elif isinstance(value, list):
        if len(value) != 2 or any(not isinstance(item, str) for item in value):
            raise ValueError("unsupported schema type")
        kinds = set(value)
        if len(kinds) != 2 or "null" not in kinds:
            raise ValueError("only nullable type declarations are supported")
    else:
        raise ValueError("unsupported schema type")
    if not kinds.issubset(_SUPPORTED_TYPES):
        raise ValueError("unsupported schema type")
    return kinds


def _validate_scalar(value: object, kinds: set[str]) -> None:
    if not isinstance(value, _SCALAR_TYPES) or (
        isinstance(value, float) and not math.isfinite(value)
    ):
        raise ValueError("schema enum and const values must be finite scalars")
    if value is None:
        compatible = "null" in kinds
    elif isinstance(value, bool):
        compatible = "boolean" in kinds
    elif isinstance(value, int):
        compatible = "integer" in kinds or "number" in kinds
    elif isinstance(value, float):
        compatible = "number" in kinds
    else:
        compatible = "string" in kinds
    if not compatible:
        raise ValueError("schema enum or const value does not match its type")


def _validate_integer_bounds(node: dict, minimum_name: str, maximum_name: str) -> None:
    minimum, maximum = node.get(minimum_name), node.get(maximum_name)
    for value in (minimum, maximum):
        if value is not None and (
            isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= _MAX_BOUND
        ):
            raise ValueError("invalid schema integer bound")
    if minimum is not None and maximum is not None and minimum > maximum:
        raise ValueError("schema minimum exceeds maximum")


def _validate_number_bounds(node: dict) -> None:
    minimum, maximum, multiple = node.get("minimum"), node.get("maximum"), node.get("multipleOf")
    for value in (minimum, maximum, multiple):
        if value is not None and (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(value)
            or abs(value) > _MAX_BOUND
        ):
            raise ValueError("invalid schema numeric bound")
    if minimum is not None and maximum is not None and minimum > maximum:
        raise ValueError("schema minimum exceeds maximum")
    if multiple is not None and multiple <= 0:
        raise ValueError("schema multipleOf must be positive")
