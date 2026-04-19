"""Infer OpenAPI 3.1 JSON Schemas from captured response samples.

Walks ``samples/<domain>/*.json``, extracts each response body, and infers
a schema. Emits one ``schemas/<domain>.yaml`` per domain with named component
schemas keyed by PascalCase operation name.

Design notes:

- We intentionally own the inference logic rather than delegating to genson or
  datamodel-code-generator. The dataset is small, bounded, and non-adversarial;
  a hand-rolled inferrer lets us produce OpenAPI-ready output directly, handle
  Blizzard-specific conventions (HATEOAS ``_links``, ``key.href``, localized
  string tables), and tune edge cases in one place.
- Single-sample limitation: every key we observe is marked ``required``. If we
  later capture multiple samples per endpoint, we'd merge and relax keys that
  are absent in any sample. For now, required accurately reflects what we saw.
- Nullability: a key whose value is ``null`` in the sample gets ``type: null``.
  This is wrong for fields that are sometimes-null, sometimes-present —
  acceptable for an inference-based draft; consumers can override.

Run with: ``uv run --with pyyaml python scripts/infer_schemas.py``
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parent.parent
SAMPLES_DIR = ROOT / "samples"
SCHEMAS_DIR = ROOT / "schemas"


# --- Schema inference ---------------------------------------------------------

def infer(value: Any) -> dict[str, Any]:
    """Infer an OpenAPI 3.1 schema for a single value."""
    if value is None:
        return {"type": "null"}
    if isinstance(value, bool):  # must come before int — bool is a subclass of int
        return {"type": "boolean"}
    if isinstance(value, int):
        return {"type": "integer"}
    if isinstance(value, float):
        return {"type": "number"}
    if isinstance(value, str):
        return {"type": "string"}
    if isinstance(value, list):
        return _infer_array(value)
    if isinstance(value, dict):
        return _infer_object(value)
    return {}  # unknown — omit type


def _infer_array(values: list[Any]) -> dict[str, Any]:
    if not values:
        return {"type": "array", "items": {}}
    element_schemas = [infer(v) for v in values]
    items = _union_schemas(element_schemas)
    return {"type": "array", "items": items}


def _infer_object(obj: dict[str, Any]) -> dict[str, Any]:
    properties: dict[str, dict[str, Any]] = {}
    for key, val in obj.items():
        properties[key] = infer(val)
    schema: dict[str, Any] = {"type": "object", "properties": properties}
    # Single-sample: every observed key is required. (See module docstring.)
    if properties:
        schema["required"] = sorted(properties.keys())
    return schema


def _union_schemas(schemas: list[dict[str, Any]]) -> dict[str, Any]:
    """Merge a list of schemas into one schema describing their union.

    If all schemas are identical, return the first. If all are objects, merge
    property schemas per-key (recursively). Otherwise, fall back to ``oneOf``.
    """
    # Deduplicate
    unique: list[dict[str, Any]] = []
    for s in schemas:
        if s not in unique:
            unique.append(s)
    if len(unique) == 1:
        return unique[0]

    # All objects — merge field-wise. Properties seen in every sample are required;
    # properties seen in only some are optional (not in required list).
    if all(s.get("type") == "object" for s in unique):
        return _merge_object_schemas(unique)

    # All arrays — union their item schemas.
    if all(s.get("type") == "array" for s in unique):
        items = _union_schemas([s.get("items", {}) for s in unique])
        return {"type": "array", "items": items}

    # Mixed — OpenAPI 3.1 lets us use an array-valued type for simple unions.
    # Pull out any schemas that are bare ``{type: X}`` or ``{type: [X, Y]}``
    # (the latter is possible when a previous union already collapsed); if
    # every schema in the union is that simple, merge their types.
    if all(set(s.keys()) == {"type"} for s in unique):
        merged: set[str] = set()
        for s in unique:
            t = s.get("type")
            if isinstance(t, list):
                merged.update(x for x in t if isinstance(x, str))
            elif isinstance(t, str):
                merged.add(t)
        if len(merged) == 1:
            return {"type": next(iter(merged))}
        return {"type": sorted(merged)}

    return {"oneOf": unique}


def _merge_object_schemas(schemas: list[dict[str, Any]]) -> dict[str, Any]:
    merged_props: dict[str, list[dict[str, Any]]] = {}
    required_sets: list[set[str]] = []
    for s in schemas:
        props = s.get("properties", {}) or {}
        required_sets.append(set(s.get("required", []) or []))
        for key, pschema in props.items():
            merged_props.setdefault(key, []).append(pschema)

    properties: dict[str, dict[str, Any]] = {}
    for key, pschemas in merged_props.items():
        properties[key] = _union_schemas(pschemas)

    # Required = intersection of required across samples, limited to keys we actually know about
    required = sorted(set.intersection(*required_sets)) if required_sets else []
    out: dict[str, Any] = {"type": "object", "properties": properties}
    if required:
        out["required"] = required
    return out


# --- Naming -------------------------------------------------------------------

_CAMEL_SNAKE_RE = re.compile(r"(^|_)(.)")


def schema_name(operation_id: str) -> str:
    """Derive a PascalCase schema name from an operation id."""
    # Drop common noise suffixes
    base = operation_id
    for suffix in ("_response",):
        if base.endswith(suffix):
            base = base[: -len(suffix)]
    # Drop ``get_`` prefix when present — every GET endpoint is "getting" something
    if base.startswith("get_"):
        base = base[4:]
    return _CAMEL_SNAKE_RE.sub(lambda m: m.group(2).upper(), base)


# --- Main ---------------------------------------------------------------------

def main() -> None:
    SCHEMAS_DIR.mkdir(parents=True, exist_ok=True)
    total_schemas = 0
    for domain_dir in sorted(SAMPLES_DIR.iterdir()):
        if not domain_dir.is_dir():
            continue
        schemas: dict[str, dict[str, Any]] = {}
        for sample_file in sorted(domain_dir.glob("*.json")):
            data = json.loads(sample_file.read_text())
            body = data.get("response", {}).get("body")
            if body is None:
                continue
            name = schema_name(data["request"]["operation_id"])
            schemas[name] = infer(body)

        if not schemas:
            print(f"[skip] {domain_dir.name}: no samples -> not writing file")
            continue
        out = SCHEMAS_DIR / f"{domain_dir.name}.yaml"
        out.write_text(yaml.safe_dump(schemas, sort_keys=True, width=120, allow_unicode=True))
        print(f"[ok]   {domain_dir.name}: {len(schemas)} schemas -> schemas/{domain_dir.name}.yaml")
        total_schemas += len(schemas)

    print(f"\nTotal: {total_schemas} schemas")


if __name__ == "__main__":
    main()
