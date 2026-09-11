# Phase 10 — Controlled web research

Status: implemented on the Phase 10 branch and pending CI verification.

## Objective

Phase 10 gives DeskAI a real read-only internet research capability while preserving the existing local security and audit boundaries.

The Agent can now call:

- `search_web`

The tool searches the public web through the configured OpenAI Responses provider and returns:

- the normalized query;
- a concise provider-grounded synthesis;
- a bounded list of source titles and URLs;
- provider response/model metadata;
- token counts.

## Provider implementation

`OpenAIChatProvider.web_search()` calls the Responses API with only the hosted:

```text
web_search
```

tool enabled for that request.

DeskAI requests:

```text
web_search_call.action.sources
```

and additionally extracts URL citations from assistant message annotations as a compatibility fallback.

Sources are deduplicated by URL and bounded to at most 10 entries.

## Tool boundary

`search_web` is an L2 read operation.

It does not provide:

- arbitrary URL opening or fetching;
- downloads;
- browser/GUI control;
- form submission;
- shell or subprocess execution;
- arbitrary Python execution;
- local filesystem access;
- local credential access;
- source-file modification.

The tool receives only:

- a query up to 500 characters;
- a requested source limit from 1 to 10.

## Privacy boundary

Web research is disabled when Privacy Mode is:

```text
Local Only
```

The WebResearchService also rejects queries that appear to contain credential-like secrets such as:

- OpenAI-style secret keys;
- bearer tokens;
- explicit password/API-key/token assignments.

The service requires the normal configured OpenAI provider credential. The credential itself is never included in model-visible tool output.

## Prompt-injection boundary

All web content is untrusted data.

The Agent and provider instructions explicitly forbid webpage content from changing DeskAI permissions or causing:

- secret disclosure;
- local-file disclosure;
- downloads;
- code execution;
- payments;
- permission escalation.

Web results cannot add tools or bypass PermissionGate.

## Audit

Every web search still flows through:

```text
Task
→ AgentRun
→ PermissionGate
→ ToolCall
→ AuditLog
```

The model cannot claim that a web search occurred unless a completed local `ToolCall` record exists.

## Verification

Phase 10 tests cover:

- source extraction from hosted web-search actions;
- URL-citation fallback extraction;
- source deduplication;
- credential-like query rejection before provider invocation;
- Local Only web-search denial;
- Agent chaining through `search_web`;
- source URLs returned to the Agent;
- L2 risk recording;
- registration of the tool in the model-visible Tool Registry;
- no real OpenAI credential or real network call in tests.

CI must still pass:

- Ruff;
- full Engine Pytest suite;
- TypeScript typecheck;
- Vite production build;
- Windows PyInstaller sidecar build;
- packaged Engine 0.10.0 smoke test;
- Tauri Windows NSIS/MSI build;
- Windows Artifact upload.

## Phase boundary

Phase 10 is public-web research only.

It is not browser automation and must not be expanded into generic HTTP fetching, downloading, form submission, or computer control without a separately reviewed permission and confirmation model.
