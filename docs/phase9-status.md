# Phase 9 — Controlled local calculation and table analysis

Status: implemented on the Phase 9 branch and pending final CI verification.

## Objective

Phase 9 gives DeskAI deterministic local data-analysis capability without exposing arbitrary Python execution.

The Agent can:

- evaluate bounded numeric expressions;
- inspect parsed CSV/XLSX tables;
- compute column summary statistics;
- group and aggregate tabular data.

All table operations use the existing parsed-document cache for the active Workspace.

## Why arbitrary Python is still disabled

A general `exec()`, REPL, shell-backed Python runner, or unrestricted notebook would create an escape path around DeskAI's file, network, process, and permission boundaries.

Phase 9 therefore exposes structured operations rather than source-code execution.

There is no tool for:

- `import`
- module loading
- attribute access
- filesystem access
- network access
- subprocess creation
- environment-variable access
- arbitrary Python statements
- dynamic code execution

## Tools

### calculate_expression

Risk level: L1.

Evaluates one numeric expression using Python's AST parser with a strict allow-list.

Supported elements include:

- numeric constants
- named numeric variables supplied explicitly by the tool request
- +, -, *, /, //, %, **
- unary +/-
- approved numeric functions:
  - abs
  - round
  - min
  - max
  - sqrt
  - log
  - log10
  - exp
  - floor
  - ceil

Safety limits include:

- maximum expression length
- maximum AST node count
- numeric literal bounds
- constant exponent requirement
- exponent magnitude limit
- no attributes/subscripts/comprehensions/lambdas/imports

### inspect_table

Risk level: L1.

Reads headers and a bounded row preview from a parsed CSV/XLSX file.

The requested File must:

- belong to the active Workspace;
- be parsed or indexed;
- have a current FileVersion;
- be CSV or XLSX;
- have a matching parsed cache entry.

For XLSX, the Agent may select one parsed sheet by name.

### summarize_table

Risk level: L1.

For up to 20 selected columns, returns:

- non-empty count
- empty count
- unique count
- numeric count
- min
- max
- sum
- mean
- median

Numeric calculations ignore cells that cannot be parsed as finite numbers.

### aggregate_table

Risk level: L1.

Groups by one column and calculates one of:

- count
- sum
- mean
- min
- max

over a selected value column.

Output is bounded to 100 groups and sorted deterministically by aggregate value then group name.

## Data boundary

Table analysis never accepts a filesystem path.

It receives a DeskAI File id, verifies Workspace ownership, resolves the active FileVersion locally, and reads only the existing parsed cache.

The analysis service currently caps a parsed table at 20,000 data rows per operation.

## Agent behavior

The Agent is instructed to prefer deterministic table tools over estimating values from raw text.

Important arithmetic should use `calculate_expression` rather than being silently recomputed by the model when an exact local calculation is practical.

Phase 8 artifact tools remain available, so a task can:

1. inspect/analyze local tabular data;
2. compute exact results;
3. create a new DOCX/XLSX result artifact.

Source files remain unchanged.

## Audit

Every Phase 9 tool call uses the existing:

`Task -> AgentRun -> PermissionGate -> ToolCall -> AuditLog`

pipeline.

Arguments, status, risk level, and bounded result summary are persisted locally.

## Verification

CI tests verify:

- approved arithmetic returns exact results;
- attempts to use `__import__`, `open`, attribute access, subscripting, list comprehensions, and oversized exponentiation are rejected;
- CSV preview, numeric summary, and grouped aggregation are deterministic;
- XLSX sheet selection works;
- a File id cannot be analyzed from another Workspace;
- an Agent can chain table analysis with exact calculation and receive the local tool outputs;
- no real OpenAI credential is used by tests.

## Phase boundary

Phase 9 is analysis-only.

A later phase may add web research and/or a stronger OS-isolated computation sandbox. Arbitrary local Python execution must not be enabled merely by reusing this expression evaluator.
