# Phase 22 — Controlled multi-step work plans

Status: complete, fully verified, and merged to main.

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

## Verification and release evidence

Feature PR:

- PR #39 — Phase 22 — Controlled multi-step work plans
- feature head: `31d04927a383760ec357b8fa4567779a2ca2647e`
- feature merge SHA: `8006f9e8bd17c982f308955a2286ba71d728bd05`

CI history:

- Run #111 / ID `34692933294`: desktop-web passed; Engine stopped at Ruff because of one unused local variable (`F841`). No functional or test failure was merged.
- the Ruff-only issue was removed on the feature branch.
- Run #112 / ID `34692976649`: final feature verification succeeded.

Final Run #112 results:

- desktop TypeScript typecheck: passed
- desktop Vite production build: passed
- Ruff: passed
- Engine pytest: **150 passed, 59 warnings in 54.63s**
- Windows packaged sidecar build: passed
- packaged Engine smoke: passed
- packaged Engine version: **0.22.0**
- packaged Work Plan endpoint smoke: **count=0**
- Tauri Windows native build: passed
- NSIS installer: `DeskAI Work_0.1.0_x64-setup.exe`
- MSI installer: `DeskAI Work_0.1.0_x64_en-US.msi`

Windows artifact:

- name: `DeskAI-Work-Windows`
- Artifact ID: **10298471688**
- size: **394,508,349 bytes**
- SHA-256: `ca1ab21f894d81fc166ad15fdfed7c4bcebe1fbe3d9a719bb305c4d2fd614400`
- created: 2026-09-12T12:24:07Z
- expires: 2026-12-11T12:11:02Z
- artifact was not expired at verification time.

## Final Phase 22 state

Phase 22 is now the verified mainline implementation for persistent multi-step work execution. It adds orchestration, persistence and explicit resume/retry semantics without introducing any new direct mutation authority over Workspace files. The original Phase 11–21 safety state machines remain the source of truth for protected file actions and recovery.
