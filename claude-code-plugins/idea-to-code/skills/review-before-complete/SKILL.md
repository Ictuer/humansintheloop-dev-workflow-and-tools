---
name: review-before-complete
description: Mandatory quality gate before marking any plan task complete. Claude MUST use this skill at the end of every implementation task, before plan-file-management → mark-task-complete. It enforces architecture grounding and an independent adversarial review, because a green checkbox hiding a Critical bug is exactly the failure this gate exists to prevent.
---

# Review Before Complete

A passing test suite is necessary but **not sufficient** to mark a task complete. A task's tests are written by the same agent that implemented it, so they share its blind spots. Tasks have been ticked 100% complete while hiding Critical defects — dropped events, cross-tenant leaks, masked failures — that only an independent reviewer caught. This skill is the gate that stops that from happening again.

Run **both** gates below before `plan-file-management → mark-task-complete`. If either surfaces a real defect, fix it and re-verify before marking the task complete.

## Gate 1 — Architecture grounding (before you write code)

Before implementing anything that touches a cross-cutting concern — authorization, data model/storage, API/message contracts, security/secrets, resilience, observability, versioning, idempotency — read the governing design docs **first** (the spec, the relevant ADR, the project standards) and confirm your approach matches the ratified architecture.

- Do not infer a mechanism from memory or from "how it's usually done".
- If the plan's details, a review finding, or your intended implementation **contradicts a ratified ADR**, STOP. Do not implement. Reclassify the task, surface the conflict to the human, and record it. Example of the mistake this prevents: adding an in-service role check when an ADR designates an external authorization service as the single policy decision point, and the service is only meant to write authz facts, not decide them.

## Gate 2 — Independent adversarial review (before you mark complete)

Do **not** use your own judgement or your own tests as the completion gate. Launch a SEPARATE, fresh-context reviewer (the Task/Agent tool) to adversarially review the finished implementation. Give it the diff or changed files and the task's Observable/Evidence, and ask it to hunt — most-severe first — for the failure modes a task's own tests routinely miss:

- idempotency / replay / at-least-once duplicate handling
- out-of-order, partial, or late events; update-after-create
- cross-tenant / cross-workspace isolation
- silent error swallowing, inappropriate fallback, swallowed failures
- equivalence / golden / shadow drift against the legacy behaviour being replaced
- concurrency, ordering, off-by-one, pagination
- secrets or PII leaking into logs, read models, or responses

Require concrete findings (file:line, failure scenario, severity), not a verdict. Verify each finding against the code yourself before acting. Fix every real Critical/Important, re-run the tests, then mark the task complete. A finding that itself contradicts a ratified ADR is suspect — re-check its premise against Gate 1 before implementing its suggested fix.

## When to apply

Every implementation task, at its end, before `mark-task-complete`. Non-negotiable for any task touching the cross-cutting concerns listed in Gate 1.
