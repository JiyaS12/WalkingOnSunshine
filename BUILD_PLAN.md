# GaitGuard AI Architecture Completion Plan

This plan replaces the superseded three-person MVP plan. GitHub issues are the
source of truth for scope and acceptance criteria; this document records the
implementation audit, ownership, dependencies, parallel execution waves, and
integration policy for the four-person team.

## Target outcome

Complete the architecture in `docs/architecture.md` as one tested workflow:

1. the voice agent conducts an outbound structured intake;
2. survey data is stored and a secure, expiring patient link is texted;
3. the patient completes a live-camera or uploaded-video walking test at
   `/patient/[id]`;
4. the session is attached only to that patient; and
5. an authenticated clinician reviews the unified subjective/objective report.

A deterministic local path must demonstrate the same handshake without live
telephony, camera hardware, or an OpenAI key.

## Implementation audit at `298bc12`

### Already implemented and retained

- FastAPI health, frame processing, video upload, summary, survey,
  patient-record, session, and synthesis endpoints.
- MediaPipe live-camera capture with a 60-frame leg-length calibration.
- Server-side `.mp4`/`.mov`/`.webm` processing with upload limits and pose
  coverage validation.
- Gait metrics, dropped-landmark handling, seeded patient data, deterministic
  template summaries, and local JSON persistence with rollback/locking.
- `/patient/[id]` base route with live/upload capture, patient timeline, session
  persistence, and error states; `/` is now a landing page.
- `/doctor` cohort/search/detail/trend/synthesis UI with five-second live sync.
- Backend regression suite: 62 tests pass on Python 3.12.
- Frontend lint and the Next.js production build pass.

### Remaining gaps found in code

- Patient routes and APIs still rely on a guessable id: there is no
  signed/expiring link or patient-scoped authorization.
- The patient page still exposes survey details and a Doctor Portal link, and
  the root landing page enumerates patient links.
- Patient upload auto-save uses a callback with stale patient-route closure risk
  if client navigation changes the id.
- No telephony, outbound-call, SMS, or spoken walking-guidance implementation.
- Clinician routes and `/doctor` are unauthenticated.
- Backend CORS uses wildcard origins with credentials.
- No CI workflow or declared runtime matrix; MediaPipe 0.10.14 requires Python
  3.12 on the tested Windows environment.
- The pinned Next.js 14.2.35/PostCSS tree currently reports high/critical
  production advisories.
- No deterministic end-to-end test covers voice intake through clinician
  synthesis, and the former simulation endpoint/UI was removed in PR #21.

## Team ownership

| Teammate | Lane | Issues | Conflict boundary |
| --- | --- | --- | --- |
| Presentation teammate | Pitch, screenshots, demo narrative | [#28](https://github.com/JiyaS12/WalkingOnSunshine/issues/28) | Presentation assets and narrative; no product code unless coordinated. |
| Voice teammate A | Outbound calls and structured intake | [#26](https://github.com/JiyaS12/WalkingOnSunshine/issues/26) | Voice-provider call lifecycle and survey adapter. |
| Voice teammate B | SMS and spoken test guidance | [#27](https://github.com/JiyaS12/WalkingOnSunshine/issues/27) | SMS/provider callbacks and guidance state machine. |
| Implementation owner | Patient access/UX, clinician security, platform, consolidation | [#22](https://github.com/JiyaS12/WalkingOnSunshine/issues/22), [#23](https://github.com/JiyaS12/WalkingOnSunshine/issues/23), [#24](https://github.com/JiyaS12/WalkingOnSunshine/issues/24), [#25](https://github.com/JiyaS12/WalkingOnSunshine/issues/25), [#29](https://github.com/JiyaS12/WalkingOnSunshine/issues/29) | Coordinates separate Codex worktrees/PRs, reviews contracts, and owns final integration. |

## New issue inventory

### Implementation owner

1. [#22 Add expiring signed patient links and scoped patient APIs](https://github.com/JiyaS12/WalkingOnSunshine/issues/22) — P0 foundation; starts immediately.
2. [#23 Secure and complete the `/patient/[id]` walking-test flow](https://github.com/JiyaS12/WalkingOnSunshine/issues/23) — P0; hardening starts against the merged PR #21 route, final integration follows #22.
3. [#24 Protect clinician data and replace wildcard runtime trust](https://github.com/JiyaS12/WalkingOnSunshine/issues/24) — P0; independent start.
4. [#25 Upgrade vulnerable frontend dependencies and add clean CI](https://github.com/JiyaS12/WalkingOnSunshine/issues/25) — P0; independent start.
5. [#29 Validate and consolidate the complete voice-to-clinician workflow](https://github.com/JiyaS12/WalkingOnSunshine/issues/29) — P0 release gate; final convergence.

### Voice and presentation teammates

1. [#26 Implement outbound calls and structured survey ingestion](https://github.com/JiyaS12/WalkingOnSunshine/issues/26) — Voice A, P0; starts immediately.
2. [#27 Send the secure patient link and guide the walking test by voice](https://github.com/JiyaS12/WalkingOnSunshine/issues/27) — Voice B, P0; provider/state-machine work starts immediately, link integration follows #22/#23.
3. [#28 Produce the architecture-aligned pitch and live-demo narrative](https://github.com/JiyaS12/WalkingOnSunshine/issues/28) — Presentation, P1; starts immediately and refreshes assets after #29.

## Dependency graph

```text
                                      ┌─────────────── #26 voice intake ────────┐
#22 signed patient contract ──┬──────> #27 SMS + voice guidance ────────────────┤
                              ├──────> #23 patient route hardening ──────────────┤
                              │                                                 │
#24 clinician security ───────┼─────────────────────────────────────────────────┤
#25 dependency upgrade + CI ──┼─────────────────────────────────────────────────┼──> #29 release validation
                              │                                                 │
#28 presentation draft ───────┴─────────────────────────────────────────────────┘
                                                                                │
                                                                                └──> #28 final assets
```

The arrows mark integration dependencies, not idle time. #23 hardens the merged
route while #22 publishes its final interface, and #27 builds its provider
adapter/state machine before the final link is available.

## Optimal execution waves

### Wave 0 — Baseline and reset (complete)

- Pull the latest architecture and implementation from `origin/main`.
- Close superseded issues #4-#15 and replace the old plan.
- Validate and merge PR #21, which supplies the base patient route and live
  clinician sync.
- Establish the passing baseline and record runtime/dependency risks.

Exit evidence: 62 backend tests pass on Python 3.12; frontend lint/build pass;
new issues #22-#29 contain testable acceptance criteria.

### Wave 1 — Four human lanes plus parallel Codex worktrees

- Presentation teammate: draft #28 from the architecture and seeded data.
- Voice teammate A: implement #26 call/intake lifecycle.
- Voice teammate B: implement #27 provider/SMS/guidance foundations against a
  mocked `patient_url`.
- Implementation owner: dispatch separate GPT-5.6 Sol xhigh Codex tasks for
  #22, #24, and #25. A separate #23 task hardens the merged patient route, but
  its final contract integration waits for #22.

Exit condition: contract examples exist; security and CI branches are green;
voice adapters run against fakes; the patient route handles authenticated and
invalid-link fixture states without exposing clinician/cohort surfaces.

### Wave 2 — Merge foundations, then close the patient handshake

Preferred merge order:

1. #22 signed-link/backend contract.
2. #25 dependency/runtime/CI foundation, rebased over #22 if its checks cover
   new tests.
3. #24 clinician boundary, rebased over the latest frontend/runtime changes.
4. #23 patient-route hardening, updated to the merged signed-link contract.

#26 may merge at any point after its survey contract checks pass. #27 rebases on
#22/#23 to replace its mocked URL/page events with the real handshake.

Exit condition: a fake voice intake receives a real expiring link, and that link
can load/save only its patient while clinician endpoints reject patient tokens.

### Wave 3 — Voice integration and stabilization

- Merge #26 and #27 after replay/idempotency and redaction tests pass.
- Run the full backend suite and frontend clean install/lint/build after every
  merge.
- Add deterministic provider/camera/OpenAI fallbacks without changing the
  published data contract.

Exit condition: the complete flow runs locally without external credentials and
the live provider path has a documented smoke procedure.

### Wave 4 — Release convergence

- #29 owns end-to-end validation, documentation reconciliation, security checks,
  and the demo-machine runbook.
- Presentation teammate refreshes #28 screenshots and claims only after the
  release candidate passes.
- No new features enter this wave unless they fix a failed acceptance criterion.

Exit condition: all issues are closed, CI and clean-checkout tests pass, the
deterministic and live demo paths are documented, and architecture status is
accurate.

## Codex task and pull-request protocol

- One fresh Codex task/worktree and one branch per implementation issue.
- Use GPT-5.6 Sol with `xhigh` reasoning for each dispatched issue.
- Branch names use `codex/<issue-number>-short-description` where controllable.
- Every PR includes `Closes #N`, the acceptance checklist, test commands/results,
  configuration changes, and known external prerequisites.
- Agents do not merge their own PRs unless explicitly assigned integration; the
  implementation owner reviews scope, tests, conflicts, and security boundaries.
- Never commit credentials, patient phone numbers, generated caches, `.env`, or
  `PROJECT_LOG.md`.

## Synchronization and push safety

Before every push or merge:

1. fetch `origin` and inspect changes to `main` plus open PRs;
2. rebase/merge the latest `main` into the issue branch without discarding
   teammate work;
3. inspect overlapping files and reconcile the shared contract deliberately;
4. rerun the affected backend/frontend checks; and
5. record the decision and evidence in the ignored local `PROJECT_LOG.md`.

## Definition of done

- Issues #22-#29 are closed through merged work or an explicit, documented scope
  decision from the team.
- The end-to-end patient identity is consistent from call through synthesis.
- Signed patient links are scoped and expiring; clinician data is authenticated;
  CORS and secrets are configured safely.
- Backend tests, frontend lint/type/build, production dependency audit, contract
  tests, and deterministic end-to-end smoke tests pass in CI and locally.
- Live camera and configured voice/SMS paths are smoke-tested on the demo machine.
- The fallback demo works without voice credentials, camera access, network, or
  an OpenAI key.
- `docs/architecture.md`, README/environment docs, and the presentation describe
  the system that actually ships.
