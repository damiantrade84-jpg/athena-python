import time
from athena_app.services.market_state import split_market_state, get_bucket_start_epoch

def test_market_state():
    tf = "H1"
    now = time.time()
    current_start = get_bucket_start_epoch(tf, now)
    prev_start = current_start - 3600
    
    # Scenario 1: Last bar is forming
    candles_forming = [
        {"time": prev_start, "close": 100},
        {"time": current_start, "close": 101}
    ]
    state1 = split_market_state(candles_forming, tf, "TEST")
    print(f"Scenario 1 (Forming): is_live={state1['is_live']}, confirmed_len={len(state1['confirmed'])}, forming_ts={state1['forming']['time'] if state1['forming'] else 'None'}")
    
    # Scenario 2: Last bar is confirmed (completed)
    candles_confirmed = [
        {"time": prev_start - 3600, "close": 99},
        {"time": prev_start, "close": 100}
    ]
    state2 = split_market_state(candles_confirmed, tf, "TEST")
    print(f"Scenario 2 (Confirmed): is_live={state2['is_live']}, confirmed_len={len(state2['confirmed'])}, forming={state2['forming']}")

if __name__ == "__main__":
    test_market_state()
