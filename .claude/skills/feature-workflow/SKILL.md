---
name: feature-workflow
description: Complete feature branch workflow — branch, implement, validate, PR, address Devin review, merge.
---

# Feature Workflow

Implement a feature from branch creation through to merged PR and clean main HEAD.

## Arguments

The user may provide a branch name or feature description as arguments. If not provided, ask.

## Process

Follow these steps in order. Use the todo list to track progress. Ask the user before proceeding at gates marked **[GATE]**.

### 1. Create feature branch

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

### 4. Commit and push

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

### 5. Create PR

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

### 6. Poll for Devin review

After creating the PR, use CronCreate to set up a recurring poll that checks for Devin review comments every ~5 minutes. The cron job fires while the session is idle and notifies you when comments arrive.

Create the cron job:
- Cron expression: `*/5 * * * *` (every 5 minutes)
- Prompt should check for Devin comments on the PR and report findings

The poll prompt should run these checks:

```
gh api repos/{owner}/{repo}/pulls/{N}/comments --jq '[.[] | select(.body | test("devin-review-comment"))] | length'
```

If the count is greater than 0, fetch the full comments:

```
gh api repos/{owner}/{repo}/pulls/{N}/comments --jq '.[] | select(.body | test("devin-review-comment")) | {id: .id, path: .path, body: .body[0:200]}'
```

Also check the review summary:

```
gh pr view {N} --json reviews --jq '.reviews[] | {body: .body[0:200], state: .state}'
```

When comments are found, report them to the user with a summary of each finding and whether it's actionable.

When no comments are found yet, report "No Devin review yet on PR #N."

### 7. Address Devin feedback

When Devin comments arrive (detected by the cron poll or reported by the user):

1. **Delete the polling cron job** using CronDelete — it's no longer needed for this phase
2. Read each Devin comment in full to understand the issue
3. Implement the fix
4. Let hooks run (fmt + test)
5. Commit with a message referencing Devin's finding
6. Push to the branch
7. Reply to the Devin comment explaining what was fixed:

```
gh api repos/{owner}/{repo}/pulls/{N}/comments/{comment_id}/replies -f body="<response>"
```

If a comment is about code that doesn't belong in this PR (e.g. leaked changes), explain that in the reply.

### 8. Poll for Devin re-scan

After pushing fixes, create a new CronCreate poll (same pattern as step 6) to wait for Devin's re-scan. Look for **new** comments that appeared after the fix push.

When the re-scan arrives:
- If new actionable issues: delete the cron, go back to step 7
- If no new issues (or only "Resolved" confirmations): delete the cron, proceed to step 9

### 9. **[GATE]** Merge and cleanup

Ask the user to confirm merge, then:

```
gh pr merge {N} --squash --delete-branch
git checkout main
git fetch --prune
git pull
```

Confirm clean state with `git status`.

## Error handling

- If `hatch test` fails, fix the issue and re-run before committing
- If `gh pr merge` fails due to conflicts, rebase: `git rebase origin/main`, resolve conflicts, force-push with `--force-with-lease`
- If a Devin comment refers to leaked changes from another branch, clean the branch with `git reset --hard main && git cherry-pick <correct-commits>`, force-push with `--force-with-lease`
