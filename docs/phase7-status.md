# Phase 7 — Task Orchestrator and audited read-only Agent

Status: implemented on the Phase 7 branch and pending final CI verification.

## Objective

Phase 7 is the first execution phase.

DeskAI can accept a persistent work Task, let a model decide which explicitly registered read-only tools are needed, execute those tools locally through a permission gate, audit every call, and return a final result.

The model never receives direct OS access.

## Execution pipeline

`Task -> AgentWorker -> AgentRun -> Responses function call -> PermissionGate -> ToolRegistry -> ToolCall/AuditLog -> function_call_output -> final result`

Tasks are persistent SQLite records. A pending task survives application restart.

If the Engine detects a task left in `running` state after an interrupted process, it returns the task to `pending` and marks the previous AgentRun as interrupted before normal processing resumes.

## Phase 7 tools

Only four read-only functions are exposed:

### search_knowledge

Searches Phase 4 hybrid retrieval inside the active Workspace.

Risk level: L1.

### search_memory

Searches active global + current-Workspace durable Memory.

Risk level: L1.

### list_workspace_files

Lists metadata for files already associated with the active Workspace.

It does not open arbitrary filesystem paths.

Risk level: L2.

### read_parsed_document

Reads an existing parsed-document cache only after validating that the requested File belongs to the active Workspace and has a current parsed/indexed version.

Risk level: L2.

## Explicitly unavailable

Phase 7 does not expose:

- delete or overwrite
- arbitrary local file writes
- shell/PowerShell
- Python execution
- browser/computer control
- email or messaging
- database mutation tools
- payment actions
- credential reading
- arbitrary filesystem traversal

A model-generated call to a tool that is not registered is denied even if the model invents a valid-looking function name.

## Permission model

Tool availability is controlled twice:

1. only registered Phase 7 tool definitions are sent to the model;
2. every actual call is checked again by PermissionGate immediately before execution.

Permissions use capability keys such as:

`agent.tool.search_knowledge`

The default Phase 7 read-only policy allows the four registered tools inside an active Workspace. A global or Workspace Permission record can explicitly disable a capability.

Unknown tools default to risk L8, require confirmation, and are denied.

## Stateless provider loop

Agent execution uses the OpenAI Responses API with custom function tools.

DeskAI keeps the Agent loop stateless from the provider perspective:

- `store=false`
- no OpenAI-hosted conversation is treated as task state
- DeskAI retains prior Response output items locally for the current run
- tool results are appended as `function_call_output` items
- the next Responses request continues from those locally held inputs

The provider output is not trusted as proof an action happened. A tool action is considered executed only after ToolRegistry has a corresponding completed ToolCall/AuditLog record.

## Limits

Phase 7 applies deterministic local limits:

- maximum 8 Agent reasoning/tool rounds per task
- maximum 12 tool calls per task
- tool-specific result limits
- parsed-document text caps
- Workspace scope validation
- tool output truncation before it is returned to the model

These limits prevent unbounded loops and uncontrolled context growth.

## Audit model

Every Agent execution records AgentRun.

Every tool attempt records ToolCall with:

- tool name
- arguments
- status
- risk level
- confirmation requirement
- result summary
- start/end timestamps

AuditLog records:

- agent_started
- agent_completed
- agent_failed
- agent_blocked
- agent_interrupted
- worker_failed
- tool_completed
- tool_failed
- tool_denied

## Privacy behavior

Local Only mode blocks cloud Agent execution until a local Agent model provider exists.

Hybrid/Cloud modes require a configured provider credential.

The model receives only:

- the explicit user Task;
- tool definitions currently allowed;
- tool results returned during that Task.

It does not automatically receive the whole Workspace.

## Desktop UX

The “任务” page provides:

- task creation
- persistent queue/status
- manual queue processing
- progress
- final result
- error/block reason
- retry
- AgentRun summary
- individual ToolCall arguments, result summary, status, and risk level

The “活动” page provides a persistent audit timeline for the current Workspace.

## Failure behavior

A provider failure or tool-loop failure marks the Task and AgentRun failed.

A policy/credential/privacy block marks the Task blocked.

Failed or blocked Tasks can be explicitly retried. Retry clears the prior transient execution state but preserves historical AgentRun, ToolCall, and AuditLog records.

## Verification

CI tests use a fake Agent provider and never consume a real API key.

Phase 7 tests verify:

- Agent tool-call loop completion;
- local Memory tool result flows back into the next model step;
- ToolCall and AuditLog persistence;
- Workspace file isolation;
- an invented destructive tool is denied at PermissionGate;
- Local Only blocks the Agent before provider invocation;
- blocked task retry succeeds after policy changes;
- task listing/status remain Workspace-scoped.

## Phase boundary

Phase 7 is intentionally read-only.

Later phases may add write-capable document/spreadsheet/Python/web/computer tools, but each new capability must declare a risk level, path/domain scope, confirmation rule, and audit behavior before it can be registered.
