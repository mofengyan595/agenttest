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
