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

Make code changes as directed by the user. Hooks in settings.json will auto-run `hatch fmt` and `hatch test` after each Python file edit — do not run these manually unless diagnosing a failure.

### 3. Final validation

Run the full validation suite explicitly before committing:

```
hatch fmt
hatch test
```

Fix any issues until both pass clean. **Do not proceed until tests pass.**

Skip this step if only non-Python files changed (e.g. docs, CLAUDE.md, README.md).

### 4. Run `/simplify`

Invoke `/simplify` to review all changed code for reuse, quality, and efficiency. This is a local analysis that may fix issues automatically.

- If `/simplify` makes changes, re-run `hatch fmt` and `hatch test` to confirm the changes are clean
- If nothing found, proceed

### 5. Run `/review-pr`

Invoke `/review-pr all` to run all 6 local review agents (code-reviewer, code-simplifier, comment-analyzer, pr-test-analyzer, silent-failure-hunter, type-design-analyzer). This is a **local analysis only** — it does not post to GitHub or require a PR to exist.

- If critical issues are found: fix them, re-run `hatch fmt` and `hatch test`, then proceed
- If clean or only minor/suggestion-level findings: proceed

### 6. Commit and push

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

### 7. Create PR

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

### 8. Poll for review comments

Set up a recurring poll using CronCreate. See [Review comment polling pattern](#review-comment-polling-pattern) below for the poll prompt template.

- Cron expression: `* * * * *` (every 1 minute)
- Set `BASELINE_COMMENT_COUNT=0` (no review comments yet)
- If no review arrives after 5 polls, delete the cron, report to the user, and ask whether to continue waiting or proceed without review

When the poll detects new comments, proceed to step 9.

### 9. Address reviewer feedback

When review comments arrive (detected by the cron poll or reported by the user):

1. **Delete the polling cron job** using CronDelete
2. Read each review comment in full to understand the issue
3. Implement the fix
4. Run `hatch fmt` and `hatch test` explicitly to verify the fix (hooks may not trigger for all edit patterns)
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

### 10. Poll for reviewer re-scan

After pushing fixes, note the current review comment count and create a new CronCreate poll. See [Review comment polling pattern](#review-comment-polling-pattern) below — set `BASELINE_COMMENT_COUNT` to the current count so only **new** comments trigger an alert.

When the re-scan arrives:
- If new actionable issues: delete the cron, go back to step 9
- If no new issues on the **first poll** (count == baseline): delete the cron immediately and proceed to step 11 — do not wait for 5 polls on re-scans

### 11. **[GATE]** Merge and cleanup

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

## Review comment polling pattern

Reusable poll prompt template for CronCreate. Replace `{N}` with the PR number, `$REPO` with the repo name, and `{BASELINE}` with the review comment count before this poll phase started (0 for initial, or the count after addressing feedback).

```
Check for review comments on PR #{N} in $REPO.

Run: gh api repos/$REPO/pulls/{N}/comments --jq 'length'

If the count is greater than {BASELINE}, fetch the new comments:
gh api repos/$REPO/pulls/{N}/comments --jq '.[] | {id: .id, path: .path, body: .body[0:200], user: .user.login}'

Also check for formal PR reviews:
gh pr view {N} --json reviews --jq '.reviews[] | {body: .body[0:200], state: .state, author: .author.login}'

If count > {BASELINE}, report the new findings with a summary of each.
If count == {BASELINE}, say "No new review comments on PR #{N}."
```

## Error handling

- If `hatch test` fails, fix the issue and re-run before committing
- If `gh pr merge` fails due to conflicts, rebase: `git rebase origin/main`, resolve conflicts, force-push with `--force-with-lease`
- If a review comment refers to leaked changes from another branch, clean the branch with `git reset --hard main && git cherry-pick <correct-commits>`, force-push with `--force-with-lease`
