"""Capture representative response samples from Blizzard's live APIs.

Requires env vars BLIZZARD_CLIENT_ID and BLIZZARD_CLIENT_SECRET. Uses the
client-credentials OAuth flow — that's all most endpoints need. User-scoped
``/profile/user/...`` endpoints are skipped since they'd require a user
access_token we don't have.

Representative scope: one example per endpoint shape. We pick well-known IDs
(Illidan = connected realm 57, Warrior = class 1, etc.) so responses are
stable and diffable across runs.

Saves raw JSON to ``samples/<domain>/<operation_id>.json``. Schema inference
runs as a separate step (scripts/infer_schemas.py) so it can be re-run without
re-hitting the API.

Run with: ``uv run --with httpx python scripts/capture_samples.py``
"""

from __future__ import annotations

import json
import os
import sys
import time
from dataclasses import dataclass
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
SAMPLES_DIR = ROOT / "samples"

REGION = os.environ.get("BLIZZARD_REGION", "us")
BASE_URL = f"https://{REGION}.api.blizzard.com"
TOKEN_URL = "https://oauth.battle.net/token"

# Test character used throughout profile samples
TEST_CHARACTER_REALM = "illidan"
TEST_CHARACTER_NAME = "beyloc"


@dataclass
class Sample:
    """One endpoint call to make. ``params`` is what we send on the wire."""
    operation_id: str
    path: str
    namespace: str | None  # full namespace value (e.g. "static-us"), None if no namespace
    params: dict[str, str] | None = None


# --- Curated endpoint list -----------------------------------------------------
#
# One representative call per path shape, grouped by domain. Every shape we
# capture here becomes a named schema in the final spec. Paths not listed here
# remain scaffolded-only (typed as ``object``) until we extend the capture set.

WOW_GAME_DATA_SAMPLES: list[Sample] = [
    Sample("get_achievement_categories_index", "/data/wow/achievement-category/index", "static-us"),
    Sample("get_achievement_category", "/data/wow/achievement-category/81", "static-us"),  # Statistics
    Sample("get_achievements_index", "/data/wow/achievement/index", "static-us"),
    Sample("get_achievement", "/data/wow/achievement/6", "static-us"),  # Level 10
    Sample("get_achievement_media", "/data/wow/media/achievement/6", "static-us"),
    Sample("get_connected_realms_index", "/data/wow/connected-realm/index", "dynamic-us"),
    Sample("get_connected_realm", "/data/wow/connected-realm/57", "dynamic-us"),  # Illidan cluster
    Sample("get_realms_index", "/data/wow/realm/index", "dynamic-us"),
    Sample("get_realm", "/data/wow/realm/illidan", "dynamic-us"),
    Sample("get_regions_index", "/data/wow/region/index", "dynamic-us"),
    Sample("get_region", "/data/wow/region/1", "dynamic-us"),  # US
    Sample("get_playable_classes_index", "/data/wow/playable-class/index", "static-us"),
    Sample("get_playable_class", "/data/wow/playable-class/1", "static-us"),  # Warrior
    Sample("get_playable_races_index", "/data/wow/playable-race/index", "static-us"),
    Sample("get_playable_race", "/data/wow/playable-race/1", "static-us"),  # Human
    Sample("get_item_classes_index", "/data/wow/item-class/index", "static-us"),
    Sample("get_item_class", "/data/wow/item-class/2", "static-us"),  # Weapon
    Sample("get_item", "/data/wow/item/19019", "static-us"),  # Thunderfury
    Sample("get_item_media", "/data/wow/media/item/19019", "static-us"),
    Sample("get_mounts_index", "/data/wow/mount/index", "static-us"),
    Sample("get_mount", "/data/wow/mount/6", "static-us"),  # Brown Horse
    Sample("get_pets_index", "/data/wow/pet/index", "static-us"),
    Sample("get_pet", "/data/wow/pet/39", "static-us"),
    Sample("get_token_index", "/data/wow/token/index", "dynamic-us"),
    Sample("get_commodities", "/data/wow/auctions/commodities", "dynamic-us"),
]

WOW_PROFILE_SAMPLES: list[Sample] = [
    # Character endpoints — use Beyloc-Illidan. profile namespace + client-credentials token works.
    Sample(
        "get_character_profile_summary",
        f"/profile/wow/character/{TEST_CHARACTER_REALM}/{TEST_CHARACTER_NAME}",
        "profile-us",
    ),
    Sample(
        "get_character_achievements_summary",
        f"/profile/wow/character/{TEST_CHARACTER_REALM}/{TEST_CHARACTER_NAME}/achievements",
        "profile-us",
    ),
    Sample(
        "get_character_appearance_summary",
        f"/profile/wow/character/{TEST_CHARACTER_REALM}/{TEST_CHARACTER_NAME}/appearance",
        "profile-us",
    ),
    Sample(
        "get_character_equipment_summary",
        f"/profile/wow/character/{TEST_CHARACTER_REALM}/{TEST_CHARACTER_NAME}/equipment",
        "profile-us",
    ),
    Sample(
        "get_character_statistics_summary",
        f"/profile/wow/character/{TEST_CHARACTER_REALM}/{TEST_CHARACTER_NAME}/statistics",
        "profile-us",
    ),
    Sample(
        "get_character_specializations_summary",
        f"/profile/wow/character/{TEST_CHARACTER_REALM}/{TEST_CHARACTER_NAME}/specializations",
        "profile-us",
    ),
    Sample(
        "get_character_media_summary",
        f"/profile/wow/character/{TEST_CHARACTER_REALM}/{TEST_CHARACTER_NAME}/character-media",
        "profile-us",
    ),
    Sample(
        "get_character_collections_index",
        f"/profile/wow/character/{TEST_CHARACTER_REALM}/{TEST_CHARACTER_NAME}/collections",
        "profile-us",
    ),
    Sample(
        "get_character_mounts_collection_summary",
        f"/profile/wow/character/{TEST_CHARACTER_REALM}/{TEST_CHARACTER_NAME}/collections/mounts",
        "profile-us",
    ),
    Sample(
        "get_character_pets_collection_summary",
        f"/profile/wow/character/{TEST_CHARACTER_REALM}/{TEST_CHARACTER_NAME}/collections/pets",
        "profile-us",
    ),
    Sample(
        "get_guild",
        "/data/wow/guild/illidan/blood-legion",
        "profile-us",
    ),
]

WOW_CLASSIC_SAMPLES: list[Sample] = [
    # Intentionally empty: Classic shares URL paths with retail, distinguished only
    # by namespace value. The bundler merges those paths into one OpenAPI operation
    # tagged for both domains — a separate Classic schema would orphan because
    # no path carries a Classic-specific operationId. Classic responses conform
    # to the same shapes as retail for the endpoints we cover.
]

D3_SAMPLES: list[Sample] = [
    # No namespace — just locale.
    Sample("get_era_index", "/data/d3/era/", None),
    Sample("get_season_index", "/data/d3/season/", None),
    Sample("get_season", "/data/d3/season/27", None),
]

SC2_SAMPLES: list[Sample] = [
    # Some endpoints need a region id (1=US, 2=EU, 3=KR, 5=CN). Use 1.
    Sample("get_league_data", "/data/sc2/league/65/201/0/6", None),
    # Leaderboard endpoints that don't need profile.
    Sample("get_grandmaster_leaderboard", "/sc2/ladder/grandmaster/1", None),
    Sample("sc2_get_season", "/sc2/ladder/season/1", None),
]

HEARTHSTONE_SAMPLES: list[Sample] = [
    Sample("search_cards", "/hearthstone/cards", None, params={"set": "rise-of-shadows", "page": "1", "pageSize": "5"}),
    Sample("get_card", "/hearthstone/cards/52119-arch-villain-rafaam", None),
    Sample("search_card_backs", "/hearthstone/cardbacks", None, params={"page": "1", "pageSize": "5"}),
    Sample("get_metadata", "/hearthstone/metadata", None),
    # /hearthstone/metadata/{metadata_type} — ``sets`` is representative.
    Sample("get_metadata_type", "/hearthstone/metadata/sets", None),
]


DOMAIN_SAMPLES = {
    "wow_game_data": WOW_GAME_DATA_SAMPLES,
    "wow_profile": WOW_PROFILE_SAMPLES,
    "wow_classic": WOW_CLASSIC_SAMPLES,
    "d3": D3_SAMPLES,
    "sc2": SC2_SAMPLES,
    "hearthstone": HEARTHSTONE_SAMPLES,
}


# --- Execution -----------------------------------------------------------------

def get_access_token(client_id: str, client_secret: str) -> str:
    resp = httpx.post(
        TOKEN_URL,
        data={"grant_type": "client_credentials"},
        auth=(client_id, client_secret),
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()["access_token"]


def fetch_sample(client: httpx.Client, sample: Sample, token: str, locale: str = "en_US") -> dict:
    params: dict[str, str] = {"locale": locale}
    if sample.namespace is not None:
        params["namespace"] = sample.namespace
    if sample.params:
        params.update(sample.params)
    headers = {"Authorization": f"Bearer {token}"}
    resp = client.get(BASE_URL + sample.path, params=params, headers=headers, timeout=30)
    return {
        "request": {
            "path": sample.path,
            "params": params,
            "operation_id": sample.operation_id,
        },
        "response": {
            "status": resp.status_code,
            "headers": {k: v for k, v in resp.headers.items() if k.lower() in {
                "content-type", "x-ratelimit-limit", "x-ratelimit-remaining",
                "last-modified", "cache-control",
            }},
            "body": _try_json(resp),
        },
    }


def _try_json(resp: httpx.Response) -> object:
    try:
        return resp.json()
    except Exception:
        return {"_raw": resp.text[:1000]}


def main() -> None:
    client_id = os.environ.get("BLIZZARD_CLIENT_ID")
    client_secret = os.environ.get("BLIZZARD_CLIENT_SECRET")
    if not client_id or not client_secret:
        print("ERROR: set BLIZZARD_CLIENT_ID and BLIZZARD_CLIENT_SECRET in env", file=sys.stderr)
        sys.exit(1)

    token = get_access_token(client_id, client_secret)
    print(f"[ok] got token (len={len(token)})")

    SAMPLES_DIR.mkdir(parents=True, exist_ok=True)

    totals: dict[str, tuple[int, int]] = {}
    with httpx.Client(http2=False) as client:
        for domain, samples in DOMAIN_SAMPLES.items():
            domain_dir = SAMPLES_DIR / domain
            domain_dir.mkdir(parents=True, exist_ok=True)
            ok = 0
            fail = 0
            for s in samples:
                try:
                    capture = fetch_sample(client, s, token)
                except httpx.HTTPError as e:
                    print(f"[err]  {domain}/{s.operation_id}: {e}")
                    fail += 1
                    continue
                status = capture["response"]["status"]
                out = domain_dir / f"{s.operation_id}.json"
                out.write_text(json.dumps(capture, indent=2, ensure_ascii=False))
                marker = "ok" if 200 <= status < 300 else f"{status}"
                print(f"[{marker:>3}] {domain}/{s.operation_id}")
                if 200 <= status < 300:
                    ok += 1
                else:
                    fail += 1
                time.sleep(0.05)  # gentle on the rate limit
            totals[domain] = (ok, fail)

    print("\nSummary:")
    for domain, (ok, fail) in totals.items():
        print(f"  {domain:15s} ok={ok} fail={fail}")


if __name__ == "__main__":
    main()
