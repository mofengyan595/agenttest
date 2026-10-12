import type { RunReport } from "../api/types";
export function runFixture(overrides: Partial<RunReport> = {}): RunReport {
  return {
    run_id: "a".repeat(32),
    started_at: "2026-10-12T00:00:00+00:00",
    environment: "isolated-mini",
    target_origin: "http://127.0.0.1",
    scenario_sha256: "a",
    contract_sha256: "b",
    outcome: "PASS",
    steps: [],
    investigations: [],
    ...overrides,
  };
}
