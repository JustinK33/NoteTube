---
name: commit-message
description: Inspect git changes and generate clear, conventional commit messages
---

Your task is to generate a high-quality git commit message for the current repository changes.

Workflow:

1. Run:
   - git status --short
   - git diff --staged --stat
   - git diff --staged

2. If there are no staged changes, run:
   - git diff --stat
   - git diff

3. Determine:
   - primary purpose of the change
   - affected area of the codebase
   - whether the change is best classified as feat, fix, refactor, perf, docs, test, chore, ci, build, or style

4. Write the best commit message in conventional commit style:
   <type>: <summary>

5. Optionally include a body only when the change spans multiple meaningful updates.

6. Prioritize messages that describe intent, not just file edits.

Formatting constraints:
- imperative mood
- concise but specific
- no trailing period
- target under 72 characters
- avoid generic words like "stuff", "things", "changes", "update"

Response format:
1. Best message
2. Alternatives
3. Why the best one fits

Example response:

```text
feat: add redis caching for user session queries