You are implementing the following application:

* Idea: @${IDEA_FILE}
* Specification: @${SPEC_FILE}
* Implementation tasks: @${PLAN_WITHOUT_STORIES_FILE}

Your task:

${SPECIFIC_TASK}

If ${SPECIFIC_TASK} is empty or not provided:
  - Select the next incomplete task from the plan file (a task with a `[ ]` checkbox).
  - Execute tasks sequentially, one at a time, until no incomplete tasks remain.

Follow the active TDD, plan-tracking, plan-file-management, and review-before-complete skills.

For every task, before you implement it: ground your approach in the ratified architecture — read the governing ADR/spec/standards for any cross-cutting concern it touches; if the plan or an obvious implementation contradicts a ratified ADR, STOP and reclassify rather than coding blind (review-before-complete, Gate 1).

Before you mark a task complete: run the review-before-complete skill (Gate 2) — a passing test suite you wrote yourself is necessary but NOT sufficient; an independent fresh-context reviewer must hunt the failure modes your tests miss. Fix any real Critical/Important finding and re-verify first.

Do not work on future tasks until the current one is complete.
If all tasks are complete, stop and report success.
