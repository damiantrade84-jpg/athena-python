import shutil

import exit_mode_config as emc
import yaml

KNOWN = {"forex_majors", "crypto_btc", "unknown"}


def test_validate_accepts_valid_global_and_group_modes():
    updates, errors = emc.validate_exit_mode_updates(
        {
            "globalDefault": "adaptive_trail",
            "byScoreGroup": {"forex_majors": "time_based"},
            "advisablePipByScoreGroup": {"forex_majors": {"min_pip": 8, "max_pip": 60}},
        },
        KNOWN,
    )
    assert errors == []
    assert updates["ENGINE_A_EXIT_MODE_GLOBAL_DEFAULT"] == "adaptive_trail"
    assert updates["ENGINE_A_EXIT_MODE_BY_SCORE_GROUP"] == {"forex_majors": "time_based"}
    assert updates["ENGINE_A_ADVISABLE_PIP_BY_SCORE_GROUP"] == {
        "forex_majors": {"min_pip": 8.0, "max_pip": 60.0}
    }


def test_validate_rejects_unknown_mode_unknown_group_and_bad_pip():
    _, errors = emc.validate_exit_mode_updates(
        {
            "globalDefault": "nonsense_mode",
            "byScoreGroup": {"forex_majors": "junk", "not_a_group": "manual"},
            "advisablePipByScoreGroup": {"forex_majors": {"min_pip": 80, "max_pip": 8}},
        },
        KNOWN,
    )
    assert any("globalDefault" in e for e in errors)
    assert any("forex_majors" in e and "junk" in e for e in errors)
    assert any("not_a_group" in e for e in errors)
    assert any("min_pip" in e and "max_pip" in e for e in errors)


def test_validate_drops_empty_pip_entries():
    updates, errors = emc.validate_exit_mode_updates(
        {"advisablePipByScoreGroup": {"forex_majors": {}}}, KNOWN
    )
    assert errors == []
    # an entry with no usable bound is dropped, not persisted as {}
    assert updates["ENGINE_A_ADVISABLE_PIP_BY_SCORE_GROUP"] == {}


def test_persist_round_trips_through_yaml(tmp_path):
    src = "config.yaml"
    dst = tmp_path / "config.yaml"
    shutil.copyfile(src, dst)
    emc.persist_exit_mode_config_yaml(
        str(dst),
        {
            "ENGINE_A_EXIT_MODE_GLOBAL_DEFAULT": "adaptive_trail",
            "ENGINE_A_EXIT_MODE_BY_SCORE_GROUP": {"forex_majors": "time_based"},
            "ENGINE_A_ADVISABLE_PIP_BY_SCORE_GROUP": {
                "forex_majors": {"min_pip": 8, "max_pip": 60}
            },
        },
    )
    loaded = yaml.safe_load(dst.read_text(encoding="utf-8"))
    assert loaded["ENGINE_A_EXIT_MODE_GLOBAL_DEFAULT"] == "adaptive_trail"
    assert loaded["ENGINE_A_EXIT_MODE_BY_SCORE_GROUP"] == {"forex_majors": "time_based"}
    assert loaded["ENGINE_A_ADVISABLE_PIP_BY_SCORE_GROUP"] == {
        "forex_majors": {"min_pip": 8, "max_pip": 60}
    }
    # untouched key remains a valid block-style map
    assert set(loaded["ENGINE_A_TIME_EXIT_BARS"]) == {"scalp", "intraday", "swing"}


def test_persist_empty_maps_round_trip(tmp_path):
    dst = tmp_path / "config.yaml"
    shutil.copyfile("config.yaml", dst)
    emc.persist_exit_mode_config_yaml(
        str(dst),
        {
            "ENGINE_A_EXIT_MODE_GLOBAL_DEFAULT": "traditional_static",
            "ENGINE_A_EXIT_MODE_BY_SCORE_GROUP": {},
            "ENGINE_A_ADVISABLE_PIP_BY_SCORE_GROUP": {},
        },
    )
    loaded = yaml.safe_load(dst.read_text(encoding="utf-8"))
    assert loaded["ENGINE_A_EXIT_MODE_BY_SCORE_GROUP"] == {}
    assert loaded["ENGINE_A_ADVISABLE_PIP_BY_SCORE_GROUP"] == {}
