# Quickstart: Sub-Agent Dynamic Spawner

## Scenario 1: Spawn a Sub-Agent

```python
from simpleclaw.agents import SubAgentSpawner, PermissionScope

spawner = SubAgentSpawner(config)

result = await spawner.spawn(
    command=["python", "scripts/analyze.py"],
    task="Analyze data file",
    scope=PermissionScope(allowed_paths=["data/"], network=False),
    timeout=60,
)

print(result.status)  # "success"
print(result.data)    # {"analysis": "..."}
```

## Scenario 2: Concurrency Limit

```python
import asyncio

# Spawn 5 agents with limit of 3
tasks = [
    spawner.spawn(["python", "task.py"], f"Task {i}")
    for i in range(5)
]
# First 3 run immediately, 2 are queued
results = await asyncio.gather(*tasks)
# All 5 complete eventually
```

## Scenario 3: Check Pool Status

```python
status = spawner.get_pool_status()
print(status)  # {"running": 2, "queued": 1, "max": 3}
```

## Scenario 4: Graceful Shutdown

```python
await spawner.shutdown()  # Terminates all running sub-agents
```
