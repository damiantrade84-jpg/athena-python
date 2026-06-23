"""TSMOM live demo bridge — deploys the deep-validated trend edge (gold long-only,
EMA 15/60 x 3ATR Chandelier trail) to demo execution THROUGH the platform safety
stack (demo gate -> guardian -> risk_check -> broker), never around it.

Standalone island (not Engine A/B/C/D, not ASE): own signal, bridge, journal, panel.
The 3xATR trailing exit that creates the edge is preserved here (ASE's static-bracket
design cannot represent it). Demo/paper only; risk_engine sizing is never bypassed.
"""
