#!/bin/sh
# Self-posting PR review: runs the review backend against a PR's diff
# from inside the PR's worktree, then posts the result as a comment with
# a provenance header, so the review lands even if the driving session
# dies between the review finishing and the comment being posted.
#
# Usage:
#   run-pr-review.sh <worktree> <base-ref> <pr-number> "<pr-title>" "<context sentence>"
#
# REVIEW_BACKEND selects the reviewer CLI: codex (default) or claude,
# the fallback for when the codex quota is exhausted. REVIEW_MODEL
# selects the model within the backend (default gpt-5.6-sol for codex,
# claude-opus-5 for claude). The tiering rule lives in SKILL.md: sol
# for plans and behavior-changing milestone PRs, terra for low-stakes
# rounds.
#
# Writes its working files (diff, prompt, output, posted comment) next
# to nothing in the repository: they go to $TMPDIR (or /tmp).
set -eu
WORKTREE="$1"; BASE="$2"; PR="$3"; TITLE="$4"; CONTEXT="$5"
BACKEND="${REVIEW_BACKEND:-codex}"
case "$BACKEND" in
  codex)  MODEL="${REVIEW_MODEL:-gpt-5.6-sol}" ;;
  claude) MODEL="${REVIEW_MODEL:-claude-opus-5}" ;;
  *) echo "unknown REVIEW_BACKEND: $BACKEND (codex or claude)" >&2; exit 2 ;;
esac
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

case "$BACKEND" in
  codex)
    codex exec -m "$MODEL" --sandbox read-only - < "$PROMPT" > "$OUT" 2> "$ERR"
    CLI_STAMP="codex CLI $(codex --version | sed 's/codex-cli //'), read-only sandbox"
    ;;
  claude)
    # --allowedTools alone restricts nothing: it adds allow rules and
    # every unlisted tool stays available. The deny list is the fence,
    # and --setting-sources ""/--strict-mcp-config keep local settings
    # and MCP servers from widening it back.
    claude -p --model "$MODEL" \
      --strict-mcp-config --setting-sources "" \
      --allowedTools "Read,Glob,Grep" \
      --disallowedTools "Bash,Write,Edit,NotebookEdit,WebFetch,WebSearch,Agent,Task,Workflow,Skill,SendMessage,CronCreate,CronDelete,RemoteTrigger,PushNotification,ScheduleWakeup,EnterWorktree,ExitWorktree,DesignSync,Monitor,LSP,ToolSearch" \
      < "$PROMPT" > "$OUT" 2> "$ERR"
    CLI_STAMP="claude CLI $(claude --version | sed 's/ (Claude Code)//'), read-only tool set"
    ;;
esac

HEAD_SHA="$(git rev-parse --short HEAD)"
{
  printf '## External review round\n\n'
  printf 'Automated external review of this PR'"'"'s diff (%s...%s): %s, model %s, %s. Posted verbatim by the review run itself; resolutions follow as replies.\n\n' \
    "$BASE" "$HEAD_SHA" "$CLI_STAMP" "$MODEL" "$(date -u +%Y-%m-%d)"
  printf -- '---\n\n'
  cat "$OUT"
} > "$WORK/comment.md"

gh pr comment "$PR" --repo rafacm/vinga --body-file "$WORK/comment.md"
echo "posted review to PR #$PR (working files in $WORK)"
