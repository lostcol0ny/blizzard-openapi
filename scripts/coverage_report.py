"""Compare path coverage between Blizzard's official HTML docs and paths/*.yaml.

Blizzard's HTML documentation uses multiple placeholder conventions:
- WoW:       /data/wow/achievement/{achievementId}          ({camelCase})
- D3/SC2:    /data/d3/season/:id/leaderboard/:leaderboard   (:colonPrefixed)
- HS:        /hearthstone/cards/:idorslug                   (:colonPrefixed)

Our scaffolded paths use Python-idiomatic snake_case: ``{achievement_id}``.

To diff meaningfully we compare path *shape* by normalizing every placeholder
(any form) to the literal string ``{X}``.

Run with: ``uv run --with pyyaml python scripts/coverage_report.py``
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
PATHS_DIR = ROOT / "paths"
HTML_DIR = Path("/mnt/c/Users/TobyD/Downloads/Blizzard API Docs")
REPORT_OUT = ROOT / "docs" / "coverage_report.md"

# Lines in HTML with endpoints look like (lots of leading whitespace, then a URL path):
#   "                  /data/wow/achievement/index"
# We accept any line whose first non-whitespace token starts with a known API prefix.
ENDPOINT_PREFIXES = (
    "/data/",
    "/profile/",
    "/sc2/",
    "/hearthstone/",
    "/hs/",
    "/oauth/",
    "/wow/",   # legacy/unlikely but harmless
    "/d3/",
)
PATH_LINE = re.compile(r"^\s*(/[A-Za-z0-9_:{}/\.\-]+)\s*$")

# Normalize any placeholder to {X}:
#   {camelCase}  -> {X}
#   :word        -> {X}
PLACEHOLDER = re.compile(r"\{[^}]+\}|:[A-Za-z_][A-Za-z0-9_]*")


def normalize(path: str) -> str:
    # Strip trailing slash (Blizzard inconsistently shows ``/d3/season/`` with and without)
    p = path.rstrip("/") or "/"
    return PLACEHOLDER.sub("{X}", p)


def extract_html_paths(html_file: Path) -> set[str]:
    paths: set[str] = set()
    for line in html_file.read_text(encoding="utf-8", errors="ignore").splitlines():
        m = PATH_LINE.match(line)
        if not m:
            continue
        raw = m.group(1)
        if not raw.startswith(ENDPOINT_PREFIXES):
            continue
        paths.add(raw)
    return paths


def extract_scaffold_paths() -> dict[str, set[str]]:
    """Return {domain: {raw_path, ...}} from paths/*.yaml."""
    by_domain: dict[str, set[str]] = {}
    for path_file in sorted(PATHS_DIR.glob("*.yaml")):
        fragment = yaml.safe_load(path_file.read_text()) or {}
        by_domain[path_file.stem] = set((fragment.get("paths") or {}).keys())
    return by_domain


def main() -> None:
    scaffold_by_domain = extract_scaffold_paths()
    all_scaffold_normalized = {normalize(p) for paths in scaffold_by_domain.values() for p in paths}

    # Group HTML files by their primary domain(s) — the coverage diff is most
    # useful reported per-file so gaps can be actioned one doc at a time.
    # We don't enforce a strict domain mapping; we just list what's missing
    # from the overall scaffold.
    report: list[str] = ["# Coverage report", ""]
    report.append(f"Scaffold contains **{len(all_scaffold_normalized)}** unique path shapes across {len(scaffold_by_domain)} domain files.")
    report.append("")

    total_html = 0
    total_missing = 0
    for html_file in sorted(HTML_DIR.glob("*.htm")):
        html_paths_raw = extract_html_paths(html_file)
        if not html_paths_raw:
            continue
        html_normalized = {normalize(p) for p in html_paths_raw}
        missing = sorted(html_normalized - all_scaffold_normalized)
        total_html += len(html_normalized)
        total_missing += len(missing)
        report.append(f"## {html_file.stem.replace(' _ Documentation', '')}")
        report.append(f"- HTML endpoints: **{len(html_normalized)}**")
        report.append(f"- Missing from scaffold: **{len(missing)}**")
        if missing:
            report.append("")
            report.append("Missing paths (normalized):")
            report.append("")
            for p in missing:
                # Find the raw form from the HTML for readability
                raw_hits = sorted(r for r in html_paths_raw if normalize(r) == p)
                raw_display = raw_hits[0] if raw_hits else p
                report.append(f"- `{raw_display}`")
            report.append("")
        else:
            report.append("")

    report.insert(
        2,
        f"Across all HTML docs: **{total_html}** distinct path shapes, **{total_missing}** absent from scaffold.",
    )

    REPORT_OUT.parent.mkdir(parents=True, exist_ok=True)
    REPORT_OUT.write_text("\n".join(report) + "\n")

    print(f"[ok] wrote coverage report -> {REPORT_OUT.relative_to(ROOT)}")
    print(f"      total HTML paths: {total_html}, missing from scaffold: {total_missing}")


if __name__ == "__main__":
    main()
