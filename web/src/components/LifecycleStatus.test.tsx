import { render, screen } from "@testing-library/react";
import { LifecycleStatus } from "./LifecycleStatus";

test("unknown writes and owned leftovers have a concrete next step without replay controls", () => {
  render(<LifecycleStatus resources={[
    {create_step: "a", cleanup_step: "b", owner_run_id: "this-run", resource_id: "fresh-owned",
      status: "OWNED", cleanup_result: "NOT_SENT"},
    {create_step: "c", cleanup_step: "d", owner_run_id: "this-run", resource_id: null,
      status: "CREATE_OUTCOME_UNKNOWN", cleanup_result: "NOT_SENT"},
  ]} />);
  expect(screen.getByText(/已准备 1 项，已清理 0 项/)).toBeVisible();
  expect(screen.getByText(/清理未完成：请按本轮资源证据核实/)).toBeVisible();
  expect(screen.getByText(/1 项写入结果尚未核实/)).toBeVisible();
  expect(screen.queryByRole("button")).not.toBeInTheDocument();
});

test("a fully cleaned run does not show an unresolved warning", () => {
  render(<LifecycleStatus resources={[{create_step: "a", cleanup_step: "b",
    owner_run_id: "this-run", resource_id: "fresh-owned", status: "CLEANED",
    cleanup_result: "PASS"}]} />);
  expect(screen.getByText(/已准备 1 项，已清理 1 项/)).toBeVisible();
  expect(screen.queryByText(/清理未完成/)).not.toBeInTheDocument();
});
