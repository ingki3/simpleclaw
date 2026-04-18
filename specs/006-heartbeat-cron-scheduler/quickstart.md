# Quickstart: Heartbeat Daemon & Cron Scheduler

## Scenario 1: Start and Stop Daemon

```python
from simpleclaw.daemon import AgentDaemon

daemon = AgentDaemon("config.yaml")
await daemon.start()   # Acquires PID lock, starts heartbeat + scheduler
# ... daemon runs in background ...
await daemon.stop()    # Clean shutdown, releases PID lock
```

## Scenario 2: Manage Cron Jobs

```python
from simpleclaw.daemon import CronScheduler, ActionType

scheduler = CronScheduler(store)

# Create a job that runs daily at 9am
job = scheduler.add_job(
    name="morning-briefing",
    cron_expression="0 9 * * *",
    action_type=ActionType.RECIPE,
    action_reference=".agent/recipes/morning-briefing/recipe.yaml",
)

# List all jobs
jobs = scheduler.list_jobs()
for j in jobs:
    print(f"{j.name}: {j.cron_expression} (enabled={j.enabled})")

# Update schedule to 8:30am
scheduler.update_job("morning-briefing", cron_expression="30 8 * * *")

# Disable temporarily
scheduler.disable_job("morning-briefing")

# Delete
scheduler.remove_job("morning-briefing")
```

## Scenario 3: Heartbeat Monitoring

```python
from simpleclaw.daemon import HeartbeatMonitor

monitor = HeartbeatMonitor(store, status_file=".agent/HEARTBEAT.md")

# Manual tick (normally called by daemon scheduler)
tick = await monitor.tick()
print(f"Dirty: {tick.dirty_state}, Pending: {tick.pending_task_count}")
```

## Scenario 4: Dreaming Auto-Trigger

```python
from simpleclaw.daemon import DreamingTrigger

trigger = DreamingTrigger(
    conversation_store=conv_store,
    dreaming_pipeline=pipeline,
    daemon_store=daemon_store,
    overnight_hour=3,
    idle_threshold=7200,
)

if await trigger.should_run():
    await trigger.execute()
```

## Scenario 5: Wait States

```python
from simpleclaw.daemon import WaitStateManager

manager = WaitStateManager(store)

# Register a wait
manager.register_wait(
    task_id="task-123",
    state={"step": 3, "context": "waiting for API response"},
    condition_type="callback",
    timeout=3600,
)

# Check and resolve
pending = manager.get_pending()
timed_out = manager.check_timeouts()
manager.resolve_wait("task-123", resolution="completed")
```
