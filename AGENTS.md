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
Stages 1–4A/4B and scoped Stage 5 delivery are implemented. Preserve raw two-round
2A records, real Java 3A/4A evidence, Stage 3B MCP evidence and independent B01–B04
failures. Maintain the unified offline showcase and Windows/Linux × base/agent CI.
Do not keep tuning used task sets or add new architecture without a new milestone.
New real Java database writes require explicit operator approval. No CourseInsight
business-code changes, Java cross-user assumptions, MCP Java writes, auto-repair,
request recovery, RAG, frontend or multi-agent. Reuse the existing trusted engine.
