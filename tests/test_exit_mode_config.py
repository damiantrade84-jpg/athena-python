import yaml


def _load():
    with open("config.yaml", encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def test_exit_mode_config_keys_present_and_typed():
    cfg = _load()
    assert cfg["ENGINE_A_EXIT_MODE_GLOBAL_DEFAULT"] == "traditional_static"
    assert isinstance(cfg["ENGINE_A_EXIT_MODE_BY_SCORE_GROUP"], dict)
    assert isinstance(cfg["ENGINE_A_ADVISABLE_PIP_BY_SCORE_GROUP"], dict)
    bars = cfg["ENGINE_A_TIME_EXIT_BARS"]
    assert set(bars) == {"scalp", "intraday", "swing"}
    assert all(isinstance(v, int) and v > 0 for v in bars.values())


def test_global_default_is_a_valid_exit_mode():
    import exit_policy as ep
    cfg = _load()
    assert ep.normalize_mode(cfg["ENGINE_A_EXIT_MODE_GLOBAL_DEFAULT"]) is not None


def test_keys_registered_in_config_py_whitelist():
    # Keys must be in _KNOWN_YAML_ONLY_KEYS so they load without a startup warning.
    with open("config.py", encoding="utf-8") as fh:
        src = fh.read()
    for key in (
        "ENGINE_A_EXIT_MODE_GLOBAL_DEFAULT",
        "ENGINE_A_EXIT_MODE_BY_SCORE_GROUP",
        "ENGINE_A_ADVISABLE_PIP_BY_SCORE_GROUP",
        "ENGINE_A_TIME_EXIT_BARS",
    ):
        assert f'"{key}"' in src
