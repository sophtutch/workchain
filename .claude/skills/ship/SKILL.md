---
name: ship
description: Ship a feature — branch, implement, validate, review, PR, merge.
---

# Feature Workflow

Implement a feature from branch creation through to merged PR and clean main HEAD.

## Prerequisites

The following CLI tools must be installed and authenticated:

- **git** — version control
- **gh** — GitHub CLI, authenticated via `gh auth login`
- **hatch** — Python project manager (runs `hatch fmt` and `hatch test`)

## Arguments

The user may provide a branch name or feature description as arguments. If not provided, ask.

## Process

Follow these steps in order. Use the todo list to track progress. Ask the user before proceeding at gates marked **[GATE]**.

### 1. Create feature branch

Derive the repo owner/name for later use:

```
REPO=$(gh repo view --json nameWithOwner -q .nameWithOwner)
```

Create the branch:

```
git checkout main && git pull origin main
git checkout -b <branch-name>
```

Branch naming: `feature/<description>`, `fix/<description>`, or `docs/<description>`.

### 2. Implement changes

Make all code changes first. Do **not** run `hatch fmt` or `hatch test` until the full implementation pass is complete — running the linter mid-implementation will damage intermediate states (e.g. removing an import that hasn't been used yet). Validation happens in the next step.

### 3. Final validation

Run the full validation suite explicitly before committing:

```
hatch fmt
hatch test
```

Fix any issues until both pass clean. **Do not proceed until tests pass.**

Skip this step if only non-Python files changed (e.g. docs, CLAUDE.md, README.md).

### 4. Run `/simplify` (non-trivial changes only)

**Skip this step** if the change is:
- A pure rename (find-and-replace across files, no logic changes)
- Doc-only (markdown, comments, README updates)
- Under ~20 lines of new logic (small validators, single-function additions)

Otherwise, invoke `/simplify` to review changed code for reuse, quality, and efficiency.

- If `/simplify` makes changes, re-run `hatch fmt` and `hatch test` to confirm the changes are clean
- If nothing found, proceed

### 5. Commit and push

- Stage specific files (not `git add .`)
- Write a descriptive commit message using HEREDOC format
- End with `Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>`
- Push with `-u` to set upstream

```
git add <files>
git commit -m "$(cat <<'EOF'
<type>: <description>

<body>

Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>
EOF
)"
git push -u origin <branch-name>
```

### 6. Create PR

```
gh pr create --base main --title "<title>" --body "$(cat <<'EOF'
## Summary
<bullets>

## Test plan
<checklist>

Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

Note the PR number from the output — it is needed for all subsequent steps.

### 7. Poll for review comments and CI checks

Set up a recurring poll using CronCreate. See [Review and CI polling pattern](#review-and-ci-polling-pattern) below for the poll prompt template.

- Cron expression: `* * * * *` (every 1 minute)
- Set `BASELINE_COMMENT_COUNT=0` (no review comments yet)
- The poll checks both review comments **and** CI check status
- **Short-circuit on green CI**: if all CI checks pass and there are no review comments, **immediately** delete the cron and proceed to the merge gate (step 10) — do not wait for 5 polls
- If CI checks fail, report the failure immediately regardless of review status
- If CI is still pending and no review comments after 5 polls, delete the cron and use `AskUserQuestion` to ask: "CI still pending, no review comments after 5 minutes. How should we proceed?" with options: "Merge without review", "Keep waiting" (creates a new polling cron), and "Hold — I'll check back later"

When the poll detects new comments, proceed to step 8.

### 8. Address reviewer feedback

When review comments arrive (detected by the cron poll or reported by the user):

1. **Delete the polling cron job** using CronDelete
2. Read each review comment in full to understand the issue
3. Implement the fix
4. Run `hatch fmt` and `hatch test` to verify the fix
5. Commit with a message referencing the reviewer's finding using HEREDOC format
6. Push to the branch
7. Reply to the review comment. **Use single quotes** for the body to avoid bash backtick interpretation:

```
gh api repos/$REPO/pulls/{N}/comments/{comment_id}/replies \
  -f body='Fixed — description of what was changed.'
```

If a reply needs backticks, use `gh pr comment {N} --body '...'` instead.

8. Resolve the conversation thread after replying:

```
gh api graphql -f query='mutation { minimizeComment(input: {subjectId: "<comment_node_id>", classifier: RESOLVED}) { minimizedComment { isMinimized } } }'
```

If a comment is about code that doesn't belong in this PR (e.g. leaked changes), explain that in the reply.

### 9. Poll for reviewer re-scan

After pushing fixes, note the current review comment count and create a new CronCreate poll. See [Review and CI polling pattern](#review-and-ci-polling-pattern) below — set `BASELINE_COMMENT_COUNT` to the current count so only **new** comments trigger an alert.

When the re-scan arrives:
- If new actionable issues: delete the cron, go back to step 8
- If no new issues on the **first poll** (count == baseline): delete the cron immediately and proceed to step 10 — do not wait for 5 polls on re-scans

### 10. **[GATE]** Merge and cleanup

Use `AskUserQuestion` to ask: "PR #N is clean. Squash-merge?" with options "Yes — merge" and "No — hold off".

If confirmed:

```
gh pr merge {N} --squash --delete-branch
git checkout main
git fetch --prune
git pull
```

Confirm clean state with `git status`.

---

## Review and CI polling pattern

Reusable poll prompt template for CronCreate. Replace `{N}` with the PR number, `$REPO` with the repo name, and `{BASELINE}` with the review comment count before this poll phase started (0 for initial, or the count after addressing feedback).

```
Check PR #{N} in $REPO for review comments AND CI check status.

1. Review comments:
Run: gh api repos/$REPO/pulls/{N}/comments --jq 'length'
If count > {BASELINE}, fetch: gh api repos/$REPO/pulls/{N}/comments --jq '.[] | {id: .id, path: .path, body: .body[0:200], user: .user.login, node_id: .node_id}'
Also check formal reviews: gh pr view {N} --json reviews --jq '.reviews[] | {body: .body[0:200], state: .state, author: .author.login}'

2. CI checks:
Run: gh pr checks {N} --json name,state --jq '.[] | {name: .name, state: .state}'

Report:
- If new review comments (count > {BASELINE}): report them with a summary
- If all checks passed AND no new comments: report "All CI checks passed, no review comments on PR #{N}. Ready to merge."
- If all checks passed AND new comments: report both
- If any checks failed: report which ones failed
- If checks are still pending and no comments: say "CI checks still running, no review comments on PR #{N}."
```

## Error handling

- If `hatch test` fails, fix the issue and re-run before committing
- If `gh pr merge` fails due to conflicts, rebase: `git rebase origin/main`, resolve conflicts, force-push with `--force-with-lease`
- If a review comment refers to leaked changes from another branch, clean the branch with `git reset --hard main && git cherry-pick <correct-commits>`, force-push with `--force-with-lease`
