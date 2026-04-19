"""Bundle openapi.yaml + paths/*.yaml + schemas/*.yaml into dist/openapi.yaml.

The split sources keep the authoring experience clean (small per-domain files,
manageable diffs), while the bundled output is what gets published, validated,
and rendered.

Run with: ``uv run --with pyyaml python scripts/bundle.py``
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml

_CAMEL_SNAKE_RE = re.compile(r"(^|_)(.)")


def schema_name(operation_id: str) -> str:
    """Derive a PascalCase schema name from an operation id.

    Must stay in lockstep with the same function in scripts/infer_schemas.py.
    """
    base = operation_id
    if base.startswith("get_"):
        base = base[4:]
    return _CAMEL_SNAKE_RE.sub(lambda m: m.group(2).upper(), base)

ROOT = Path(__file__).resolve().parent.parent
BASE = ROOT / "openapi.yaml"
PATHS_DIR = ROOT / "paths"
SCHEMAS_DIR = ROOT / "schemas"
DIST = ROOT / "dist" / "openapi.yaml"


def main() -> None:
    base: dict = yaml.safe_load(BASE.read_text())
    base.setdefault("paths", {})
    base.setdefault("components", {}).setdefault("schemas", {})

    # Merge path files. WoW Classic uses the same URL paths as retail, distinguished
    # only by the namespace value (e.g. ``static-classic-us`` vs ``static-us``).
    # When we see a path that already exists, we treat it as the same OpenAPI
    # operation and just union the tags so both domains claim it.
    #
    # Load order matters on duplicates: retail wins on path collisions so its
    # summary/description survive, and Classic just adds its tag.
    load_order = ["wow_game_data", "wow_profile", "wow_classic", "d3", "sc2", "hearthstone"]
    path_files = sorted(
        PATHS_DIR.glob("*.yaml"),
        key=lambda p: load_order.index(p.stem) if p.stem in load_order else len(load_order),
    )
    path_count = 0
    for path_file in path_files:
        fragment = yaml.safe_load(path_file.read_text()) or {}
        for path, item in (fragment.get("paths") or {}).items():
            existing = base["paths"].get(path)
            if existing is None:
                base["paths"][path] = item
                path_count += 1
                continue
            # Duplicate — union tags on the GET operation (the only method we emit for now)
            existing_tags = set(existing.get("get", {}).get("tags", []))
            new_tags = set(item.get("get", {}).get("tags", []))
            merged_tags = sorted(existing_tags | new_tags)
            existing.setdefault("get", {})["tags"] = merged_tags

    # Merge schema files (flat: each file contributes to components.schemas)
    schema_count = 0
    for schema_file in sorted(SCHEMAS_DIR.glob("*.yaml")):
        fragment = yaml.safe_load(schema_file.read_text()) or {}
        # Accept both a raw mapping of schemas and a {components: {schemas: {...}}} form
        schemas = fragment.get("components", {}).get("schemas") if "components" in fragment else fragment
        if not isinstance(schemas, dict):
            continue
        for name, schema in schemas.items():
            if name in base["components"]["schemas"]:
                print(f"[warn] duplicate schema {name} in {schema_file.name} — keeping earlier definition")
                continue
            base["components"]["schemas"][name] = schema
            schema_count += 1

    # Wire inferred schemas into path responses. If ``components.schemas`` has
    # a schema named after the operation (PascalCase), replace the placeholder
    # ``{type: object}`` in the 200 response with a ``$ref`` pointer.
    component_schema_names = set(base["components"]["schemas"].keys())
    wired = 0
    for path, item in base["paths"].items():
        op = item.get("get", {})
        op_id = op.get("operationId")
        if not op_id:
            continue
        target = schema_name(op_id)
        if target not in component_schema_names:
            continue
        ok = op.get("responses", {}).get("200", {}).get("content", {}).get("application/json", {})
        if not ok:
            continue
        ok["schema"] = {"$ref": f"#/components/schemas/{target}"}
        wired += 1
    if wired:
        print(f"[ok] wired {wired} path responses to component schemas")

    # Disambiguate operationId collisions across different paths. Python class
    # scope hid these — e.g. ``get_auctions`` exists on both WowGameData (retail)
    # and WowClassic classes, but the endpoints and signatures differ.
    seen: dict[str, str] = {}  # operationId -> path
    for path, item in base["paths"].items():
        op = item.get("get", {})
        op_id = op.get("operationId")
        if not op_id:
            continue
        if op_id in seen and seen[op_id] != path:
            # Prefix with first tag (domain) to disambiguate
            tags = op.get("tags") or []
            prefix = tags[0].replace("-", "_") + "_" if tags else "alt_"
            new_id = f"{prefix}{op_id}"
            op["operationId"] = new_id
            seen[new_id] = path
            print(f"[info] renamed operationId {op_id!r} -> {new_id!r} on {path} (collision)")
        else:
            seen[op_id] = path

    DIST.parent.mkdir(parents=True, exist_ok=True)
    DIST.write_text(yaml.safe_dump(base, sort_keys=False, width=120))

    print(f"[ok] bundled {path_count} paths + {schema_count} schemas -> {DIST.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
