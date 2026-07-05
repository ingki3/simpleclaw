## Summary
- 

## Runtime Surface
- [ ] No runtime behavior changed
- [ ] User entrypoint changed: `/...`
- [ ] Cron/recipe changed: `...`
- [ ] Skill/tool changed: `...`
- [ ] Config/deploy/restart needed: yes/no + reason

## Validation
- [ ] `.venv/bin/python -m ruff check src/ tests/ scripts/`
- [ ] `.venv/bin/python -m pytest tests/unit/ -v`
- [ ] `.venv/bin/python -m pytest tests/contracts/ -v`
- [ ] `.venv/bin/python -m pytest tests/integration/ -m "offline" -v` if integration surface changed
- [ ] `scripts/smoke/live_runtime_smoke.py --json --no-telegram-send` after deploy/restart if live runtime surface changed

## Evidence
Paste focused command output or CI links here.

## Multica
- Issue: BIZ-XXX or N/A
