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

### 5. Commit

- Stage specific files (not `git add .`)
- Write a descriptive commit message using HEREDOC format
- End with `Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>`

```
git add <files>
git commit -m "$(cat <<'EOF'
<type>: <description>

<body>

Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>
EOF
)"
```

### 6. Code review

Run `/engineering:code-review` on the local diff against main:

```
git diff main...HEAD
```

- If the verdict is **Approve** with no critical issues, proceed to step 8
- If the verdict is **Request Changes** or has critical issues, proceed to step 7

### 7. Address code review findings

For each critical issue or actionable suggestion from the review:

1. Implement the fix
2. Re-run `hatch fmt` and `hatch test`
3. Amend the commit: `git commit --amend --no-edit`
4. Re-run `/engineering:code-review` on the updated diff

Repeat until the verdict is **Approve**.

### 8. Push

```
git push -u origin <branch-name>
```

### 9. Create PR

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

Note the PR number from the output — it is needed for the merge step.

### 10. **[GATE]** Merge and cleanup

Use `AskUserQuestion` to ask: "PR #N is ready. Squash-merge?" with options "Yes — merge" and "No — hold off".

If confirmed:

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
