# Fixture Operating Rules

These rules are always-on context: every byte below is paid on every request of
every session that loads this bundle, whether or not a fixture is ever touched.
That is the point of the fixture — it is what the head-cost check exists to
measure, and it is deliberately the sort of prose that accretes in a real
bundle without anybody noticing the bill.

## Naming

Fixture files are named for the defect they carry, never for the test that
consumes them. A fixture named `test_case_3.md` tells a later reader nothing;
a fixture named `overlong-description.md` tells them exactly what it is for and
survives the test being renamed, split, or rewritten entirely.

## Placement

Fixtures live under `test-fixtures/`, which every scan in this repository
excludes by name. A fixture that lives anywhere else will be picked up by a
validator run against the repository root and will be reported as a real
defect, which wastes exactly as much of a reader's attention as a real defect
would.

## Content

A fixture violates one thing on purpose, or several things on purpose, and
nothing by accident. An accidental second violation makes every assertion in
the consuming test ambiguous: a count that moves might be the thing under test
or might be the accident, and the test cannot tell you which. Keep the
violations enumerated in the fixture's own README so a later reader can check
the fixture against its stated intent rather than inferring it.

## Lifecycle

A fixture outlives the change that motivated it. When the check it exercises is
removed, the fixture is deleted in the same commit — a fixture with no consumer
is stale content that still costs a reader the time it takes to work out
whether anything depends on it.

## Review

Every fixture is reviewed against the check it exercises, not against taste. A
fixture that no longer fails the check it was written for is not a fixture; it
is a passing file with a misleading name, and it will quietly hold a regression
open for as long as it stays that way.
