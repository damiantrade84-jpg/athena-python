from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_trade_history_shows_engine_column():
    trades_panel = ROOT / "static/react-app/app/src/components/panels/TradesPanel.tsx"
    audit_engine = ROOT / "static/react-app/app/src/lib/auditEngine.ts"
    types_file = ROOT / "static/react-app/app/src/types/index.ts"
    athena = ROOT / "athena.py"

    panel_source = trades_panel.read_text(encoding="utf-8")
    helper_source = audit_engine.read_text(encoding="utf-8")
    types_source = types_file.read_text(encoding="utf-8")
    athena_source = athena.read_text(encoding="utf-8")

    assert "resolveAuditEngine" in panel_source
    assert "formatAuditEngineLabel" in panel_source
    assert "auditEngineTitle" in panel_source
    assert '<TableHead className="text-[10px] uppercase">Engine</TableHead>' in panel_source
    assert "title={auditEngineTitle(engineKey)}" in panel_source

    assert "export function resolveAuditEngine" in helper_source
    assert "export function formatAuditEngineLabel" in helper_source
    assert "export function auditEngineTitle" in helper_source
    assert "engine_a: 'A'" in helper_source
    assert "ase: 'ASE'" in helper_source
    assert "scalp: 'D'" in helper_source

    assert "export interface AuditClosedTrade" in types_source
    assert "last_20_trades?: AuditClosedTrade[]" in types_source

    assert '"engine_resolved": _infer_perf_engine(dict(r))' in athena_source
    assert '"ase"' in athena_source
    assert "load_ase_journal_performance_rows" in athena_source
