# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.1.0] - 2026-04-19

Initial public draft of an unofficial OpenAPI 3.1 specification for Blizzard's
Battle.net APIs.

### Added

- 256 paths scaffolded from the `blizzardapi3` Python wrapper covering WoW
  (Game Data, Profile, Media, Search — retail and Classic), Diablo 3 (Game
  Data and Community), StarCraft 2 (Game Data and Community), and Hearthstone.
- 199 component response schemas inferred from live Battle.net captures.
- OAuth 2.0 security schemes for the Client Credentials and Authorization Code
  flows.
- Shared parameters: `Namespace`, `Locale`, `SearchPage`, `SearchPageSize`,
  `SearchOrderBy`.
- Per-endpoint path parameter typing: integer `*_id` params with `minimum: 1`,
  slug patterns, `faction` enum, `battletag` pattern.
- `dist/openapi.yaml` — single-file bundled spec suitable for direct
  consumption.
- `scripts/scaffold_paths.py` extracts paths from wrapper source.
- `scripts/coverage_report.py` cross-checks coverage against Blizzard's
  official HTML documentation.
- `scripts/capture_samples.py` drives live response capture.
- `scripts/infer_schemas.py` produces component schemas from captures.
- `scripts/bundle.py` merges everything into `dist/openapi.yaml`, wires
  response schemas onto path operations, and disambiguates operation-ID
  collisions.
- GitHub Actions workflow `.github/workflows/ci.yml` runs scaffold + bundle
  on every push/PR, then validates with `openapi-spec-validator` and lints
  with Redocly.
- GitHub Actions workflow `.github/workflows/pages.yml` publishes the
  Redoc-rendered HTML to GitHub Pages.

### Known gaps

- 52 endpoints lack inferred response schemas — most are pre-release (The
  War Within: Midnight housing endpoints), require user-scoped profile
  auth, or depend on specific live character state (active PvP seasons,
  Mythic Keystone runs).
- Response schemas are single-sample inferences: every observed property is
  marked `required`, and `null` values produce `type: null` rather than
  nullable variants.
