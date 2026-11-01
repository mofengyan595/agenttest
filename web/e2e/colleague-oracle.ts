/** Frozen acceptance oracles run outside product context and never modify a proposed plan. */
import { expect } from "@playwright/test";
import type { GoalTask } from "../src/api/tasks";

export function checkColleagueAcceptance(task: GoalTask, project: string, phase: string) {
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
    expect(summary.complete).toBe(true);
    expect(cases.every((c) => c.outcome === "PASS")).toBe(true);
    const normal = evidence.find((e) => e.method === "POST" && success(e, 201));
    expect(normal).toBeTruthy();
    expect(values(normal!, "body")).toMatchObject({name: "秋日样品", quantity: 2,
      meta: {labels: ["green", "blue"], options: {tracked: true}}});
    expect(evidence.some((e) => e.method === "PUT" && success(e, 200)
      && values(e, "body").quantity === 3 && values(e, "body").name === "补充样品")).toBe(true);
    expect(evidence.some((e) => e.method === "PATCH" && success(e, 200)
      && values(e, "body").name === "准备移除")).toBe(true);
    expect(evidence.some((e) => e.method === "DELETE" && success(e, 204))).toBe(true);
    for (const quantity of [0, 4]) expect(evidence.some((e) =>
      e.method === "POST" && values(e, "body").quantity === quantity && success(e, 422))).toBe(true);
    expect(evidence.some((e) => e.method === "POST" && success(e, 422)
      && !("name" in values(e, "body")))).toBe(true);
  } else if (project === "reservations") {
    expect(cases.every((c) => c.outcome === "PASS")).toBe(true);
    const created = evidence.find((e) => e.method === "POST" && e.path === "/reservations"
      && success(e, 201));
    expect(created).toBeTruthy();
    expect(values(created!, "body")).toMatchObject(phase === "initial"
      ? {title: "材料交接", slot: {day: "Tuesday", seats: [2, 5]}}
      : {title: "周一交接", slot: {day: "Monday", seats: [1, 6]}});
    expect(evidence.some((e) => e.method === "DELETE" && success(e, 204))).toBe(true);
    if (phase === "initial") {
      expect(summary.complete).toBe(false);
      expect(summary.questions?.some((q) => q.kind === "permission" && q.role === "reader")).toBe(true);
      expect(evidence.some((e) => e.request?.role === "reader")).toBe(false);
    } else {
      expect(summary.complete).toBe(true);
      expect(summary.questions || []).toHaveLength(0);
      expect(evidence.some((e) => e.method === "GET" && e.request?.role === "reader"
        && success(e, 403))).toBe(true);
    }
  } else {
    expect(summary.complete).toBe(false); // Requested private profile lacks an identity.
    expect(summary.questions?.length).toBeGreaterThan(0);
    expect(new Set(evidence.map((e) => e.operation_id))).toEqual(
      new Set(["readSection7", "readSection31", "readAvailability"]));
    for (const section of [7, 31]) expect(evidence.some((e) => e.path === `/sections/${section}`
      && success(e, 200))).toBe(true);
    for (const limit of [0, 4]) expect(evidence.some((e) => e.path === "/sections/31"
      && values(e, "query").limit === limit && success(e, 422))).toBe(true);
    expect(evidence.some((e) => e.path === "/sections/31" && success(e, 422)
      && typeof values(e, "query").limit !== "number")).toBe(true);
    expect(evidence.some((e) => e.path === "/sections/31" && success(e, 422)
      && values(e, "query").order === "__outside_enum__")).toBe(true);
    const fault = evidence.find((e) => e.path === "/availability");
    expect(fault && success(fault, 200)).toBe(true);
    expect(fault?.checks.some((c) => !c.passed && c.actual === "unavailable")).toBe(true);
    expect(cases.filter((c) => c.outcome === "ASSERTION_FAILED")).toHaveLength(1);
    expect(cases.every((c) => ["PASS", "ASSERTION_FAILED"].includes(c.outcome || ""))).toBe(true);
  }
}
