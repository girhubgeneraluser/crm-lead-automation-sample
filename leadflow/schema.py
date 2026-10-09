"""Minimal JSON Schema validator (stdlib only).

Supports the subset of draft-07 keywords this project uses: type, required, properties,
additionalProperties (bool), enum, minLength, maxLength, pattern, minimum, maximum,
items, minItems, maxItems. Returns *all* errors with JSON-path style locations so the
webhook can answer with a precise 422 instead of a generic "bad request".
"""
import json
import os
import re

_TYPE_CHECKS = {
    "string": lambda v: isinstance(v, str),
    "integer": lambda v: isinstance(v, int) and not isinstance(v, bool),
    "number": lambda v: isinstance(v, (int, float)) and not isinstance(v, bool),
    "boolean": lambda v: isinstance(v, bool),
    "object": lambda v: isinstance(v, dict),
    "array": lambda v: isinstance(v, list),
    "null": lambda v: v is None,
}

SCHEMA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "config", "schemas")
_cache = {}


def load_schema(name):
    if name not in _cache:
        with open(os.path.join(SCHEMA_DIR, name + ".json"), encoding="utf-8") as fh:
            _cache[name] = json.load(fh)
    return _cache[name]


def _type_name(value):
    for name in ("null", "boolean", "integer", "number", "string", "array", "object"):
        if _TYPE_CHECKS[name](value):
            return name
    return type(value).__name__


def validate(instance, schema, path="$"):
    """Return a list of {"path": ..., "message": ...} dicts; empty list means valid."""
    errors = []
    expected = schema.get("type")
    if expected is not None:
        types = expected if isinstance(expected, list) else [expected]
        if not any(_TYPE_CHECKS[t](instance) for t in types):
            errors.append({"path": path, "message": "expected %s, got %s" % (" or ".join(types), _type_name(instance))})
            return errors  # further keyword checks would be noise

    if "enum" in schema and instance not in schema["enum"]:
        errors.append({"path": path, "message": "must be one of %s" % json.dumps(schema["enum"])})

    if isinstance(instance, str):
        if "minLength" in schema and len(instance) < schema["minLength"]:
            errors.append({"path": path, "message": "shorter than minLength %d" % schema["minLength"]})
        if "maxLength" in schema and len(instance) > schema["maxLength"]:
            errors.append({"path": path, "message": "longer than maxLength %d" % schema["maxLength"]})
        if "pattern" in schema and not re.search(schema["pattern"], instance):
            errors.append({"path": path, "message": "does not match pattern %s" % schema["pattern"]})

    if _TYPE_CHECKS["number"](instance):
        if "minimum" in schema and instance < schema["minimum"]:
            errors.append({"path": path, "message": "less than minimum %s" % schema["minimum"]})
        if "maximum" in schema and instance > schema["maximum"]:
            errors.append({"path": path, "message": "greater than maximum %s" % schema["maximum"]})

    if isinstance(instance, dict):
        props = schema.get("properties", {})
        for key in schema.get("required", []):
            if key not in instance:
                errors.append({"path": "%s.%s" % (path, key), "message": "is required"})
        for key, value in instance.items():
            if key in props:
                errors.extend(validate(value, props[key], "%s.%s" % (path, key)))
            elif schema.get("additionalProperties") is False:
                errors.append({"path": "%s.%s" % (path, key), "message": "unexpected property"})

    if isinstance(instance, list):
        if "minItems" in schema and len(instance) < schema["minItems"]:
            errors.append({"path": path, "message": "fewer than minItems %d" % schema["minItems"]})
        if "maxItems" in schema and len(instance) > schema["maxItems"]:
            errors.append({"path": path, "message": "more than maxItems %d" % schema["maxItems"]})
        if "items" in schema:
            for i, item in enumerate(instance):
                errors.extend(validate(item, schema["items"], "%s[%d]" % (path, i)))
    return errors
