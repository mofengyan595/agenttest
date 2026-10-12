import { workflowStages } from "./workflowStatus";
import { runFixture } from "./test/fixtures";

test("node entry and false defaults cannot imply success or failure", () => {
  const stages = workflowStages(
    runFixture({
      workflow_nodes: ["plan", "validate", "execute"],
      dsl_valid: false,
      policy_authorized: false,
    }),
  );
  expect(stages[2].state).toBe("observed");
  expect(stages[3].state).toBe("observed");
  expect(stages[5].state).toBe("observed");
  expect(workflowStages(runFixture())[2].state).toBe("unknown");
});
test("explicit validation conclusions and review are distinct", () => {
  expect(
    workflowStages(
      runFixture({
        dsl_valid: true,
        policy_authorized: true,
        task_mode: "operator_plan",
      }),
    )[2].state,
  ).toBe("success");
  expect(
    workflowStages(
      runFixture({
        workflow_nodes: ["validate"],
        validation_issue: "POLICY_NOT_APPROVED",
      }),
    )[2].state,
  ).toBe("failed");
  expect(
    workflowStages(
      runFixture({ outcome: "NEEDS_REVIEW", workflow_completed: true }),
    )[3].state,
  ).toBe("unknown");
});

test("model diagnostic marks a failed Planner without treating unrelated issues as model failure", () => {
  expect(
    workflowStages(
      runFixture({
        workflow_nodes: ["plan"],
        diagnostic: { layer: "model", code: "MODEL_INVALID_RESPONSE" },
      }),
    )[1].state,
  ).toBe("failed");
  expect(
    workflowStages(
      runFixture({
        workflow_nodes: ["plan"],
        diagnostic: { layer: "dsl", code: "DSL_INVALID" },
      }),
    )[1].state,
  ).toBe("observed");
});
