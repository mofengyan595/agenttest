import type { components } from "./schema";
// The API omits fields absent in legacy files. Keep null/unknown distinct on the UI.
type ValidationKey = "dsl_valid" | "policy_authorized" | "intent_verified";
export type RunReport = Omit<
  components["schemas"]["RunReport"],
  ValidationKey
> &
  Partial<Record<ValidationKey, boolean | null>>;
export type StepResult = components["schemas"]["StepResult"];
export type RunPage = components["schemas"]["RunPage"];
export type ContractSummary = components["schemas"]["ContractSummary"];
export type Outcome = components["schemas"]["Outcome"];
