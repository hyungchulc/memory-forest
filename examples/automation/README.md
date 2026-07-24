# Automation examples

These examples schedule deterministic validation and private derived-index
rebuilding. They do not implement capture or semantic promotion.

- [`run-maintenance.sh`](run-maintenance.sh) provides one bounded maintenance
  command whose `index` operation acquires the core sibling lock.
- [`crontab.example`](crontab.example) shows a portable daily cron entry.
- [`org.memory-forest.maintenance.plist.example`](org.memory-forest.maintenance.plist.example)
  shows a per-user macOS LaunchAgent.
- [`codex-scheduled-task-prompt.md`](codex-scheduled-task-prompt.md) provides a
  bounded Codex Scheduled Task prompt.

Read the full [Automation guide](../../docs/automation.md) before installing a
schedule. Replace every placeholder, keep all state and logs private, and run
the wrapper manually before enabling unattended execution.
