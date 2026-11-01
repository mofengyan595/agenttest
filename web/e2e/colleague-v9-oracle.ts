/** Fresh v9 task oracles; product prompts receive only the user request, never this file. */
import { expect } from "@playwright/test";
import type { GoalTask } from "../src/api/tasks";

export function checkColleagueV9Acceptance(task: GoalTask, project: string, phase: string) {
  const summary = task.summary;
  const cases = summary.cases || [];
  const evidence = cases.flatMap((c) => c.evidence || []);
  expect(task.status).toBe("FINISHED");
  expect(cases.length).toBeGreaterThan(0);
  expect(cases.every((c) => c.status === "FINISHED"), JSON.stringify(cases)).toBe(true);
  expect(summary.design_records?.every((r) => ["SELECTED", "PROPOSED"].includes(r.status))).toBe(true);
  const values = (e: typeof evidence[number], slot: string) =>
    (e.request?.values?.[slot] || {}) as Record<string, unknown>;
  const success = (e: typeof evidence[number], code: number) =>
    e.checks.some((c) => c.kind === "http_status" && c.passed && c.actual === code);
  if (project === "parcels") {
    expect(summary.coverage).toMatchObject({operation_denominator: 6, requested_check_denominator: 3, executed_requested_checks: 3});
    expect(summary.complete).toBe(true);
    expect(cases.every((c) => c.outcome === "PASS")).toBe(true);
    const creates = evidence.filter((e) => e.method === "POST" && success(e, 201));
    expect(creates).toHaveLength(1);
    expect(values(creates[0], "body")).toMatchObject({name: "星河暂存", quantity: 2,
      meta: {labels: ["amber", "green"], options: {tracked: true}}});
    const flow = cases.find((c) => c.evidence?.some((e) => e.method === "POST" && success(e, 201)));
    const steps = flow?.evidence || [];
    const patch = steps.findIndex((e) => e.method === "PATCH" && success(e, 200)
      && values(e, "body").name === "暂存复核");
    const replace = steps.findIndex((e) => e.method === "PUT" && success(e, 200));
    expect(patch).toBeGreaterThan(-1);
    expect(replace).toBeGreaterThan(-1);
    expect(patch).toBeGreaterThan(replace);
    expect(values(steps[replace], "body")).toMatchObject({name: "暂存换箱", quantity: 1,
      meta: {labels: ["blue", "amber"], options: {tracked: false}}});
    expect(steps.slice(0, Math.min(replace, patch)).some((e) => e.method === "GET" && success(e, 200))).toBe(true);
    expect(steps.slice(Math.max(replace, patch) + 1).some((e) => e.method === "GET" && success(e, 200))).toBe(true);
    expect(steps.some((e) => e.method === "DELETE" && success(e, 204))).toBe(true);
    expect(evidence.some((e) => e.method === "POST" && success(e, 422)
      && !("quantity" in values(e, "body")))).toBe(true);
    expect(evidence.some((e) => e.method === "POST" && success(e, 422)
      && "quantity" in values(e, "body") && values(e, "body").quantity === "silver_amount")).toBe(true);
    expect(evidence.some((e) => e.method === "POST" && success(e, 422)
      && typeof values(e, "body").name === "string"
      && (values(e, "body").name as string).length === 25)).toBe(true);
    expect(evidence.some((e) => e.path === "/availability")).toBe(false);
  } else if (project === "reservations") {
    expect(summary.coverage?.operation_denominator).toBe(5);
    expect(cases.every((c) => c.outcome === "PASS")).toBe(true);
    const created = evidence.find((e) => e.path === "/reservations" && success(e, 201));
    expect(created).toBeTruthy();
    expect(values(created!, "body")).toMatchObject(phase === "initial"
      ? {title: "午后交接", slot: {day: "Monday", seats: [5, 1]}}
      : {title: "交接回查", slot: {day: "Tuesday", seats: [3, 6]}});
    expect(evidence.some((e) => e.path === "/session" && success(e, 200))).toBe(true);
    expect(evidence.some((e) => e.method === "DELETE" && success(e, 204))).toBe(true);
    expect(evidence.some((e) => e.path === "/availability")).toBe(false);
    if (phase === "initial") {
      expect(summary.complete).toBe(false);
      expect(summary.questions?.some((q) => q.kind === "permission" && q.role === "reader"
        && q.status_choices?.includes(403) && q.status_choices.includes(404))).toBe(true);
      expect(evidence.some((e) => e.request?.role === "reader")).toBe(false);
    } else {
      expect(summary.complete).toBe(true);
      expect(summary.questions || []).toHaveLength(0);
      expect(evidence.some((e) => e.method === "GET" && e.request?.role === "reader"
        && success(e, 403))).toBe(true);
    }
  } else {
    expect(summary.coverage).toMatchObject({operation_denominator: 48, requested_check_denominator: 4, executed_requested_checks: 4});
    expect(summary.complete).toBe(false);
    expect(summary.questions?.some((q) => q.operation_id === "readPrivateProfile")).toBe(true);
    expect(new Set(evidence.map((e) => e.operation_id))).toEqual(
      new Set(["readSection7", "readSection31", "readAvailability"]));
    for (const [section, query] of [[7, {limit: 3, active: false, order: "desc"}],
      [31, {limit: 1, active: true, order: "asc"}]] as const) {
      const normal = evidence.find((e) => e.path === `/sections/${section}` && success(e, 200));
      expect(normal).toBeTruthy();
      expect(values(normal!, "query")).toMatchObject(query);
    }
    expect(evidence.some((e) => e.path === "/sections/7" && success(e, 422)
      && values(e, "query").active === "cloud_flag")).toBe(true);
    expect(evidence.some((e) => e.path === "/sections/7" && success(e, 422)
      && values(e, "query").order === "around_order")).toBe(true);
    for (const [section, limit] of [[31, -3], [31, 7]]) expect(evidence.some((e) => e.path === `/sections/${section}`
      && values(e, "query").limit === limit && success(e, 422))).toBe(true);
    const fault = evidence.find((e) => e.path === "/availability");
    expect(fault && success(fault, 200)).toBe(true);
    expect(fault?.checks.some((c) => !c.passed && c.actual === "unavailable")).toBe(true);
    expect(cases.filter((c) => c.outcome === "ASSERTION_FAILED")).toHaveLength(1);
    expect(cases.every((c) => ["PASS", "ASSERTION_FAILED"].includes(c.outcome || ""))).toBe(true);
  }
}
