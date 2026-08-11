You are reviewing a pull request's diff, read-only, in a checkout of the samtal repository with the PR's branch checked out. You have no network access; everything you need is in this prompt or the repository files.

## The pull request

PR #__PR_NUMBER__: __PR_TITLE__

Context: __CONTEXT__. Read the governing plan in full, including any review-round sections whose resolutions amend the design; the plan is the spec this PR is held against. The companion implementation doc records what the milestone actually did and any recorded deviations. The issue's decisions are settled; do not re-litigate them.

The diff under review is in `__DIFF_FILE__` (git diff __BASE__...HEAD). Read it in full. Read any file the diff touches whenever the hunk alone is not enough to judge it; read neighboring code the diff should have changed but did not.

## What to review for

- Correctness bugs in the changed code, including concurrency (the database serializes on BEGIN IMMEDIATE with a 10 s busy timeout), error mapping, and boot order.
- Violations of the plan: a deliverable the milestone claims but the diff does not contain, or a design the plan's review round settled that the diff contradicts.
- The no-leak contract: no secret, no rejected input value, no library traceback may reach stdout, stderr, a log record, an HTTP response body or header, or an exception chain, on any path the diff adds or touches. This is the highest-priority class of finding.
- Semantics restated outside the repository (`config/store.py` is the semantics layer; handlers and CLI are transport).
- Tests that would pass with the behavior they claim to pin removed, missing refusal cases, and assertions weaker than the claim in the implementation doc.
- CI honesty: workflow changes that cannot work as written, drift checks that do not check what they claim.
- Message and documentation drift: CLI message text is contract (operators meet one vocabulary); generated documents must match their generators.

## Output format

Number every finding. For each: a priority (P1 must fix before merge, P2 should fix before merge, P3 worth noting), a one-line title, the evidence (file and line in the diff or the tree), and the concrete fix. Report only findings, do not restate the diff. End with a verdict: mergeable as is, mergeable after the listed fixes, or not mergeable. Be adversarial; a finding that survives scrutiny is worth ten observations.
