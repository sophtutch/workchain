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
| `plan` | `/feature plan [name]` | Create a new feature and break into tasks |
| `list` | `/feature list` | Show open features and progress |
| `completed` | `/feature completed` | Show completed features |
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
2. Explore the codebase to understand what files and systems are involved
3. Assess scope — if the work naturally splits into independent areas (e.g. separate models, separate subsystems, separate concerns), create multiple feature files rather than one large one. Each feature should be a cohesive unit that can be planned, tracked, and completed independently.
4. For each feature:
   a. If no name was provided (or multiple features are being created), derive a short kebab-case name from the feature description
   b. Break the feature into sequenced tasks — each task should be one PR's worth of work
   c. Write `.claude/features/<name>.md` with all tasks marked `[ ]`
5. Present all feature(s) and their tasks to the user for review
6. Adjust based on user feedback (add, remove, reorder, split, merge features or tasks)
7. Set feature status to `in_progress` once the user approves

Guidelines for decomposition:
- A single feature should have 1-5 tasks. If you have more, consider splitting into multiple features.
- Each task should be independently shippable (tests pass after each)
- Order tasks so later ones build on earlier ones
- Prefer small, focused tasks over large ones
- Name tasks with a short kebab-case identifier (used as branch name suffix)
- Features can depend on each other — note dependencies in the description if so

### list

`/feature list`

1. Read all `.md` files in `.claude/features/` (open features only, not completed/)
2. Parse the frontmatter and task checkboxes from each
3. Display a table:

```
Feature                      Status        Progress   Created
typed-result-complete-step   in_progress   0/1        2026-04-04
typed-result-fail-step       in_progress   0/1        2026-04-04
```

If no open features exist, say "No open features. Use `/feature plan` to create one, or `/feature completed` to see completed features."

After displaying the table, use `AskUserQuestion` to let the user select a feature to work on. List all features as options with their description as the option description. If the user selects a feature, proceed to `next` for that feature.

### completed

`/feature completed`

1. Read all `.md` files in `.claude/features/completed/`
2. Parse the frontmatter from each
3. Display a table:

```
Feature          Progress   Created      Completed
audit-refactor   5/5        2026-04-02   2026-04-03
```

If no completed features exist, say "No completed features yet."

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
