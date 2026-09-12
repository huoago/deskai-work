# Phase 22 — Controlled multi-step work plans

Status: implemented on the Phase 22 verification branch; full CI and Windows artifact verification pending.

## Objective

Phase 22 moves DeskAI Work from isolated safe tools toward persistent multi-step work execution without weakening any Phase 11–21 file-safety boundary.

A plan-mode Task is not consumed by the legacy Agent Worker. Instead, DeskAI drafts a persistent WorkPlan with ordered WorkPlanStep records, explicit dependencies, locally recomputed risk levels, execution results and pause/resume state.

## Plan drafting

- plan tasks use `execution_mode=plan`;
- the configured model drafts 1–12 executable steps through Structured Output;
- the model receives the registered tool catalog plus authorized Workspace file metadata only;
- every selected tool must already exist in ToolRegistry and pass PermissionGate;
- risk_level and execution_mode are taken from local policy, never from model output;
- the planner cannot invent confirmation, shell, arbitrary Python, browser, overwrite, purge or other missing capabilities;
- invalid/forward/cyclic dependencies are rejected before persistence.

## Dependency result references

Later steps may use explicit references such as:

- `{{step:1.result.answer}}`
- `{{step:2.result.result}}`
- `{{step:1.result.files.0.file_id}}`

References are resolved only from completed declared dependency steps. Hidden or undeclared cross-step references are rejected.

## Execution

After human review, the user explicitly starts the plan.

- auto steps execute through the existing ToolRegistry;
- ToolCall and AuditLog remain authoritative;
- dependencies must be completed before a step can run;
- structured step results are persisted for later dependencies and UI inspection;
- new generated DOCX/XLSX artifacts remain inside the existing generated-artifact sandbox.

## Existing-file confirmation gates

All existing-file actions remain proposal-only:

- propose_source_file_edit
- propose_source_file_edit_batch
- propose_file_organization
- propose_file_organization_batch
- propose_file_recycle
- propose_file_recycle_batch

After one of these tools creates its existing Phase 11–16 proposal, the WorkPlan immediately enters `awaiting_confirmation`.

The plan does not own a new confirmation API. The user must use the original transaction confirmation control. Resume checks the persisted proposal status and continues only after the expected safe state is proven:

- source edit / organization → applied
- recycle → recycled

Rejected, rolled-back/restored, missing or recovery-required proposal state blocks the plan. DeskAI never automatically replans around a rejected file action.

## Cancellation and interruption

- cancelling a plan stops pending plan execution;
- any already-created file proposal is preserved unchanged and is not auto-rejected or deleted;
- Engine restart does not replay a running step;
- an interrupted plan freezes as `paused` and the current step as `interrupted`;
- explicit retry is required;
- a step that already created a persistent file proposal cannot be automatically retried.

## API

- `GET /work-plans`
- `GET /work-plans/{plan_id}`
- `POST /tasks/{task_id}/work-plan`
- `POST /work-plans/{plan_id}/start`
- `POST /work-plans/{plan_id}/resume`
- `POST /work-plans/{plan_id}/retry`
- `POST /work-plans/{plan_id}/cancel`

## Desktop

Tasks now support two entry paths:

- immediate Agent execution;
- generate multi-step plan.

Plan-mode task detail exposes the plan summary, limitations, dependency graph, tool/risk metadata, step arguments/results, confirmation-gate linkage, start/resume/retry/cancel actions and progress.

## Security invariants

- no arbitrary tool names;
- no model-controlled risk downgrade;
- no plan-owned file mutation API;
- no plan-owned confirmation/rollback/restore API;
- no automatic force overwrite or ignore-SHA behavior;
- no automatic replanning around rejected mutation steps;
- no automatic replay after ambiguous interruption;
- no Agent Worker competition with plan-mode Tasks;
- original Phase 11–21 safety state machines remain authoritative.

Final test count, CI run, packaged Engine 0.22.0 smoke result, Windows installers, Artifact ID/hash, PR number and merge SHA will be recorded after verification.
