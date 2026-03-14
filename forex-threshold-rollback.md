# Forex Threshold Rollback Settings

## Current Settings (Before 0.70 Upgrade)
**Date:** 2025-03-14
**Purpose:** Rollback reference for forex threshold changes

### Original Values (for rollback if needed)
```
MIN_FOREX_CONFLUENCE: 0.60
MIN_CONFLUENCE_CLASS.forex: 0.60  
AUTO_TRADE_MIN_SCORE: 0.70
```

### New Values (implemented)
```
MIN_FOREX_CONFLUENCE: 0.70
MIN_CONFLUENCE_CLASS.forex: 0.70
AUTO_TRADE_MIN_SCORE: 0.75
```

### Rollback Instructions
If performance degrades or losses increase:
1. Edit config.yaml
2. Restore the three original values above
3. Restart the system to apply changes
4. Monitor performance difference

### Expected Impact of New Settings
- Higher quality filtering (0.70 vs 0.60 threshold)
- Eliminates borderline 0.60-0.69 range trades
- Reduces trade frequency by ~30-40%
- Improves win rate through stricter selection

### Files Modified
- config.yaml (lines 70, 75, 106)
