# Benchmark task prompts

A small, fixed set of tasks to run Claude Code against, so traces are
comparable across runs and models. Mirrors the task categories Caeden suggested:
a coding bug fix, a file-search/refactor, and a data task.

Run one with:

```bash
scripts/profile_task.sh --prompt-file tasks/01-bugfix.md
```

Then compare the generated reports under `reports/`. The interesting axes:

- **explore vs. execute phase shift** — does read/explore really front-load?
- **tool mix** — how Bash-heavy vs. Edit-heavy is each task type?
- **cache-read share** — how decode-dominated is the workload?
- **retries/errors** — where do loops happen, and on which tools?
