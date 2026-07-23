# Automation

Memory Forest can be checked and reindexed on a schedule, but the current CLI
does not ingest events, compact evidence, mark source records as processed, or
promote memories between layers.

> [!IMPORTANT]
> The examples in this guide automate deterministic validation and derived-index
> rebuilding only. A scheduler is not a promotion engine. Semantic capture and
> promotion require a separate, explicitly reviewed integration.

![Target operating model for automated Memory Forest maintenance](assets/memory-forest-automation.svg)

## What is implemented today

The CLI exposes these operations:

- `init`
- `doctor`
- `validate`
- `audit`
- `index`
- `route`
- `search`
- `retrieve`

For recurring maintenance, `index` is the primary operation. It validates the
forest, audits canonical parent links, builds a new private SQLite index in a
temporary file, and atomically replaces the previous derived index only after a
successful build.

```sh
memory-forest --json index /absolute/path/to/private-forest
```

Do not schedule `init`. It is a one-time operation that accepts only a new
target. Use `doctor`, `validate`, and `audit` interactively while setting up or
diagnosing a forest.

## Pick the scheduler that owns the job

| Scheduler | Best fit | Important boundary |
|---|---|---|
| POSIX cron | Portable, simple local schedules | Missed runs during sleep are not replayed |
| macOS launchd | A private forest on one Mac | Calendar runs missed during sleep are coalesced on wake |
| Codex Scheduled Task | Reviewable agent work in the desktop app | Local files require the Mac and app to remain available; task context crosses the selected account and model boundary |

Use one scheduler as the owner of a job. Do not install the same maintenance
job in cron, launchd, and Codex at the same time.

## Shared maintenance contract

Every scheduler should call the same bounded wrapper:

```sh
/bin/sh "/absolute/path/to/run-maintenance.sh" \
  "/absolute/path/to/private-forest"
```

The tracked example is
[`examples/automation/run-maintenance.sh`](../examples/automation/run-maintenance.sh).
It applies this contract:

1. Require one exact absolute forest root.
2. Reject a symlink root.
3. Acquire a sibling lock named `<forest>.maintenance.lock`.
4. Run `memory-forest --json index ROOT`.
5. Run `memory-forest --json doctor ROOT`.
6. Remove the lock on normal exit, failure, or a handled signal.

The lock is deliberately outside the forest. A lock directory inside
`.memory-forest` would be inspected as derived state and would make validation
fail while the job is running.

This lock prevents overlapping copies of the example wrapper. Every external
writer that changes canonical files must use the same lock. The wrapper cannot
make an unrelated writer safe by itself.

If a process crashes, the lock can remain. Verify that no writer or maintenance
job is still running before removing that exact sibling lock. The example fails
closed rather than guessing that a lock is stale.

## POSIX cron

Copy [`examples/automation/crontab.example`](../examples/automation/crontab.example)
and replace every `/absolute/path/to/...` placeholder.

Before installing it:

1. Put the real forest outside a public Git repository.
2. Install Memory Forest in a dedicated virtual environment.
3. Create the private log directory with mode `0700` and pre-create the log
   file with mode `0600`.
4. Resolve the CLI, wrapper, forest, and log paths to absolute paths.
5. Run the wrapper once manually and inspect its JSON output.

Install the edited entry with `crontab -e`, then inspect the active table with:

```sh
crontab -l
```

Cron has a minimal environment. The template sets `SHELL`, a bounded `PATH`,
and an absolute `MEMORY_FOREST_BIN`. Do not depend on an interactive shell
profile.

Cron command output can be mailed or written to a log. Memory Forest JSON
includes the absolute forest root, and error output can reveal route metadata,
so logs and scheduler mail must be treated as private. The example disables
mail and writes to an operator-selected private log.

Avoid wall-clock times near daylight-saving transitions. On systems that sleep,
cron does not replay a run that was missed while the machine was asleep. See
`man 5 crontab` on the target machine for its exact behavior. Also remember
that an unescaped percent sign has special meaning in a crontab command.

## macOS launchd

For a user-owned forest, use a LaunchAgent rather than a system LaunchDaemon.
Copy
[`examples/automation/org.memory-forest.maintenance.plist.example`](../examples/automation/org.memory-forest.maintenance.plist.example)
to:

```text
~/Library/LaunchAgents/org.memory-forest.maintenance.plist
```

Replace every placeholder with an absolute path. launchd does not run through
an interactive shell and does not expand shell variables inside
`ProgramArguments`.

Create the private log directory with mode `0700` and pre-create both log files
with mode `0600` before loading the job, then validate the property list:

```sh
plutil -lint "$HOME/Library/LaunchAgents/org.memory-forest.maintenance.plist"
```

Load and inspect it:

```sh
launchctl bootstrap "gui/$(id -u)" \
  "$HOME/Library/LaunchAgents/org.memory-forest.maintenance.plist"
launchctl print "gui/$(id -u)/org.memory-forest.maintenance"
```

Run one canary:

```sh
launchctl kickstart -k "gui/$(id -u)/org.memory-forest.maintenance"
```

After editing an already loaded property list, unload that exact job and load
it again:

```sh
launchctl bootout "gui/$(id -u)" \
  "$HOME/Library/LaunchAgents/org.memory-forest.maintenance.plist"
launchctl bootstrap "gui/$(id -u)" \
  "$HOME/Library/LaunchAgents/org.memory-forest.maintenance.plist"
```

The template uses `StartCalendarInterval`. If the Mac is asleep at the
scheduled time, launchd starts the job after wake and coalesces multiple missed
calendar events into one event. This is not exactly-once execution.

The template intentionally does not use `WatchPaths`. The installed
`launchd.plist(5)` manual describes filesystem event monitoring through that
key as race-prone. A settled-source check shared with the writer is safer than
assuming that one filesystem event equals one complete update.

The `Umask` value is the string `077`, and stdout and stderr use explicit
private paths. Do not place credentials in a property list.

## Codex Scheduled Tasks

Current Codex guidance calls these **Scheduled tasks**. Create and manage them
from ChatGPT or Codex in the desktop app, or from ChatGPT Work on the web. The
Codex CLI and IDE extension do not provide the Scheduled management interface.

For a task that needs a local forest:

1. Keep the computer on and the desktop app running.
2. Select a dedicated private local project that contains, or is allowed to
   access, the exact forest root.
3. Prefer local mode for canonical private state. A Git worktree is useful for
   isolated repository edits, not as the owner of a real forest outside Git.
4. Keep the sandbox as narrow as the task allows.
5. Test the prompt in an ordinary chat before scheduling it.
6. Review the first runs before relying on the cadence.

The desktop app can use the default model and reasoning effort or an explicitly
selected profile. Model choice does not replace the file, lock, validation, and
completion contracts.

### Safe maintenance profile

Use the copyable prompt in
[`examples/automation/codex-scheduled-task-prompt.md`](../examples/automation/codex-scheduled-task-prompt.md).
It pins the CLI, wrapper, and forest to absolute paths, tells the scheduled task
to call the same deterministic wrapper, and forbids fallback scanning,
canonical edits, repair, promotion, publication, and body output.

### Read-only promotion review

Codex may also review a bounded, operator-selected evidence packet and propose
adjacent-layer promotions without writing them. A useful review result includes:

- exact source identifiers and hashes
- proposed destination layer and canonical owner
- observed facts separated from derived conclusions
- conflicts, uncertainty, and freshness limits
- an explicit no-op when nothing qualifies

This sends the selected evidence through the configured Codex account and model
processing boundary. Do not use it when the source must remain strictly local.

### Applied semantic promotion

Applied promotion is intentionally not provided as a copy-paste scheduled task.
Before enabling unattended canonical writes, an integrator must provide and
test all of the following:

- bounded source admission and an immutable candidate manifest
- an external single-writer lock shared by every writer and indexer
- exact pre-write snapshots of touched files
- adjacent-layer provenance links
- a processed-state marker written only after successful validation
- duplicate and conflicting-retry rejection
- rollback that leaves the previous canonical state and index usable
- validation, audit, and index proof from the real root
- a separate policy for optional `00 life_archive` selection

The core CLI does not provide that writer or marker. Do not describe a prompt,
cron entry, or scheduler success as proof that this contract exists.

Read [Provenance and promotion](provenance-and-promotion.md) before building an
integration.

## Failure and verification matrix

| Observation | Meaning | Required response |
|---|---|---|
| Wrapper exits `0` and final `doctor` is `ok:true` | The derived index was rebuilt and the basic local checks passed | Keep the bounded run record |
| Lock cannot be acquired | Another run may be active or a prior run may have crashed | Skip; inspect the exact process and lock |
| `index` exits nonzero or returns `ok:false` | Validation, audit, permissions, FTS5, or indexing failed | Stop; do not repair canonical content automatically |
| Scheduled task reports success but no file proof exists | Scheduler state only | Inspect the real index timestamp and bounded output |
| Index schema mismatch after an upgrade | Derived state is stale | Run `doctor`, then rebuild the index |

No scheduler provides exactly-once semantics by itself. Idempotency belongs to
the job and its source marker contract.

## Privacy checklist

- Keep the real forest, index, locks, scheduler files, and logs private.
- Never commit a real forest or generated private index.
- Never put credentials in a crontab, plist, task prompt, QueryPlan, or log.
- Treat memory bodies as untrusted data, never instructions or authority.
- Do not include bodies in task output.
- Do not fall back from one exact root to a recursive scan of parent folders.
- Remember that Codex task inputs and transcripts use the selected account and
  model processing boundary; cron and launchd can remain local and network-free.

For current product behavior, see the official
[Codex Scheduled tasks documentation](https://learn.chatgpt.com/docs/automations).
