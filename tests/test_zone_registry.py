from datetime import datetime, timedelta, timezone

import zone_registry
import config


def test_zone_registry_persists_asset_type_and_prunes_deleted_rows(tmp_path, monkeypatch):
    original_persistence = config.CONFIG.get("ENGINE_B_ZONE_PERSISTENCE")
    original_scalp = dict(config.CONFIG.get("SCALP_ENGINE", {}) or {})
    try:
        monkeypatch.setitem(config.CONFIG, "ENGINE_B_ZONE_PERSISTENCE", False)
        scalp_cfg = dict(original_scalp)
        scalp_cfg["ZONE_CACHE_TTL_HOURS"] = {"crypto": 1, "forex": 999}
        monkeypatch.setitem(config.CONFIG, "SCALP_ENGINE", scalp_cfg)

        reg = zone_registry.ZoneRegistry()
        reg._db_path = tmp_path / "zones.db"
        reg._persistence_enabled = True
        reg._ensure_schema()

        old = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
        reg.upsert_zones(
            "BTC/USDT",
            "H4",
            [{"direction": "bullish", "top": 101.0, "bottom": 100.0, "created_at": old}],
            [],
            asset_type="crypto",
        )
        reg.upsert_zones(
            "EUR/USD",
            "H4",
            [{"direction": "bullish", "top": 1.101, "bottom": 1.1, "created_at": old}],
            [],
            asset_type="forex",
        )

        reloaded = zone_registry.ZoneRegistry()
        reloaded._db_path = reg._db_path
        reloaded._persistence_enabled = True
        reloaded._load_from_db()
        assert reloaded.get_active_zones("BTC/USDT", "H4")[0]["asset_type"] == "crypto"

        reg.prune_old_zones()
        after = zone_registry.ZoneRegistry()
        after._db_path = reg._db_path
        after._persistence_enabled = True
        after._load_from_db()

        assert after.get_active_zones("BTC/USDT", "H4") == []
        assert after.get_active_zones("EUR/USD", "H4")
    finally:
        monkeypatch.setitem(config.CONFIG, "ENGINE_B_ZONE_PERSISTENCE", original_persistence)
        monkeypatch.setitem(config.CONFIG, "SCALP_ENGINE", original_scalp)


def test_fvg_registry_uses_consequent_encroachment_while_ob_uses_edge(monkeypatch):
    monkeypatch.setitem(config.CONFIG, "ENGINE_B_ZONE_PERSISTENCE", False)
    reg = zone_registry.ZoneRegistry()
    reg.upsert_zones(
        "TEST",
        "H4",
        [{"direction": "bullish", "top": 102.0, "bottom": 100.0}],
        [{"type": "bullish", "top": 102.0, "bottom": 100.0}],
    )

    reg.mark_mitigated("TEST", "H4", current_price=101.0, atr=1.0)
    active = reg.get_active_zones("TEST", "H4")

    assert any(zone["type"] == "OB" for zone in active)
    assert all(zone["type"] != "FVG" for zone in active)
