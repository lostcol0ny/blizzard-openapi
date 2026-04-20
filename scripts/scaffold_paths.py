"""Extract OpenAPI path entries from blizzardapi3's API modules.

Walks each ``blizzardapi3/api/*.py`` module, finds every non-private method
on the main class, and extracts:

- HTTP path (string literal or f-string) from the return expression
- Namespace type (``static`` / ``dynamic`` / ``profile`` / none) from the helper call
- Path parameters from f-string ``{name}`` tokens
- Extra query parameters passed as ``**extra`` kwargs to the helper
- Summary from the method's docstring first line

Emits one ``paths/<module>.yaml`` file per module. These files are meant to be
bundled into the main ``openapi.yaml`` via ``$ref`` by ``scripts/bundle.py``.

Run with: ``uv run --with pyyaml python scripts/scaffold_paths.py``
"""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml

BLIZZARDAPI3_API = Path("/home/toby/projects/blizzardapi3/blizzardapi3/api")
OUT_DIR = Path(__file__).resolve().parent.parent / "paths"

MODULE_TAG = {
    "wow_game_data": "wow-game-data",
    "wow_profile": "wow-profile",
    "wow_classic": "wow-classic",
    # d3 and sc2 split into "game-data" vs "community" by URL prefix; see tag_for_path.
    "d3": "diablo3-game-data",
    "sc2": "sc2-community",
    "hearthstone": "hearthstone",
}


def tag_for_path(module_name: str, path: str) -> str:
    """Pick the OpenAPI tag for a given (module, path) pair.

    Blizzard splits D3 and SC2 into "Game Data" and "Community" categories,
    but blizzardapi3 keeps them in single modules. Re-derive the split from
    URL structure so Redoc groups endpoints under the declared tags.
    """
    if module_name == "d3":
        # Profile endpoints are the community API; everything under /d3/data
        # or /data/d3 is game data.
        if path.startswith("/d3/profile"):
            return "diablo3-community"
        return "diablo3-game-data"
    if module_name == "sc2":
        # Only the league data under /data/sc2 is game data; the rest
        # (profile, ladder, metadata, player, legacy) is the community API.
        if path.startswith("/data/sc2"):
            return "sc2-game-data"
        return "sc2-community"
    return MODULE_TAG.get(module_name, module_name)

# Maps helper name -> (namespace_type, needs_profile_auth). ``None`` namespace
# means the game has no namespace concept (d3 / sc2 / hearthstone).
HELPER_NAMESPACE = {
    "_static_get": ("static", False),
    "_static_get_async": ("static", False),
    "_dynamic_get": ("dynamic", False),
    "_dynamic_get_async": ("dynamic", False),
    "_profile_get": ("profile", True),
    "_profile_get_async": ("profile", True),
    "_get": (None, False),
    "_get_async": (None, False),
}


@dataclass
class Endpoint:
    operation_id: str
    summary: str
    path: str
    namespace_type: str | None
    needs_profile_auth: bool
    path_params: list[str] = field(default_factory=list)
    extra_query_params: list[str] = field(default_factory=list)


def _extract_fstring_path(node: ast.AST) -> tuple[str, list[str]] | None:
    """Return (templated_path, [param_names]) from a string or f-string AST node.

    For plain strings, returns the literal and an empty list. For JoinedStr
    (f-string), reconstructs the path with ``{name}`` placeholders and records
    the param names.

    Drops ``.lower()`` / ``.upper()`` method calls on the substitution — those
    are normalization shims in the client, not part of the API surface.
    """
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value, []
    if isinstance(node, ast.JoinedStr):
        parts: list[str] = []
        params: list[str] = []
        for value in node.values:
            if isinstance(value, ast.Constant) and isinstance(value.value, str):
                parts.append(value.value)
            elif isinstance(value, ast.FormattedValue):
                name = _name_from_expr(value.value)
                if name is None:
                    return None
                parts.append("{" + name + "}")
                params.append(name)
            else:
                return None
        return "".join(parts), params
    return None


def _name_from_expr(expr: ast.AST) -> str | None:
    """Extract a parameter name from an f-string substitution expression."""
    # Strip .lower() / .upper() wrappers
    if isinstance(expr, ast.Call) and isinstance(expr.func, ast.Attribute) and expr.func.attr in {"lower", "upper"}:
        return _name_from_expr(expr.func.value)
    if isinstance(expr, ast.Name):
        return expr.id
    return None


def _find_helper_call(body: list[ast.stmt]) -> ast.Call | None:
    """Find the inner helper call (e.g. ``self._static_get(...)``) in a method body.

    Handles both:
        return self._static_get(...)
        return await self._static_get_async(...)
    """
    for stmt in body:
        if isinstance(stmt, ast.Return) and stmt.value is not None:
            value: ast.AST = stmt.value
            if isinstance(value, ast.Await):
                value = value.value
            if isinstance(value, ast.Call):
                func = value.func
                if isinstance(func, ast.Attribute) and func.attr in HELPER_NAMESPACE:
                    return value
    return None


def _extract_endpoint(method: ast.FunctionDef | ast.AsyncFunctionDef) -> Endpoint | None:
    if method.name.startswith("_"):
        return None
    call = _find_helper_call(method.body)
    if call is None:
        return None
    # call.args: (region, locale, path, ...positional extras are uncommon)
    # call.keywords: extra=... kwargs
    if len(call.args) < 3:
        return None
    path_result = _extract_fstring_path(call.args[2])
    if path_result is None:
        return None
    path, path_params = path_result
    helper_name = call.func.attr  # type: ignore[attr-defined]
    namespace_type, needs_auth = HELPER_NAMESPACE[helper_name]
    extra_query = [kw.arg for kw in call.keywords if kw.arg]

    operation_id = method.name.removesuffix("_async")
    docstring = ast.get_docstring(method) or ""
    summary = docstring.strip().split("\n", 1)[0] if docstring else operation_id.replace("_", " ").title()

    return Endpoint(
        operation_id=operation_id,
        summary=summary,
        path=path,
        namespace_type=namespace_type,
        needs_profile_auth=needs_auth,
        path_params=path_params,
        extra_query_params=extra_query,
    )


def parse_module(module_path: Path) -> list[Endpoint]:
    tree = ast.parse(module_path.read_text())
    endpoints: dict[str, Endpoint] = {}  # operation_id -> Endpoint (dedupe sync/async)
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef):
            for item in node.body:
                if isinstance(item, ast.FunctionDef | ast.AsyncFunctionDef):
                    ep = _extract_endpoint(item)
                    if ep and ep.operation_id not in endpoints:
                        endpoints[ep.operation_id] = ep
    return list(endpoints.values())


# Path-param schema refinements. Name-based because blizzardapi3 doesn't
# carry richer type info through to the helper calls.
_SLUG_PATTERN = r"^[a-z0-9-]+$"
# battletag is URL-encoded as `Name-1234` (the `#` becomes `-`). Names can
# contain letters/digits but not hyphens themselves; the discriminator is
# 4-6 digits in practice.
_BATTLETAG_PATTERN = r"^[^-]+-\d{4,6}$"

_ENUM_PATH_PARAMS: dict[str, list[str]] = {
    "faction": ["alliance", "horde"],
}


def _path_param_schema(name: str) -> dict:
    """Infer a schema for a path parameter by name.

    Conservative: only claim an enum/pattern when Blizzard's conventions make
    it safe. Anything unrecognized falls back to bare ``string``.
    """
    lower = name.lower()
    if lower in _ENUM_PATH_PARAMS:
        return {"type": "string", "enum": _ENUM_PATH_PARAMS[lower]}
    if lower.endswith("_id") or lower == "id":
        return {"type": "integer", "minimum": 1}
    if lower.endswith("_slug") or lower == "slug":
        return {"type": "string", "pattern": _SLUG_PATTERN}
    if lower == "battletag":
        return {"type": "string", "pattern": _BATTLETAG_PATTERN}
    return {"type": "string"}


def _is_search_path(path: str) -> bool:
    """True if this path is one of Blizzard's search endpoints.

    All search endpoints accept the standard pagination/ordering query params
    (`_page`, `_pageSize`, `orderby`) plus arbitrary field filters.
    """
    return "/search/" in path


def build_path_item(endpoint: Endpoint, tag: str) -> dict:
    """Build the OpenAPI path item (GET operation) for one endpoint."""
    parameters: list[dict] = []

    # Path parameters (typed by name heuristic)
    for name in endpoint.path_params:
        parameters.append(
            {
                "name": name,
                "in": "path",
                "required": True,
                "schema": _path_param_schema(name),
            }
        )

    # Namespace query parameter (only for WoW APIs)
    if endpoint.namespace_type is not None:
        parameters.append({"$ref": "#/components/parameters/Namespace"})
    # Locale is always present
    parameters.append({"$ref": "#/components/parameters/Locale"})

    # Search endpoints accept standard pagination + ordering params
    if _is_search_path(endpoint.path):
        parameters.append({"$ref": "#/components/parameters/SearchPage"})
        parameters.append({"$ref": "#/components/parameters/SearchPageSize"})
        parameters.append({"$ref": "#/components/parameters/SearchOrderBy"})

    # Extra query params (e.g. access_token, or filter params like _page / _pageSize)
    for name in endpoint.extra_query_params:
        if name == "access_token":
            continue  # modeled via security scheme
        parameters.append(
            {
                "name": name,
                "in": "query",
                "required": False,
                "schema": {"type": "string"},
            }
        )

    operation: dict = {
        "operationId": endpoint.operation_id,
        "summary": endpoint.summary,
        "tags": [tag],
        "parameters": parameters,
        "responses": {
            "200": {
                "description": "Successful response",
                "content": {"application/json": {"schema": {"type": "object"}}},
            },
            "401": {"description": "Unauthorized"},
            "404": {"description": "Not found"},
        },
    }

    if endpoint.needs_profile_auth:
        operation["security"] = [{"AuthorizationCode": ["wow.profile"]}]

    return {"get": operation}


def build_paths_document(module_name: str, endpoints: list[Endpoint]) -> dict:
    paths: dict[str, dict] = {}
    for ep in endpoints:
        tag = tag_for_path(module_name, ep.path)
        # Merge entries that differ only by operationId collision (shouldn't happen after dedupe)
        existing = paths.get(ep.path)
        if existing is None:
            paths[ep.path] = build_path_item(ep, tag)
        else:
            # Extremely unlikely — would need two GET operations on the same path
            existing["get"]["operationId"] += f"_or_{ep.operation_id}"
    # Top-level wrapper so these files are valid standalone fragments
    return {"paths": paths}


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    summary_lines: list[str] = []
    for module_name in sorted(MODULE_TAG):
        module_path = BLIZZARDAPI3_API / f"{module_name}.py"
        if not module_path.exists():
            print(f"[skip] {module_name} — file missing")
            continue
        endpoints = parse_module(module_path)
        doc = build_paths_document(module_name, endpoints)
        out_path = OUT_DIR / f"{module_name}.yaml"
        out_path.write_text(yaml.safe_dump(doc, sort_keys=False, width=120))
        summary_lines.append(f"  {module_name}: {len(endpoints)} endpoints -> {out_path.relative_to(OUT_DIR.parent)}")
        print(f"[ok]   {module_name}: {len(endpoints)} endpoints")

    print("\nSummary:")
    print("\n".join(summary_lines))


if __name__ == "__main__":
    main()
