# AGENTS.md — instructions for Codex

Project: AgentTest, an independent Python API-testing platform. Read `CODEX_TASK.md` before any changes.

## Hard rules
1. **Independent repo**: never modify or commit changes to `mofengyan595/courseinsight-cloud`. It is a test target, not part of this codebase.
2. Work on the **smallest next milestone** from the verified progress. Stage 1 and the Stage 2 minimal workflow are complete; do not reinitialize or repeat them.
3. Python 3.11 preferred (3.12 acceptable); create `.venv` in repo. Never install packages globally or commit `.venv`.
4. Base project must run and tests must pass **without API keys, Docker, network access, or an LLM**.
5. **Do not run model-generated Python/Shell**. The model produces a validated DSL; the deterministic executor implements an allowlisted operation set.
6. Never target production services. No arbitrary URLs; forbid nonlocal or non-allowlisted target hostnames/IPs and redirects to unapproved destinations; protect against SSRF, including resolved-address and redirect checks. Execute mutating tests only in explicitly authorized isolated environments.
7. No auto-modification of assertions to make failing tests pass. Unknown expectations become `NEEDS_REVIEW`, never invented PASS/BUG.
8. All credentials go in `.env` or environment variables; scrub Authorization headers, cookies, passwords, tokens from trace files and logs.
9. Preserve test artifacts and evidence, classify `ASSERTION_FAILED`, `TEST_INFRA_ERROR`, `TOOL_ERROR`, `NEEDS_REVIEW`, `PASS` distinctly.
10. Keep architecture understandable: small modules, typed models, docstrings for nontrivial behavior, `pytest` plus `ruff` for every change.
11. LangGraph / MCP SDK versions are changing: consult current official docs before implementing. MCP Python SDK **v2** uses `MCPServer` rather than older `FastMCP`; do not copy older v1 imports into a v2 project.
12. After each stage: run checks, show command outputs, note incomplete tests, update `docs/progress.md` and give next exact milestone. Do not claim tests executed if not run.

## Current milestone
The 2026-11-12 P2.1 milestone fixes the reliability defects that kept P2's autonomous loop from
completing, without adding a second harness or agent loop. See
docs/deepagents-reliability-p21.md. Deep Agents stays the only main Agent runtime; LangChain
supplies the ChatModel/tool calling/tools/middleware, LangGraph the runtime and domain
subgraphs, and AgentTest owns Planning IR, compiler, policy, validator, executor and trusted
evidence. Fixed in this milestone: the engine gate (`normalization_applied` meant "clauses were
dropped", so ordinary multi-action requests could never reach the normalized compiler), clause
to instance identity collapse in the compiler, the missing `DATA_DEPENDENCY` edge for approved
policy bindings (create and "read the id it returned" were split into two scenarios), duplicate
derived absence instances, legacy absence ordering, unclassified clauses blocking valid
requests, `MODEL_INVALID_RESPONSE` without position/ceiling diagnostics, gateway refusals
poisoning unknown-usage accounting, and a rejected design being reported as `complete=True`.
A model-proposed binding, selector or literal that disagrees with the approved policy is now
refused (`PLANNING_REFERENCE_MISMATCH` / `DESIGN_LITERAL_NOT_APPROVED`) instead of silently
replaced with the approved value. Every creation counts as `CLEANUP_UNPROVEN` until a lifecycle
receipt proves cleanup. Real ecnu-max, real HTTP, four literal goals, strictly sequential, each
in its own isolated lab: the final acceptance batch reached strict completion 2/4
(`functional-06`, `regression-07`), with `functional-05` and `investigation-03` also complete in
the debug set, versus P2's 0/4. `functional-06` both found a real `ASSERTION_FAILED` and ran a
6-step create-to-delete lifecycle with `created == deleted`. All executed rounds leaked nothing
and produced no duplicate case records (P2's pre-release round leaked 10 of 12). Cost fell to
22k-74k tokens per task against P2's 100k-120k, by context editing, repeated-read pointers,
four memoized tools and early stops, never by dropping errors. Unresolved: a focus order
failure still interrupts a round instead of asking once more, per-task cost is still above P1,
the pre-release goal is still not strictly complete, and two archived cases are preserved as
`xfail(strict=True)` (a repeated identical read compiling to one step, and a diagnostic code
that moved earlier).

### Previous milestone (P2, preserved)
The 2026-11-12 P2 milestone replaces P1's ADAPT conclusion with a controlled adoption: Deep
Agents is now the product's only autonomous Agent runtime, and the legacy RoundSupervisor is
kept solely as a regression/fallback path behind `loop_engine`. See
docs/deepagents-product-runtime-p2.md. `ProjectBudget.loop_engine` defaults to AUTO, which
resolves to DEEP_AGENTS only when the harness is installed and a model is configured; a base
install stays importable and degrades to DETERMINISTIC, while an explicit DEEP_AGENTS request
fails closed. All four entries (Web/CLI/Goal/Suite) share `ProductTasks.run`; the eight product
tools reuse the P1 ProductSession semantics and the single Executor, and the catalogue plus the
`write_file` enforcement probe are re-proved on every run. Task completion is deterministic per
goal type (`agenttest.task-completion.v1`): agent termination, evidence produced, obligations
covered, task complete, anomalies found and pending items are separate flags, `checks_passed`
never implies `complete`, and a model's final answer is not a verdict. Real ecnu-max runs on the
isolated lab (13 independent runs) reached strict completion 0/4: one narrowed functional round
measured 4 of 7 in-scope obligations with a real 503 anomaly, the delete regression passed its
lifecycle but never got the model to author the approved post-delete 404 case, the pre-release
round produced ten repeated create-only cases, and the investigation round sent zero requests
after `PLANNING_INSTANCE_COVERAGE`/`PLANNING_INSTANCE_UNBOUND` rejections. Safety held: zero
unauthorized tool executions, zero unknown-result write replays, zero delete-404 false
positives. The delete-then-read violation is still detected (S1-S5 real-HTTP regression: S4
answers 204 but keeps the record and is reported ASSERTION_FAILED). Offline: 2744 passed /
15 skipped / 0 failed, ruff clean. Unresolved and blocking "strict completion as a product
claim": multi-instance flow coverage in the IR compiler, narrow-target design stability
(`MODEL_INVALID_RESPONSE`), the model-authored absence case, duplicate case shapes in the
pre-release round, and a cost of 3-5x P1 arm A. Do not describe completion rate as improved
until those are fixed.

### Previous milestone (preserved)
The 2026-11-01 reliable testing-colleague Goal supersedes related freezes only within
docs/reliable-colleague-goal.md. Implement its five stages in order from 628b2dd.
Scoped local commits on the current branch are authorized after stage checks; no push,
branches/worktrees, multi-agent, RAG, new framework or real business writes.
Keep all historical failures and UNKNOWN usage. Model calls and self-built isolated
CRUD are authorized without a Goal-wide ceiling; retain per-task budgets and the
operator-specific unknown-usage gates. Do not replay writes after cancellation/restart.
Five stages and scoped self-built acceptance are complete; see
docs/reliable-colleague-delivery.md. Final runtime is 4736758; final offline 2082 passed,
15 skipped, 0 failed. Same-frozen-request real re-verification is 7/9, with two preserved
planning failures; original v1 is 2 passed, 4 failed, 3 unexecuted. Do not retune/repeat
used paid positions to chase a score. Final documentation local commit is authorized;
no push. Actual business integration remains unverified and needs its own environment
and scoped authorization. Preserve all original snapshots and failure records.

### Previous testing-colleague milestone (preserved)
The 2026-10-26 testing-colleague Goal supersedes related product freezes. Follow
docs/testing-colleague-goal.md: complete five stages and fresh three-round real-model
acceptance through the normal web UI on three new documented loopback projects,
including 40+ operations. All necessary configured-model calls and isolated self-built
target CRUD are authorized; no Goal-wide call/token ceiling. Keep per-task budgets,
historical evidence and existing uncommitted work. The operator later explicitly allowed
future unknown-usage continuation for this Goal; preserve UNKNOWN and each timeout,
record the authority separately, and never estimate or replace missing usage. Product
defaults still stop unknown usage for other operators. No multi-agent,
RAG, branches/worktrees, commit/push, CourseInsight changes or real business writes.

### Prior product milestone (preserved)
The 2026-10-26 operator requests an actual product and authorizes completing generic
intake first; the operator will supply the real target environment later. Use real
configured model planning in the normal web workspace; keep simulation explicit and
separate. Implement contract-driven, operator-reviewed operations, inputs, credentials
aliases, dependencies and assertions without matching built-in business profiles.
Necessary product source/configuration and bounded real-model acceptance calls are
authorized by this request; do not reopen any historical paid evaluation schedule.
The user then explicitly said "批准所有真实模型调用" on 2026-10-26. Model calls
within the authorized product work do not need repeated approval; retain per-task
budgets and stop on unknown usage. The first live product task passed with one
ecnu-plus call / 1197 reported tokens / two actual loopback HTTP requests.
No branch/worktree, commit/push, production access, real Java/PocketBase writes,
generated runtime code, weakened assertions, rejected-plan repair or write replay.
Preserve the existing uncommitted web-intake work. See docs/product-intake-goal.md.

### Previous milestone (frozen)
The 2026-10-23 plan-reuse Goal engineering and its only frozen 36-position real
schedule are complete. See docs/plan-reuse-final-results.md: baseline2/18,
candidate9/18 (eligible9/15), candidate projects3/6, approved negatives85/105;
all three quality targets were NOT met. Safety counters0, all usage known.
Baseline86d6822 / candidateedf3d3f products remain frozen; parent11e4697 only adds
scored safety-stop handling. Preserve v1/v2 Mock archives, real failures, original
full-task/diagnosis criteria, and every older result below. No more paid calls,
used-task tuning, assertion changes or feature expansion without a new milestone.
User review package is ready; independent human review/time savings remain unverified.
At handoff verify final delivery checks and exact-HEAD CI; do not reopen the paid schedule.

### Plan-reuse operator objective (preserved)
The new 2026-10-23 plan-reuse Goal in docs/plan-reuse-goal.md authorizes five
incremental stages: bounded mature-tool comparison, validated immutable plan reuse
and deterministic variants, shared entry integration/attribution, a NEW frozen
six-task three-round baseline/candidate comparison, and user-review/value delivery.
Baseline is 86d68225ab39c6f31d950298e51d0c77d1f2cac0. Commits/pushes and configured
model calls are authorized; no branches/worktrees. Schemathesis is an independent
optional evaluation environment first. All historical results below remain frozen.
No multi-agent, RAG, rejected-plan repair, write replay, real Java/PocketBase writes
or CourseInsight modifications. See the new Goal for exact budgets and gates.

### Previous milestone (frozen results)
The scoped 2026-10-23 implementation and frozen paid schedules are complete;
only final delivery checks/documentation and exact-SHA CI remain in scope.
See docs/trusted-testing-final-results.md: real full tasks12/36, eligible12/30,
funded projects0/6; quality targets were NOT met. TV_NEW01 passed once and was
exported to a model-disabled fixed regression. Preserve v1 input-budget failure,
original v2 reference error/scores, amended archives, all old scores and failures.
Do not add paid trials, retune used requests, change assertions or expand features
without a new milestone. Independent human review/time savings remain unverified.

### Operator objective (preserved)
The 2026-10-23 Goal establishes the incremental milestone in
docs/trusted-testing-goal.md: approved negative execution, bounded project planning,
expression adaptation and a third synthetic project, evidence attribution, then a
new frozen 12-request/three-round evaluation. Scoped commits/pushes and configured
model calls are authorized. Preserve all historical scores and archives. No new
real Java/PocketBase writes, CourseInsight changes, RAG, multi-agent, generated
code execution, repair, recovery or write replay. All existing safety gates apply.

### Previous milestone (preserved)
The 2026-10-20 Goal establishes a new incremental milestone: reusable project-level
natural-language goals, single/flow/project testing, shared tool protocols and bounded
conversation context, then CLI-first SQLite task service and existing-console execution.
Follow the operator objective in docs/nl-entry-goal.md. This supersedes the previous
feature/frontend freeze only for that scope. Scoped commits/pushes and configured model
calls are authorized. Keep every prior archive, failure and score unchanged. Freeze a
new 12-request, three-round evaluation before real calls; labels/reference steps stay
outside product runtime. No RAG, multi-agent, second framework, CourseInsight changes,
real Java/PocketBase writes, model code execution, repair, replay or request recovery.

### Historical freezes (preserved; new Goal scope above takes precedence)
The 2026-10-18 Goal feature scope is now frozen for final acceptance and CI.
The 24-task three-round development schedule is complete; preserve its two scheduler
freezes, all failures and the separate explicit-finish audit. Do not add paid trials,
tune used tasks, or expand features without a new milestone. Formal independent
human evaluation remains pending; deliver the unreviewed enrollment materials.

The operator's 2026-10-18 Goal authorizes five incremental milestones: reference
diagnostics, bounded read-only feedback decisions, reusable onboarding/observability,
fresh paired development evaluation, and demonstration/interview delivery. Implement
these sequentially using existing trusted components; the freeze below applies only
to unrelated expansion. Goal-scoped verified commits/pushes and configured model
calls are authorized. The operator removed the Goal-wide model request/token
ceiling on 2026-10-18; keep frozen per-stage schedules and per-run safety budgets,
no implicit retries; unknown usage stops subsequent paid calls. Formal independent
evaluation still requires genuine independent human review. No new real Java or
PocketBase writes, CourseInsight changes, RAG, multi-agent, repair or write recovery.

Stages 1–4A/4B and scoped Stage 5 delivery are implemented. Preserve raw two-round
2A records, real Java 3A/4A evidence, Stage 3B MCP evidence and independent B01–B04
failures. Maintain the unified offline showcase and Windows/Linux × base/agent CI.
Do not keep tuning used task sets or add new architecture without a new milestone.
New real Java database writes require explicit operator approval. No CourseInsight
business-code changes, Java cross-user assumptions, MCP Java writes, auto-repair,
request recovery, RAG, frontend or multi-agent. Reuse the existing trusted engine.
