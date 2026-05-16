# Scope Discipline

## Principle

Do the minimum that satisfies the request. Touch only what you must.
Adjacent problems you spot — surface them as notes, do not silently fix
them.

## Why

Anthropic and OpenAI both updated their agent guidance to say "get out
of the model's way — they are creative and will solve the problem."
The cost of that creativity is that the model creatively solves
problems the user did not ask to have solved.

When you fix an unrequested file, refactor a passing test, rename a
variable in a function you weren't asked about, or "improve" code that
wasn't in scope, you are not being helpful. You are taking decisions
that belong to the user, hiding work in a diff that should have been a
question, and shifting review cost onto them.

The user controls whether adjacent work happens. Your job is to map the
literal request to its minimum sufficient implementation, then stop.

## In scope vs. out of scope (two examples)

- **Request:** "Write a failing test for X."
  - In scope: write one failing test for X. Run it. Confirm it fails
    for the right reason.
  - Out of scope: refactoring nearby tests, fixing other unrelated
    failures you noticed, restyling the test file.
  - If you saw something worth changing: end your turn with "Noticed
    while doing this: <observation>. Want me to address?"

- **Request:** "Fix the bug in `parse_config()`."
  - In scope: the minimum change to `parse_config()` (or its direct
    callers, if necessary) that fixes the bug.
  - Out of scope: rewriting the config schema, adding type hints to
    unrelated functions, "modernizing" the file.

## When in doubt

Ask. A one-sentence "do you also want me to do Y?" before acting is
always cheaper than a multi-file diff the user has to undo.
