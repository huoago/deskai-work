# Phase 23 — Durable background work-plan runner

Status: implemented on the Phase 23 verification branch; full CI and Windows artifact verification pending.

## Objective

Phase 23 turns Phase 22 persistent work plans into durable background execution. Start, Resume and Retry now perform only a persisted state transition and wake a dedicated WorkPlanWorker; the HTTP request no longer runs the entire plan synchronously.

The Phase 22 dependency graph, ToolRegistry, PermissionGate, ToolCall/AuditLog records and original Phase 11–16 confirmation state machines remain authoritative.

## Durable queue

Plan lifecycle adds two explicit runtime states:

- `queued` — persisted and ready for WorkPlanWorker claim;
- `cancelling` — cancellation was requested while a tool was already running; no new step may begin.

The dedicated WorkPlanWorker:

- claims the oldest queued plan;
- persists plan/task as running before execution;
- advances the existing dependency-aware WorkPlanService;
- persists every completed step before selecting later work;
- stops immediately at original proposal confirmation gates;
- exposes a read-only worker status endpoint;
- provides a bounded manual process endpoint for deterministic tests/support.

## API behavior

Existing endpoints retain their meaning but are now non-blocking:

- `POST /work-plans/{plan_id}/start` → queue and wake worker;
- `POST /work-plans/{plan_id}/resume` → validate the original confirmation gate, queue and wake worker;
- `POST /work-plans/{plan_id}/retry` → reset one eligible failed/interrupted step, queue and wake worker;
- `POST /work-plans/{plan_id}/cancel` → immediate cancel if not actively running, otherwise persist cancelling and stop after the current tool returns safely.

Worker endpoints:

- `GET /work-plan-worker/status`
- `POST /work-plan-worker/process?limit=N`

The manual process endpoint follows the same WorkPlanWorker code path and does not bypass any permission or confirmation rule.

## Restart recovery

On Engine startup:

- queued plans remain queued;
- awaiting-confirmation plans remain frozen at their original human gate;
- running plans with no `running` step are safe checkpoints and are requeued while retaining completed steps/results;
- running plans with a `running` step are ambiguous and freeze as `paused`; the unproven step becomes `interrupted`;
- cancelling plans with no running step finish cancellation;
- an interrupted step is never replayed automatically.

This distinguishes “the Worker had claimed the plan” from “a tool might have partially executed”.

## Safe cancellation

Cancellation never force-kills a running tool.

If cancellation arrives while a tool is active:

1. plan/task become `cancelling`;
2. the current tool is allowed to return through its existing safe implementation;
3. the result is persisted as the final completed checkpoint;
4. all remaining pending plan steps are cancelled;
5. no later plan step is started.

If the current tool is a Phase 11–16 `propose_*` tool, its persistent proposal is retained independently. The proposal remains subject to the original human confirmation flow; cancellation does not apply, reject, delete or roll it back.

## Desktop

- selected Task detail automatically refreshes while the Tasks page is open;
- the global Workspace poll includes WorkPlanWorker status;
- Tasks shows separate Agent Worker and Plan Worker status;
- plan/task status labels include background queue and safe cancellation;
- Start/Resume/Retry messages state that work was queued rather than pretending the work completed synchronously;
- active cancellation explains that the current tool will finish safely before execution stops.

## Safety invariants

- no new filesystem mutation capability;
- no new file confirmation capability;
- no force-kill of a tool in progress;
- no automatic replay of an unproven running step;
- no automatic replanning around denied or rejected mutation steps;
- no automatic confirmation of Phase 11–16 proposals;
- no automatic deletion/rejection of proposals on plan cancellation;
- local PermissionGate remains authoritative;
- all tool attempts continue through ToolRegistry and persistent ToolCall/AuditLog records;
- worker startup recovery acts only on persisted plan/step status.

Final test count, CI run, Engine 0.23.0 packaged worker smoke, Windows installer artifact, PR and merge SHA will be recorded after verification.
