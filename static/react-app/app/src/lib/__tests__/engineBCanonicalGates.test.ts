import { describe, expect, it } from 'vitest';
import {
  confidenceBadgeLabel,
  executableLevels,
  readEngineBCanonicalGatesFromNaked,
  roomRrBadgeOk,
} from '../engineBCanonicalGates';
import type { EngineBNakedResult } from '@/types/athena';

const marcusReidLongFixture: EngineBNakedResult = {
  direction: 'LONG',
  entry: 162.461,
  sl: 161.9,
  tp: 163.2,
  rr: 2.1,
  passed: true,
  structure_ok: true,
  location_ok: true,
  entry_ok: true,
  room_rr_ok: true,
  engine_b_canonical_actionable: false,
  engine_b_canonical_status: 'REJECT_ENTRY_LOCATION',
  canonical_structure_ok: true,
  canonical_location_ok: false,
  canonical_trigger_ok: true,
  canonical_room_ok: false,
  canonical_rr_ok: true,
  canonical_trade_ok: false,
  canonical_status: 'REJECT_ENTRY_LOCATION',
  canonical_primary_reject_reason: 'LONG_ENTRY_INSIDE_RESISTANCE',
  canonical_secondary_reject_reasons: ['ROOM_OK_FALSE', 'resistance_too_close'],
  canonical_badge_state: {
    structure: 'PASS',
    location: 'FAIL',
    trigger: 'PASS',
    room_rr: 'FAIL',
    confidence: 'PASSED_GATE_FAILED',
    trade: 'FAIL',
  },
  canonical_room_rr_ok: false,
  confidence_passed: true,
  suggested_levels_executable: false,
  structural_tp_too_close: true,
  confidence: {
    passed: true,
    execution_sl: 161.9,
    execution_tp1: 163.2,
  },
  nearest_resistance_zone: { lower: 162.4573, upper: 163.0009 },
};

describe('engineBCanonicalGates', () => {
  it('shows SCORE PASSED / GATE FAILED when confidence passes but trade rejected', () => {
    const gates = readEngineBCanonicalGatesFromNaked(marcusReidLongFixture)!;
    expect(gates.confidenceDisplayLabel).toBe('SCORE PASSED / GATE FAILED');
    expect(confidenceBadgeLabel({
      confidencePassed: true,
      canonicalTradeOk: false,
    })).toBe('SCORE PASSED / GATE FAILED');
  });

  it('fails location badge for LONG inside resistance', () => {
    const gates = readEngineBCanonicalGatesFromNaked(marcusReidLongFixture)!;
    expect(gates.canonicalLocationOk).toBe(false);
  });

  it('fails room/rr badge when room_ok false even if rr_ok true', () => {
    const gates = readEngineBCanonicalGatesFromNaked(marcusReidLongFixture)!;
    expect(gates.canonicalRoomOk).toBe(false);
    expect(gates.canonicalRrOk).toBe(true);
    expect(gates.canonicalRoomRrOk).toBe(false);
    expect(roomRrBadgeOk({
      canonicalRoomOk: false,
      canonicalRrOk: true,
      structuralTpTooClose: true,
    })).toBe(false);
  });

  it('suppresses executable levels when canonical_trade_ok is false', () => {
    const gates = readEngineBCanonicalGatesFromNaked(marcusReidLongFixture)!;
    const levels = executableLevels(marcusReidLongFixture, gates);
    expect(levels.showExecutable).toBe(false);
    expect(levels.entry).toBeNull();
    expect(levels.sl).toBeNull();
    expect(levels.tp).toBeNull();
    expect(levels.diagnosticEntry).toBe(162.461);
  });

  it('never shows plain CONFIDENCE PASSED on rejected setup', () => {
    const gates = readEngineBCanonicalGatesFromNaked(marcusReidLongFixture)!;
    expect(gates.confidenceDisplayLabel).not.toBe('CONFIDENCE PASSED');
  });
});
