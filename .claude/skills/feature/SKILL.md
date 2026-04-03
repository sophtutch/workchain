---
name: feature
description: Plan, track, and ship multi-PR features — plan, list, status, next.
---

# Feature Management

Plan features, break them into sequenced tasks, track progress on disk, and ship tasks one at a time via `/ship`.

## Arguments

First argument is the subcommand. Remaining arguments depend on the subcommand.

| Command | Usage | Description |
|---------|-------|-------------|
| `plan` | `/feature plan <name>` | Create a new feature and break into tasks |
| `list` | `/feature list` | Show all features and progress |
| `status` | `/feature status <name>` | Show a feature's tasks with status |
| `next` | `/feature next <name>` | Ship the next pending task |

If no arguments are provided, show this help table and ask what the user wants to do.

## Storage

Features are stored as markdown files in `.claude/features/<name>.md`, git-tracked. Format:

```markdown
---
name: <feature-name>
created: <ISO date>
status: planning | in_progress | completed
---

# <Feature Title>

## Description
<What this feature does and why>

## Tasks

- [x] task-1: Description of first task
  - branch: `refactor/explicit-store-methods`
  - pr: #24
- [x] task-2: Description of second task
  - branch: `refactor/audit-logging-in-store`
  - pr: #25
- [ ] task-3: Description of third task
```

Task format:
- `[ ]` — pending
- `[-]` — in progress (currently being shipped)
- `[x]` — completed (with branch and PR metadata indented below)

---

## Subcommands

### plan

`/feature plan [name]`

1. Ask the user to describe the feature and its goals
2. If no name was provided, derive a short kebab-case name from the feature description (e.g. "Replace dict params with StepResult" becomes `store-typed-params`)
3. Explore the codebase to understand what files and systems are involved
3. Break the feature into sequenced tasks — each task should be one PR's worth of work
4. Write `.claude/features/<name>.md` with all tasks marked `[ ]`
5. Present the task list to the user for review
6. Adjust tasks based on user feedback (add, remove, reorder, split, merge)
7. Set feature status to `in_progress` once the user approves

Guidelines for task breakdown:
- Each task should be independently shippable (tests pass after each)
- Order tasks so later ones build on earlier ones
- Prefer small, focused tasks over large ones
- Name tasks with a short kebab-case identifier (used as branch name suffix)

### list

`/feature list`

1. Read all `.md` files in `.claude/features/` and `.claude/features/completed/`
2. Parse the frontmatter and task checkboxes from each
3. Display a table:

```
Feature              Status        Progress   Created
store-typed-params   in_progress   2/5        2026-04-03
audit-refactor       completed     5/5        2026-04-02
```

If no features exist, say "No features found. Use `/feature plan <name>` to create one."

After displaying the table, use `AskUserQuestion` to let the user select a feature to work on. List all non-completed features as options with their description as the option description. Include a "None — just browsing" option. If the user selects a feature, proceed to `next` for that feature.

### status

`/feature status <name>`

1. Read `.claude/features/<name>.md` (or `.claude/features/completed/<name>.md`)
2. Display the description and all tasks with their status:

```
# store-typed-params (in_progress)

Replace dict params with StepResult in store methods.

Tasks:
  [x] task-1: Replace result dict in complete_step (PR #26)
  [x] task-2: Replace result dict in fail_step (PR #27)
  [-] task-3: Replace result dict in block_step
  [ ] task-4: Remove result_summary audit params
  [ ] task-5: Update CLAUDE.md
```

If the feature file doesn't exist, say so and suggest `/feature plan <name>`.

### next

`/feature next <name>`

1. Read `.claude/features/<name>.md`
2. Find the first pending task (`[ ]`)
3. If no pending tasks remain, mark the feature as `completed` and report
4. Mark the task as in progress (`[-]`) and save the file
5. Derive a branch name: `<feature-name>/<task-id>` (e.g. `store-typed-params/replace-complete-step-dict`)
6. Tell the user what task is being shipped and invoke `/ship <branch-name>`
7. After `/ship` completes (PR merged and on clean main):
   - Read the feature file again (it may have been modified)
   - Mark the task as `[x]`
   - Add branch and PR metadata below the task
   - If all tasks are now complete:
     - Set feature status to `completed` in frontmatter
     - Add `completed: <ISO date>` to frontmatter
     - Add a `## PRs` section listing all task PRs
     - Move the file from `.claude/features/<name>.md` to `.claude/features/completed/<name>.md`
     - Report: "Feature <name> complete! Moved to completed/"
   - Otherwise save the file and report progress: "Task 3/5 complete. Next: `/feature next <name>`"

If `/ship` is interrupted or fails, leave the task as `[-]` so the user can resume with `/feature next <name>` (it will pick up the same task).

## Error handling

- If `.claude/features/` doesn't exist, create it
- If `.claude/features/completed/` doesn't exist, create it when first needed
- If a feature file doesn't exist for `status` or `next`, report and suggest `plan`
- If `next` is called on a completed feature, check `.claude/features/completed/<name>.md` and report that all tasks are done
- If a task is already `[-]` (in progress), `next` picks it up (resume, don't skip)
