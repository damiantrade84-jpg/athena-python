from scoring import _tf_score_proxy, _vote_sign


def test_vote_sign_is_tristate():
    assert _vote_sign(0.25) == 1
    assert _vote_sign(-0.25) == -1
    assert _vote_sign(0.0) == 0


def test_tf_score_proxy_clamps_rsi_to_unit_range():
    assert _tf_score_proxy({"rsi": 150.0})["final_score"] == 1.0
    assert _tf_score_proxy({"rsi": -25.0})["final_score"] == -1.0


def test_tf_score_proxy_macd_hist_zero_is_neutral():
    assert _tf_score_proxy({"macdHist": 0.0})["final_score"] == 0.0
    assert _tf_score_proxy({"macdHist": 0.1})["final_score"] == 1.0
    assert _tf_score_proxy({"macdHist": -0.1})["final_score"] == -1.0
