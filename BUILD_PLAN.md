# Walking On Sunshine MVP Build Plan

This document is the persistent execution plan for the hackathon MVP. GitHub issues are the source of truth for scope and acceptance criteria; this file records ownership, dependencies, critical paths, and integration order.

## Outcome

Deliver a judge-ready longitudinal gait monitoring demo that:

- captures or simulates a walking session;
- reports gait speed and supporting gait measurements;
- compares a valid session with a three-session personal baseline;
- rejects or clearly labels poor-quality recordings;
- explains change in accessible, non-diagnostic language;
- works without an LLM and has a deterministic demo fallback.

The current prototype fall-risk value must not be represented as a clinically validated probability. Until outcome validation exists, use “mobility trend” or “gait-change indicator.”

## Team split

Exactly three people own the work. Each person should work through their lane in priority order and avoid editing another lane's implementation unless coordinating an interface or unblocking integration.

| Person | Workstream | Issues | Primary files/surfaces |
|---|---|---|---|
| Person 1 | Platform & Integration | [#4](https://github.com/JiyaS12/WalkingOnSunshine/issues/4), [#5](https://github.com/JiyaS12/WalkingOnSunshine/issues/5), [#6](https://github.com/JiyaS12/WalkingOnSunshine/issues/6), [#7](https://github.com/JiyaS12/WalkingOnSunshine/issues/7) | branch integration, CI, camera architecture, runtime configuration |
| Person 2 | Backend & Measurement | [#8](https://github.com/JiyaS12/WalkingOnSunshine/issues/8), [#9](https://github.com/JiyaS12/WalkingOnSunshine/issues/9), [#10](https://github.com/JiyaS12/WalkingOnSunshine/issues/10), [#11](https://github.com/JiyaS12/WalkingOnSunshine/issues/11) | API models, processing, session state, measurement fixtures/tests |
| Person 3 | Patient Experience & Demo | [#12](https://github.com/JiyaS12/WalkingOnSunshine/issues/12), [#13](https://github.com/JiyaS12/WalkingOnSunshine/issues/13), [#14](https://github.com/JiyaS12/WalkingOnSunshine/issues/14), [#15](https://github.com/JiyaS12/WalkingOnSunshine/issues/15) | guided UX, results language, privacy, end-to-end demo/runbook |

## Issue inventory

### Person 1 — Platform & Integration

1. [#4 Establish the canonical MVP integration branch](https://github.com/JiyaS12/WalkingOnSunshine/issues/4) — P0, starts immediately, integration bottleneck.
2. [#5 Add backend/frontend CI](https://github.com/JiyaS12/WalkingOnSunshine/issues/5) — P0, follows #4.
3. [#7 Harden runtime configuration and failure behavior](https://github.com/JiyaS12/WalkingOnSunshine/issues/7) — P1, follows #4; do before final deployment testing.
4. [#6 Decompose `WebcamFeed`](https://github.com/JiyaS12/WalkingOnSunshine/issues/6) — P1, follows #4; preserve behavior and avoid blocking the demo for optional cleanup.

### Person 2 — Backend & Measurement

1. [#8 Define the versioned session/baseline contract](https://github.com/JiyaS12/WalkingOnSunshine/issues/8) — P0, starts immediately and publishes fixtures early.
2. [#9 Make gait speed and within-person change primary](https://github.com/JiyaS12/WalkingOnSunshine/issues/9) — P0, follows the #8 contract.
3. [#10 Add telemetry quality and comparability gates](https://github.com/JiyaS12/WalkingOnSunshine/issues/10) — P0, follows #8; coordinate reason codes with Person 3.
4. [#11 Add deterministic fixtures and regression coverage](https://github.com/JiyaS12/WalkingOnSunshine/issues/11) — P1, follows #9 and #10.

### Person 3 — Patient Experience & Demo

1. [#12 Build guided capture and baseline onboarding](https://github.com/JiyaS12/WalkingOnSunshine/issues/12) — P0, starts immediately using fixtures.
2. [#14 Add consent and local-first privacy behavior](https://github.com/JiyaS12/WalkingOnSunshine/issues/14) — P0, independent; work on it while waiting for final metric fields.
3. [#13 Build the longitudinal results experience](https://github.com/JiyaS12/WalkingOnSunshine/issues/13) — P0, prototype from #8 fixtures; finalize after #9 and #10.
4. [#15 Integrate and validate the judge-ready demo](https://github.com/JiyaS12/WalkingOnSunshine/issues/15) — P0, final convergence and release gate.

## Dependency graph

```text
Person 1:  #4 ──> #5 ──> #7 ──> #6 ──┐
             │                         │
             └─────────────────────────┼──────────┐
                                       │          v
Person 2:  #8 ──> #9 ──> #10 ──> #11 ├───────> #15
             │      │       │          │
             │      └───────┼──> #13 ─┤
             └──────────────>│          │
                             │          │
Person 3:  #12 ──> #14 ─────┴──> #13 ─┘
```

The arrows show the safest integration order, not a requirement to wait before doing mock-driven frontend work. Person 3 should build against the #8 fixtures while #9 and #10 are being implemented.

## Optimal execution waves

### Wave 0 — Start all three lanes immediately

- Person 1: #4, establish the canonical Phase 2 integration line and port hardening fixes.
- Person 2: #8, freeze the API/session contract and publish small fixtures.
- Person 3: #12, build the guided flow against fixtures; no dependency on a finished backend.

Exit condition: the canonical branch exists, the payload contract is reviewable, and the main patient flow can render mocked states.

### Wave 1 — Build the demo-critical core in parallel

- Person 1: #5, make clean backend/frontend verification automatic.
- Person 2: #9, implement gait speed and baseline/prior-session deltas.
- Person 3: #14, complete consent and local-first privacy behavior while metric implementation settles.

Exit condition: every new merge is checked, core longitudinal measurements exist, and live capture has an honest data-use flow.

### Wave 2 — Connect quality to the user experience

- Person 1: #7, harden configuration, CORS, timeouts, and fallback behavior.
- Person 2: #10, add quality/comparability results and gates.
- Person 3: #13, finish the dashboard using #9 measurements and #10 quality states.

Exit condition: unreliable recordings cannot appear as meaningful health change, and the UI explains every success/warning/retry state.

### Wave 3 — Stabilize without changing the product contract

- Person 1: #6, decompose camera code with behavior-preserving tests.
- Person 2: #11, complete deterministic processor and longitudinal regression coverage.
- Person 3: prepare #15's runbook, fixture reset path, pitch sequence, and failure checklist without declaring it complete.

Exit condition: the major technical paths have automated evidence and the final demo checklist is ready.

### Wave 4 — Final convergence

- Person 3 leads #15.
- Person 1 owns setup/deployment/camera failures.
- Person 2 owns metric, data-quality, and fixture failures.
- All three run the smoke-test checklist on the actual demo machine.

Exit condition: both live and deterministic fallback demonstrations complete in under three minutes from a documented setup.

## Bottlenecks and how to contain them

### #4 — Canonical integration branch

This is the first repository bottleneck. Keep it narrow: integrate PR #2, port applicable PR #3 fixes, verify, and merge. Do not combine metric redesign or major UI changes into it. Persons 2 and 3 continue with contract/fixture work while #4 is active.

### #8 — Shared session contract

This is the interface bottleneck. Publish example payloads early. After agreement, incompatible field changes require coordination with both frontend and backend owners.

### #10 — Quality gate semantics

Person 3 needs stable status and reason codes to finish results. Agree on a minimal set (`valid`, `warning`, `rejected`) and machine-readable reasons before polishing thresholds.

### #15 — Final integration

This is the release bottleneck. No new features enter once #15 begins unless they fix a failed acceptance criterion. Prefer a reliable fallback over additional scope.

## Branch and pull-request protocol

- One branch and one pull request per issue.
- Branch names use `codex/<issue-number>-short-description`.
- Every PR links its issue with `Closes #N` and repeats the acceptance checklist.
- Rebase or merge the latest canonical integration branch before requesting final review.
- Keep contract fixtures backward compatible during a wave.
- Do not mix opportunistic refactors into demo-critical PRs.
- Person 3 is the integration owner for #15; Persons 1 and 2 remain responsible for failures in their own lanes.

## Merge order

Preferred order when work is ready:

1. #4 and #8
2. #12 and #14
3. #5 and #9
4. #7 and #10
5. #13
6. #6 and #11
7. #15

Independent items in the same numbered step may merge in either order after checks pass.

## Scope cuts if time becomes critical

Cut or simplify in this order while retaining a coherent demo:

1. Defer deep internal decomposition in #6 after extracting only the riskiest camera lifecycle logic.
2. Use simple local JSON/in-memory session persistence rather than adding a database.
3. Use deterministic template summaries instead of debugging an LLM integration.
4. Keep only three quality states and the most actionable reason codes.
5. Demonstrate one patient and a fixed three-session baseline.

Never cut input validation, quality gating, simulated fallback, consent, or the non-diagnostic wording.

## Definition of done

- All twelve issues meet their acceptance criteria or have an explicitly documented, judge-safe scope cut.
- CI is green from a clean checkout.
- A real camera session works on the target machine.
- Simulated mode works with the camera, network, and OpenAI unavailable.
- Poor-quality sessions do not update or produce a misleading trend.
- The demo uses gait speed and within-person change as its primary story.
- No UI or summary claims diagnosis, validated fall prediction, or regulatory compliance.
- `PROJECT_LOG.md` remains local, ignored, and unpushed.

