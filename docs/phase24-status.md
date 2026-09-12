# Phase 24 — Work Plan Supervision & Human Control

Status: complete and fully verified on the Phase 24 feature branch; ready to merge to `main`.

## Objective

Phase 24 adds a persistent supervision layer on top of the Phase 22/23 WorkPlan and WorkPlanWorker execution model. It improves human control over long-running plans without adding any new filesystem, shell, browser, network, overwrite, delete, rollback or recovery authority.

## Pause and continue

A supervisor may pause only a queued or running plan.

- queued plans become paused immediately before Worker claim;
- running plans become pausing;
- pausing never force-kills the current tool;
- after the current tool returns through its existing safe implementation, the plan becomes paused and no later step starts;
- Continue is allowed only when a paused plan has no unresolved failed/interrupted step;
- failed/interrupted steps still require explicit Retry.

## Step skip rules

Human Skip is deliberately narrow.

A step may be skipped only when:

- it has not executed yet;
- it is an auto step rather than a Phase 11–16 proposal gate;
- it has not created an external proposal;
- no active later step depends on it;
- the supervisor supplies a reason.

Skipped steps are persisted in the timeline and count as resolved for plan progress. Skip cannot be used to bypass an existing-file confirmation gate.

## Execution budgets

Each plan persists:

- maximum automatic/tool step attempts;
- used step attempts;
- cumulative runtime budget in seconds;
- consumed runtime seconds.

Before starting another tool, the WorkPlanService checks both budgets. Exhaustion pauses the plan and creates an action notification. A human may increase the budget through the supervision settings API and then Continue.

Budgets never downgrade PermissionGate or confirmation requirements.

## Step timeout

Each plan persists a supervised per-step timeout threshold.

The timeout is intentionally soft:

- DeskAI never kills a Python thread or tool halfway through execution;
- elapsed wall-clock duration is measured around the existing ToolRegistry call;
- if the threshold is exceeded, the completed tool result is persisted;
- for normal auto steps, the plan pauses before any later step starts;
- for proposal-gate steps, the original file confirmation gate remains authoritative; after that confirmation completes, the plan enters `paused` before any later step can run, preserving the timeout stop condition for human review.

This avoids converting a timeout into an ambiguous partially executed tool state.

## Failure policy

Phase 24 supports two explicit policies:

- pause — persist the failed step and pause the plan for human Retry/inspection;
- stop — persist the failed step and terminate the plan as failed.

There is no automatic skip-on-failure policy.

## Risk-based human approval

The supervisor may configure an approval risk threshold from 0 through 4.

- 4 disables the extra pre-step approval layer;
- 0–3 require auto steps at or above that local risk level to enter awaiting_step_approval before execution;
- the risk value comes from the local PermissionGate-derived ToolRegistry catalog, never from model output;
- approval only allows that already-registered auto step to return to the queue;
- proposal_gate steps continue to use the original Phase 11–16 confirmation lifecycle and are never replaced by Phase 24 approval.

## Timeline and notifications

Phase 24 adds persistent WorkPlanEvent records.

Events capture supervision milestones such as:

- drafted / queued / completed;
- pause / continue / cancel;
- step approval required / approved;
- step skipped;
- budget exhaustion;
- timeout;
- step failure;
- file proposal confirmation required;
- restart recovery;
- Worker failure.

Warning/action events remain unread until acknowledged. Plan detail exposes the latest timeline and unread notification count, while full event and notification APIs remain available.

## Estimate

Plan detail exposes:

- total steps;
- completed steps;
- skipped steps;
- remaining unresolved steps;
- average duration of completed measured steps;
- estimated remaining seconds based on that average.

ETA is an operational estimate only and does not alter scheduling or safety decisions.

## API

- PATCH /work-plans/{plan_id}/supervision
- POST /work-plans/{plan_id}/pause
- POST /work-plans/{plan_id}/continue
- POST /work-plans/{plan_id}/steps/{step_id}/approve
- POST /work-plans/{plan_id}/steps/{step_id}/skip
- GET /work-plans/{plan_id}/events
- GET /work-plans/{plan_id}/notifications
- POST /work-plans/{plan_id}/events/{event_id}/acknowledge

Existing Start / Resume / Retry / Cancel and WorkPlanWorker endpoints remain unchanged.

## Desktop

The Task plan panel now shows:

- persistent plan status and progress;
- remaining steps and ETA;
- step-attempt and runtime budgets;
- per-step timeout threshold;
- failure policy;
- approval risk threshold;
- unread notification count;
- latest execution timeline;
- per-step duration, timeout, approval and skip evidence;
- Pause / Continue;
- Approve Step;
- Skip Step;
- Retry;
- Cancel;
- Supervision Settings.

## Safety invariants

- Pause and timeout do not force-kill an executing tool;
- Worker ownership uses a conditional `queued → running` claim; a concurrent Pause cannot be silently overwritten by a stale Worker selection;
- Step execution, Approve, and Skip use conditional state transitions so concurrent human control cannot resurrect cancelled/skipped work;
- skipping an unrelated pending step never releases another step's `awaiting_step_approval` gate;
- Skip cannot bypass proposal_gate or active dependencies;
- risk approval can only add a human gate and cannot reduce PermissionGate restrictions;
- approval does not replace source edit / organization / recycle confirmation;
- budget changes do not add tools or permissions;
- failure policy never automatically skips a failed step;
- notification acknowledgement changes only notification metadata;
- WorkPlanEvent does not contain new filesystem authority;
- the original Phase 11–23 safety state machines remain authoritative.

## Verification

- Feature PR: #43 — Phase 24 — Work Plan Supervision & Human Control.
- Verified feature head: `e28407c674e9fc59462eb85403215d6ce2ff36c4`.
- CI: Run #130, workflow run ID `34702783662`, success across Engine, desktop-web, and windows-native.
- Engine: `169 passed, 59 warnings`; Ruff: all checks passed.
- Packaged Engine smoke: `0.24.0`.
- Packaged Work Plan endpoint: verified.
- Packaged Work Plan Worker: verified.
- Packaged Work Plan Supervision routes: verified.
- Windows installers:
  - `DeskAI Work_0.1.0_x64-setup.exe`
  - `DeskAI Work_0.1.0_x64_en-US.msi`
- Windows artifact: `DeskAI-Work-Windows`, artifact ID `10300976210`, size `394621390` bytes.
- Artifact SHA-256: `b73e9d78c1406adeb6be440be495cf01c96b4b3286b35617cb6f14771e0396f9`.
- Artifact created: 2026-09-12T15:51:34Z; expires: 2026-12-11T15:37:19Z.
- No new filesystem, shell, browser, network, overwrite, delete, rollback, recovery, or unattended destructive authority was added.

The final feature merge SHA will be recorded after PR #43 is merged.
