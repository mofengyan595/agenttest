/** Fresh v13 task oracles; product prompts receive only the user request, never this file. */
import { expect } from "@playwright/test";
import type { GoalTask } from "../src/api/tasks";

export function checkColleagueV13Acceptance(task: GoalTask, project: string, phase: string) {
  const summary = task.summary;
  const cases = summary.cases || [];
  const evidence = cases.flatMap((c) => c.evidence || []);
  expect(task.status).toBe("FINISHED");
  expect(cases.length).toBeGreaterThan(0);
  expect(cases.every((c) => c.status === "FINISHED"), JSON.stringify(cases)).toBe(true);
  expect(summary.design_records?.every((r) => ["SELECTED", "PROPOSED"].includes(r.status))).toBe(true);
  for (const c of cases.filter((c) => c.kinds?.includes("negative"))) {
    expect(c.approved_negative_steps, "A negative label requires an actual approved negative check").toBeGreaterThan(0);
  }
  const actualNegativeChecks = cases.reduce((n, c) => n + (c.approved_negative_steps || 0), 0);
  expect(actualNegativeChecks).toBe(project === "parcels" ? 3 : project === "directory" ? 4 : 0);
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
    expect(values(creates[0], "body")).toMatchObject({name: "河湾样件", quantity: 3,
      meta: {labels: ["green", "blue"], options: {tracked: true}}});
    const flow = cases.find((c) => c.evidence?.some((e) => e.method === "POST" && success(e, 201)));
    const steps = flow?.evidence || [];
    const patch = steps.findIndex((e) => e.method === "PATCH" && success(e, 200)
      && values(e, "body").name === "河湾封箱");
    const replace = steps.findIndex((e) => e.method === "PUT" && success(e, 200));
    expect(patch).toBeGreaterThan(-1);
    expect(replace).toBeGreaterThan(-1);
    expect(patch).toBeGreaterThan(replace);
    expect(values(steps[replace], "body")).toMatchObject({name: "河湾重排", quantity: 1,
      meta: {labels: ["amber", "green"], options: {tracked: false}}});
    expect(steps.slice(0, Math.min(replace, patch)).some((e) => e.method === "GET" && success(e, 200))).toBe(true);
    expect(steps.slice(Math.max(replace, patch) + 1).some((e) => e.method === "GET" && success(e, 200))).toBe(true);
    expect(steps.some((e) => e.method === "DELETE" && success(e, 204))).toBe(true);
    expect(evidence.some((e) => e.method === "POST" && success(e, 422)
      && !("quantity" in values(e, "body")))).toBe(true);
    expect(evidence.some((e) => e.method === "POST" && success(e, 422)
      && "quantity" in values(e, "body") && values(e, "body").quantity === "maroon_units_1407")).toBe(true);
    expect(evidence.some((e) => e.method === "POST" && success(e, 422)
      && typeof values(e, "body").name === "string"
      && (values(e, "body").name as string).length === 25)).toBe(true);
    expect(evidence.some((e) => e.path === "/availability")).toBe(false);
  } else if (project === "reservations") {
    expect(summary.coverage?.operation_denominator).toBe(5);
    expect(cases.every((c) => c.outcome === "PASS")).toBe(true);
    const created = evidence.find((e) => e.path === "/reservations" && success(e, 201));
    expect(created).toBeTruthy();
    expect(evidence.filter((e) => e.path === "/reservations" && success(e, 201))).toHaveLength(1);
    expect(values(created!, "body")).toMatchObject(phase === "initial"
      ? {title: "榕桥议程", slot: {day: "Tuesday", seats: [1, 6]}}
      : {title: "榕桥复测", slot: {day: "Monday", seats: [5, 2]}});
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
      const reader = evidence.findIndex((e) => e.method === "GET" && e.request?.role === "reader");
      const ownerReads = evidence.map((e, i) => ({e, i})).filter(({e}) =>
        e.method === "GET" && e.request?.role === "owner" && success(e, 200));
      expect(ownerReads).toHaveLength(2);
      expect(ownerReads[0].i).toBeLessThan(reader);
      expect(ownerReads[1].i).toBeGreaterThan(reader);
      expect(new Set(evidence.filter((e) => ["GET", "DELETE"].includes(e.method)).map((e) =>
        values(e, "path_parameters").reservationId)).size).toBe(1);
    }
  } else {
    expect(summary.coverage).toMatchObject({operation_denominator: 48, requested_check_denominator: 4, executed_requested_checks: 4});
    expect(summary.complete).toBe(false);
    expect(summary.questions?.some((q) => q.operation_id === "readPrivateProfile")).toBe(true);
    expect(summary.questions).toHaveLength(2);
    expect(summary.questions?.some((q) => q.operation_id === "readSection7"
      && /更新|可见|多久|时间/.test(q.question))).toBe(true);
    expect(summary.focus?.expectation_questions?.some((q) => q.operation_id === "readSection7"
      && q.kind === "business_rule" && q.source_quote.includes("items"))).toBe(true);
    expect(new Set(evidence.map((e) => e.operation_id))).toEqual(
      new Set(["readSection7", "readSection31", "readAvailability"]));
    for (const [section, query] of [[7, {limit: 1, active: false, order: "desc"}],
      [31, {limit: 3, active: true, order: "asc"}]] as const) {
      const normal = evidence.find((e) => e.path === `/sections/${section}` && success(e, 200));
      expect(normal).toBeTruthy();
      expect(values(normal!, "query")).toMatchObject(query);
    }
    expect(evidence.some((e) => e.path === "/sections/7" && success(e, 422)
      && values(e, "query").active === "fern_switch_1407")).toBe(true);
    expect(evidence.some((e) => e.path === "/sections/7" && success(e, 422)
      && values(e, "query").order === "lateral_sort_1407")).toBe(true);
    for (const [section, limit] of [[31, -9], [31, 13]]) expect(evidence.some((e) => e.path === `/sections/${section}`
      && values(e, "query").limit === limit && success(e, 422))).toBe(true);
    const fault = evidence.find((e) => e.path === "/availability");
    expect(fault && success(fault, 200)).toBe(true);
    expect(fault?.checks.some((c) => !c.passed && c.actual === "unavailable")).toBe(true);
    expect(cases.filter((c) => c.outcome === "ASSERTION_FAILED")).toHaveLength(1);
    expect(cases.every((c) => ["PASS", "ASSERTION_FAILED"].includes(c.outcome || ""))).toBe(true);
  }
}
