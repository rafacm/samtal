#!/bin/sh
# Self-posting PR review: runs codex against a PR's diff from inside
# the PR's worktree, then posts the result to the PR as a comment with
# a provenance header, so the review lands even if the driving session
# dies between the review finishing and the comment being posted.
#
# Usage:
#   run-pr-review.sh <worktree> <base-ref> <pr-number> "<pr-title>" "<context sentence>"
#
# Writes its working files (diff, prompt, output, posted comment) next
# to nothing in the repository: they go to $TMPDIR (or /tmp).
set -eu
WORKTREE="$1"; BASE="$2"; PR="$3"; TITLE="$4"; CONTEXT="$5"
SKILL_DIR="$(cd "$(dirname "$0")" && pwd)"
WORK="${TMPDIR:-/tmp}/external-review-pr-$PR"
mkdir -p "$WORK"
DIFF="$WORK/diff.txt"
PROMPT="$WORK/prompt.md"
OUT="$WORK/out.txt"
ERR="$WORK/err.txt"

cd "$WORKTREE"
git diff "$BASE"...HEAD > "$DIFF"

# Placeholder substitution via python: the title and context are prose
# and sed's replacement syntax would mangle the characters prose uses.
python3 - "$SKILL_DIR/pr-review-prompt.md" "$PROMPT" \
  "$PR" "$TITLE" "$CONTEXT" "$DIFF" "$BASE" <<'PY'
import sys
template, out, pr, title, context, diff, base = sys.argv[1:8]
text = open(template).read()
for key, value in [("__PR_NUMBER__", pr), ("__PR_TITLE__", title),
                   ("__CONTEXT__", context), ("__DIFF_FILE__", diff),
                   ("__BASE__", base)]:
    text = text.replace(key, value)
open(out, "w").write(text)
PY

codex exec -m gpt-5.6-sol --sandbox read-only - < "$PROMPT" > "$OUT" 2> "$ERR"

HEAD_SHA="$(git rev-parse --short HEAD)"
{
  printf '## External review round\n\n'
  printf 'Automated external review of this PR'"'"'s diff (%s...%s): codex CLI %s, model gpt-5.6-sol, read-only, %s. Posted verbatim by the review run itself; resolutions follow as replies.\n\n' \
    "$BASE" "$HEAD_SHA" "$(codex --version | sed 's/codex-cli //')" "$(date -u +%Y-%m-%d)"
  printf -- '---\n\n'
  cat "$OUT"
} > "$WORK/comment.md"

gh pr comment "$PR" --repo rafacm/samtal --body-file "$WORK/comment.md"
echo "posted review to PR #$PR (working files in $WORK)"
