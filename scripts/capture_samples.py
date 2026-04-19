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
    Sample("get_auctions", "/data/wow/connected-realm/57/auctions", "dynamic-us"),
    Sample("get_azerite_essences_index", "/data/wow/azerite-essence/index", "static-us"),
    Sample("get_azerite_essence", "/data/wow/azerite-essence/2", "static-us"),  # Azeroth's Undying Gift
    Sample("get_azerite_essence_media", "/data/wow/media/azerite-essence/2", "static-us"),
    Sample("get_connected_realms_index", "/data/wow/connected-realm/index", "dynamic-us"),
    Sample("get_connected_realm", "/data/wow/connected-realm/57", "dynamic-us"),  # Illidan cluster
    Sample("get_covenant_index", "/data/wow/covenant/index", "static-us"),
    Sample("get_covenant", "/data/wow/covenant/1", "static-us"),  # Kyrian
    Sample("get_covenant_media", "/data/wow/media/covenant/1", "static-us"),
    Sample("get_soulbind_index", "/data/wow/covenant/soulbind/index", "static-us"),
    Sample("get_soulbind", "/data/wow/covenant/soulbind/7", "static-us"),  # Pelagos
    Sample("get_conduit_index", "/data/wow/covenant/conduit/index", "static-us"),
    Sample("get_conduit", "/data/wow/covenant/conduit/11", "static-us"),
    Sample("get_creature_families_index", "/data/wow/creature-family/index", "static-us"),
    Sample("get_creature_family", "/data/wow/creature-family/1", "static-us"),  # Wolf
    Sample("get_creature_family_media", "/data/wow/media/creature-family/1", "static-us"),
    Sample("get_creature_types_index", "/data/wow/creature-type/index", "static-us"),
    Sample("get_creature_type", "/data/wow/creature-type/1", "static-us"),  # Beast
    Sample("get_creature", "/data/wow/creature/42722", "static-us"),  # Cho'gall
    Sample("get_creature_display_media", "/data/wow/media/creature-display/30221", "static-us"),
    Sample("get_guild_crest_components_index", "/data/wow/guild-crest/index", "static-us"),
    Sample("get_guild_crest_border_media", "/data/wow/media/guild-crest/border/0", "static-us"),
    Sample("get_guild_crest_emblem_media", "/data/wow/media/guild-crest/emblem/0", "static-us"),
    Sample("get_heirloom_index", "/data/wow/heirloom/index", "static-us"),
    Sample("get_heirloom", "/data/wow/heirloom/1", "static-us"),
    Sample("get_item_appearance", "/data/wow/item-appearance/1", "static-us"),
    Sample("get_item_appearance_set", "/data/wow/item-appearance/set/1", "static-us"),
    Sample("get_item_classes_index", "/data/wow/item-class/index", "static-us"),
    Sample("get_item_class", "/data/wow/item-class/2", "static-us"),  # Weapon
    Sample("get_item_sets_index", "/data/wow/item-set/index", "static-us"),
    Sample("get_item_set", "/data/wow/item-set/1", "static-us"),
    Sample("get_item_subclass", "/data/wow/item-class/2/item-subclass/1", "static-us"),  # Weapon/Axe2H
    Sample("get_item", "/data/wow/item/19019", "static-us"),  # Thunderfury
    Sample("get_item_media", "/data/wow/media/item/19019", "static-us"),
    Sample("get_journal_expansions_index", "/data/wow/journal-expansion/index", "static-us"),
    Sample("get_journal_expansion", "/data/wow/journal-expansion/68", "static-us"),  # Legion
    Sample("get_journal_encounters_index", "/data/wow/journal-encounter/index", "static-us"),
    Sample("get_journal_encounter", "/data/wow/journal-encounter/89", "static-us"),  # Ragnaros
    Sample("get_journal_instances_index", "/data/wow/journal-instance/index", "static-us"),
    Sample("get_journal_instance", "/data/wow/journal-instance/63", "static-us"),  # Naxxramas
    Sample("get_journal_instance_media", "/data/wow/media/journal-instance/63", "static-us"),
    Sample("get_modified_crafting_index", "/data/wow/modified-crafting/index", "static-us"),
    Sample("get_modified_crafting_category_index", "/data/wow/modified-crafting/category/index", "static-us"),
    Sample("get_modified_crafting_category", "/data/wow/modified-crafting/category/2", "static-us"),
    Sample("get_modified_crafting_reagent_slot_type_index", "/data/wow/modified-crafting/reagent-slot-type/index", "static-us"),
    Sample("get_modified_crafting_reagent_slot_type", "/data/wow/modified-crafting/reagent-slot-type/3", "static-us"),
    Sample("get_mounts_index", "/data/wow/mount/index", "static-us"),
    Sample("get_mount", "/data/wow/mount/6", "static-us"),  # Brown Horse
    Sample("get_mythic_keystone_affixes_index", "/data/wow/keystone-affix/index", "static-us"),
    Sample("get_mythic_keystone_affix", "/data/wow/keystone-affix/1", "static-us"),  # Overflowing
    Sample("get_mythic_keystone_affix_media", "/data/wow/media/keystone-affix/1", "static-us"),
    Sample("get_mythic_keystone_dungeons_index", "/data/wow/mythic-keystone/dungeon/index", "dynamic-us"),
    Sample("get_mythic_keystone_dungeon", "/data/wow/mythic-keystone/dungeon/353", "dynamic-us"),  # Siege of Boralus
    Sample("get_mythic_keystone_index", "/data/wow/mythic-keystone/index", "dynamic-us"),
    Sample("get_mythic_keystone_periods_index", "/data/wow/mythic-keystone/period/index", "dynamic-us"),
    Sample("get_mythic_keystone_period", "/data/wow/mythic-keystone/period/641", "dynamic-us"),
    Sample("get_mythic_keystone_seasons_index", "/data/wow/mythic-keystone/season/index", "dynamic-us"),
    Sample("get_mythic_keystone_season", "/data/wow/mythic-keystone/season/1", "dynamic-us"),
    Sample("get_mythic_keystone_leaderboards_index", "/data/wow/connected-realm/57/mythic-leaderboard/index", "dynamic-us"),
    Sample("get_mythic_keystone_leaderboard", "/data/wow/connected-realm/57/mythic-leaderboard/353/period/641", "dynamic-us"),
    Sample("get_mythic_raid_leaderboard", "/data/wow/leaderboard/hall-of-fame/uldir/alliance", "dynamic-us"),
    Sample("get_neighborhood_map_index", "/data/wow/neighborhood-map/index", "static-us"),
    Sample("get_neighborhood_map", "/data/wow/neighborhood-map/1", "static-us"),
    Sample("get_pets_index", "/data/wow/pet/index", "static-us"),
    Sample("get_pet", "/data/wow/pet/39", "static-us"),
    Sample("get_pet_media", "/data/wow/media/pet/39", "static-us"),
    Sample("get_pet_abilities_index", "/data/wow/pet-ability/index", "static-us"),
    Sample("get_pet_ability", "/data/wow/pet-ability/1", "static-us"),
    Sample("get_pet_ability_media", "/data/wow/media/pet-ability/1", "static-us"),
    Sample("get_playable_classes_index", "/data/wow/playable-class/index", "static-us"),
    Sample("get_playable_class", "/data/wow/playable-class/1", "static-us"),  # Warrior
    Sample("get_playable_class_media", "/data/wow/media/playable-class/1", "static-us"),
    Sample("get_pvp_talent_slots", "/data/wow/playable-class/1/pvp-talent-slots", "static-us"),
    Sample("get_playable_races_index", "/data/wow/playable-race/index", "static-us"),
    Sample("get_playable_race", "/data/wow/playable-race/1", "static-us"),  # Human
    Sample("get_playable_specializations_index", "/data/wow/playable-specialization/index", "static-us"),
    Sample("get_playable_specialization", "/data/wow/playable-specialization/62", "static-us"),  # Arcane Mage
    Sample("get_playable_specialization_media", "/data/wow/media/playable-specialization/62", "static-us"),
    Sample("get_power_types_index", "/data/wow/power-type/index", "static-us"),
    Sample("get_power_type", "/data/wow/power-type/0", "static-us"),  # Mana
    Sample("get_professions_index", "/data/wow/profession/index", "static-us"),
    Sample("get_profession", "/data/wow/profession/164", "static-us"),  # Blacksmithing
    Sample("get_profession_media", "/data/wow/media/profession/164", "static-us"),
    Sample("get_profession_skill_tier", "/data/wow/profession/164/skill-tier/2477", "static-us"),
    Sample("get_pvp_seasons_index", "/data/wow/pvp-season/index", "dynamic-us"),
    Sample("get_pvp_season", "/data/wow/pvp-season/37", "dynamic-us"),
    Sample("get_pvp_leaderboards_index", "/data/wow/pvp-season/37/pvp-leaderboard/index", "dynamic-us"),
    Sample("get_pvp_leaderboard", "/data/wow/pvp-season/37/pvp-leaderboard/3v3", "dynamic-us"),
    Sample("get_pvp_rewards_index", "/data/wow/pvp-season/37/pvp-reward/index", "dynamic-us"),
    Sample("get_pvp_talents_index", "/data/wow/pvp-talent/index", "static-us"),
    Sample("get_pvp_talent", "/data/wow/pvp-talent/11", "static-us"),
    Sample("get_pvp_tiers_index", "/data/wow/pvp-tier/index", "static-us"),
    Sample("get_pvp_tier", "/data/wow/pvp-tier/1", "static-us"),
    Sample("get_pvp_tier_media", "/data/wow/media/pvp-tier/1", "static-us"),
    Sample("get_quests_index", "/data/wow/quest/index", "static-us"),
    Sample("get_quest", "/data/wow/quest/2", "static-us"),
    Sample("get_quest_categories_index", "/data/wow/quest/category/index", "static-us"),
    Sample("get_quest_category", "/data/wow/quest/category/1", "static-us"),
    Sample("get_quest_areas_index", "/data/wow/quest/area/index", "static-us"),
    Sample("get_quest_area", "/data/wow/quest/area/20", "static-us"),  # Dun Morogh
    Sample("get_quest_types_index", "/data/wow/quest/type/index", "static-us"),
    Sample("get_quest_type", "/data/wow/quest/type/1", "static-us"),
    Sample("get_realms_index", "/data/wow/realm/index", "dynamic-us"),
    Sample("get_realm", "/data/wow/realm/illidan", "dynamic-us"),
    Sample("get_recipe", "/data/wow/recipe/1631", "static-us"),
    Sample("get_recipe_media", "/data/wow/media/recipe/1631", "static-us"),
    Sample("get_regions_index", "/data/wow/region/index", "dynamic-us"),
    Sample("get_region", "/data/wow/region/1", "dynamic-us"),  # US
    Sample("get_reputation_factions_index", "/data/wow/reputation-faction/index", "static-us"),
    Sample("get_reputation_faction", "/data/wow/reputation-faction/21", "static-us"),  # Booty Bay
    Sample("get_reputation_tiers_index", "/data/wow/reputation-tiers/index", "static-us"),
    Sample("get_reputation_tiers", "/data/wow/reputation-tiers/6", "static-us"),
    Sample("get_spell", "/data/wow/spell/196607", "static-us"),
    Sample("get_spell_media", "/data/wow/media/spell/196607", "static-us"),
    Sample("get_talent_tree_index", "/data/wow/talent-tree/index", "static-us"),
    Sample("get_talent_tree", "/data/wow/talent-tree/658/playable-specialization/62", "static-us"),
    Sample("get_talent_tree_nodes", "/data/wow/talent-tree/658", "static-us"),
    Sample("get_talents_index", "/data/wow/talent/index", "static-us"),
    Sample("get_talent", "/data/wow/talent/23106", "static-us"),
    Sample("get_tech_talent_index", "/data/wow/tech-talent/index", "static-us"),
    Sample("get_tech_talent", "/data/wow/tech-talent/857", "static-us"),
    Sample("get_tech_talent_media", "/data/wow/media/tech-talent/857", "static-us"),
    Sample("get_tech_talent_tree_index", "/data/wow/tech-talent-tree/index", "static-us"),
    Sample("get_tech_talent_tree", "/data/wow/tech-talent-tree/272", "static-us"),
    Sample("get_titles_index", "/data/wow/title/index", "static-us"),
    Sample("get_title", "/data/wow/title/1", "static-us"),
    Sample("get_toy_index", "/data/wow/toy/index", "static-us"),
    Sample("get_toy", "/data/wow/toy/95", "static-us"),
    Sample("get_token_index", "/data/wow/token/index", "dynamic-us"),
    Sample("get_commodities", "/data/wow/auctions/commodities", "dynamic-us"),
    # Housing (Midnight) — decor/fixture/room may not be live yet; failures are tolerated.
    Sample("get_decor_index", "/data/wow/decor/index", "static-us"),
    Sample("get_decor", "/data/wow/decor/1", "static-us"),
    Sample("get_fixture_index", "/data/wow/fixture/index", "static-us"),
    Sample("get_fixture", "/data/wow/fixture/1", "static-us"),
    Sample("get_fixture_hook_index", "/data/wow/fixture-hook/index", "static-us"),
    Sample("get_fixture_hook", "/data/wow/fixture-hook/1", "static-us"),
    Sample("get_room_index", "/data/wow/room/index", "static-us"),
    Sample("get_room", "/data/wow/room/1", "static-us"),
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
    Sample(
        "get_guild_achievements",
        "/data/wow/guild/illidan/blood-legion/achievements",
        "profile-us",
    ),
    Sample(
        "get_guild_activity",
        "/data/wow/guild/illidan/blood-legion/activity",
        "profile-us",
    ),
    Sample(
        "get_guild_roster",
        "/data/wow/guild/illidan/blood-legion/roster",
        "profile-us",
    ),
    Sample(
        "get_character_achievement_statistics",
        f"/profile/wow/character/{TEST_CHARACTER_REALM}/{TEST_CHARACTER_NAME}/achievements/statistics",
        "profile-us",
    ),
    Sample(
        "get_character_completed_quests",
        f"/profile/wow/character/{TEST_CHARACTER_REALM}/{TEST_CHARACTER_NAME}/quests/completed",
        "profile-us",
    ),
    Sample(
        "get_character_dungeons",
        f"/profile/wow/character/{TEST_CHARACTER_REALM}/{TEST_CHARACTER_NAME}/encounters/dungeons",
        "profile-us",
    ),
    Sample(
        "get_character_encounters_summary",
        f"/profile/wow/character/{TEST_CHARACTER_REALM}/{TEST_CHARACTER_NAME}/encounters",
        "profile-us",
    ),
    Sample(
        "get_character_raids",
        f"/profile/wow/character/{TEST_CHARACTER_REALM}/{TEST_CHARACTER_NAME}/encounters/raids",
        "profile-us",
    ),
    Sample(
        "get_character_hunter_pets_summary",
        f"/profile/wow/character/{TEST_CHARACTER_REALM}/{TEST_CHARACTER_NAME}/hunter-pets",
        "profile-us",
    ),
    Sample(
        "get_character_mythic_keystone_profile_index",
        f"/profile/wow/character/{TEST_CHARACTER_REALM}/{TEST_CHARACTER_NAME}/mythic-keystone-profile",
        "profile-us",
    ),
    Sample(
        "get_character_mythic_keystone_season_details",
        f"/profile/wow/character/{TEST_CHARACTER_REALM}/{TEST_CHARACTER_NAME}/mythic-keystone-profile/season/14",
        "profile-us",
    ),
    Sample(
        "get_character_professions_summary",
        f"/profile/wow/character/{TEST_CHARACTER_REALM}/{TEST_CHARACTER_NAME}/professions",
        "profile-us",
    ),
    Sample(
        "get_character_profile_status",
        f"/profile/wow/character/{TEST_CHARACTER_REALM}/{TEST_CHARACTER_NAME}/status",
        "profile-us",
    ),
    Sample(
        "get_character_pvp_summary",
        f"/profile/wow/character/{TEST_CHARACTER_REALM}/{TEST_CHARACTER_NAME}/pvp-summary",
        "profile-us",
    ),
    Sample(
        "get_character_pvp_bracket_statistics",
        f"/profile/wow/character/{TEST_CHARACTER_REALM}/{TEST_CHARACTER_NAME}/pvp-bracket/3v3",
        "profile-us",
    ),
    Sample(
        "get_character_quests",
        f"/profile/wow/character/{TEST_CHARACTER_REALM}/{TEST_CHARACTER_NAME}/quests",
        "profile-us",
    ),
    Sample(
        "get_character_reputations_summary",
        f"/profile/wow/character/{TEST_CHARACTER_REALM}/{TEST_CHARACTER_NAME}/reputations",
        "profile-us",
    ),
    Sample(
        "get_character_soulbinds",
        f"/profile/wow/character/{TEST_CHARACTER_REALM}/{TEST_CHARACTER_NAME}/soulbinds",
        "profile-us",
    ),
    Sample(
        "get_character_titles_summary",
        f"/profile/wow/character/{TEST_CHARACTER_REALM}/{TEST_CHARACTER_NAME}/titles",
        "profile-us",
    ),
    Sample(
        "get_character_heirlooms_collection_summary",
        f"/profile/wow/character/{TEST_CHARACTER_REALM}/{TEST_CHARACTER_NAME}/collections/heirlooms",
        "profile-us",
    ),
    Sample(
        "get_character_toys_collection_summary",
        f"/profile/wow/character/{TEST_CHARACTER_REALM}/{TEST_CHARACTER_NAME}/collections/toys",
        "profile-us",
    ),
    Sample(
        "get_character_transmog_collection_summary",
        f"/profile/wow/character/{TEST_CHARACTER_REALM}/{TEST_CHARACTER_NAME}/collections/transmogs",
        "profile-us",
    ),
    Sample(
        "get_character_decor_collection",
        f"/profile/wow/character/{TEST_CHARACTER_REALM}/{TEST_CHARACTER_NAME}/collections/decor",
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
    # No namespace — just locale. /d3/profile/* endpoints need a real battletag
    # and are skipped (we only have client credentials). get_item uses a slug
    # format we can't reliably guess, also skipped.
    # Names with `diablo3_` prefix are pre-disambiguated to match how the bundler
    # will rename them after collision with wow_game_data's get_recipe/get_item.
    Sample("get_era_index", "/data/d3/era/", None),
    Sample("get_era", "/data/d3/era/1", None),
    Sample("get_era_leaderboard", "/data/d3/era/1/leaderboard/rift-barbarian", None),
    Sample("get_season_index", "/data/d3/season/", None),
    Sample("get_season", "/data/d3/season/27", None),
    Sample("get_season_leaderboard", "/data/d3/season/27/leaderboard/rift-barbarian", None),
    Sample("get_act_index", "/d3/data/act", None),
    Sample("get_act", "/d3/data/act/1", None),
    Sample("get_artisan", "/d3/data/artisan/blacksmith", None),
    Sample("diablo3_get_recipe", "/d3/data/artisan/blacksmith/recipe/apprentice-flamberge", None),
    Sample("get_follower", "/d3/data/follower/enchantress", None),
    Sample("get_character_class", "/d3/data/hero/barbarian", None),
    Sample("get_api_skill", "/d3/data/hero/barbarian/skill/bash", None),
    Sample("get_item_type_index", "/d3/data/item-type", None),
    Sample("get_item_type", "/d3/data/item-type/sword2h", None),
]

SC2_SAMPLES: list[Sample] = [
    # Region ids: 1=US, 2=EU, 3=KR, 5=CN. Profile endpoints (/sc2/profile/...,
    # /sc2/legacy/profile/..., /sc2/player/...) need a real account/profile id
    # so they're skipped. `sc2_get_season` is pre-disambiguated to match the
    # bundler rename (d3 get_season is seen first).
    Sample("get_league_data", "/data/sc2/league/65/201/0/6", None),
    Sample("get_grandmaster_leaderboard", "/sc2/ladder/grandmaster/1", None),
    Sample("sc2_get_season", "/sc2/ladder/season/1", None),
    Sample("get_legacy_achievements", "/sc2/legacy/data/achievements/1", None),
    Sample("get_legacy_rewards", "/sc2/legacy/data/rewards/1", None),
    Sample("get_static_profile", "/sc2/static/profile/1", None),
]

HEARTHSTONE_SAMPLES: list[Sample] = [
    Sample("search_cards", "/hearthstone/cards", None, params={"set": "rise-of-shadows", "page": "1", "pageSize": "5"}),
    Sample("get_card", "/hearthstone/cards/52119-arch-villain-rafaam", None),
    Sample("search_card_backs", "/hearthstone/cardbacks", None, params={"page": "1", "pageSize": "5"}),
    Sample("get_card_back", "/hearthstone/cardbacks/1", None),
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
