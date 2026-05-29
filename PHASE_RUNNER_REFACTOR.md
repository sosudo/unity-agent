# Phase-runner refactor — design note (deferred)

Approved in principle. Not yet implemented.

## Why deferred

A grep of `_assert_lsp_alive(...)` finds 19 phase blocks in `run_pipeline`, ranging 48 to 240 lines each (~1,700 lines total). They share a skeleton, but the variability is real:

| Variability | Affected phases |
|---|---|
| **Variant dispatch** (FF/TF/TT, FF/FT/TF/TT, F/T) | Semiformalization, Exploration, Formalization, Critic |
| **Worktree create/cleanup + audit** | Formalization (F/T), Strategy-Formalization, Escalation |
| **Contract check + raise** | Generation, Validation, Critic |
| **Multi-subagent dict** | Exploration (3 subagents), Formalization (2 subagents + library) |
| **Per-iteration prompt suffix** ("REPORT.md from previous iteration…") | Formalization, Critic |
| **`.format` / `Template` substitution** | Retrospective, Recursive-Unity |
| **No critic loop** | Source-Scan, Retrospective |

A single `_run_phase(...)` that handles all of this needs ~6 optional callables (`pre_query`, `build_options`, `contract_check`, `post_query`, `commit_metadata`, `user_prompt_builder`). At that point the dispatcher is barely smaller than the call sites, and the indirection makes the control flow harder to follow than the current straight-line code.

Two shapes look workable, but each needs the user's judgment before I land it:

### Shape A — Phase descriptor + thin runner

```python
@dataclass
class PhaseSpec:
    name: str
    title: str
    prompt_path: Path
    subagents: dict[str, AgentDefinition]
    user_prompt: str
    model: str = "opus"
    fallback_model: str = "sonnet"
    budget: float | None = None
    extra_options: dict = field(default_factory=dict)
    pre_query: Callable[[], None] | None = None
    contract_check: Callable[[], None] | None = None
    post_query: Callable[[], None] | None = None
    commit_metadata: dict | None = None
```

A `_run_phase(spec: PhaseSpec)` then runs the outer `while True`. Saves ~30 lines per simple phase, ~15 per complex phase. Worktree phases still need custom pre/post callbacks.

**Pro**: Forces every phase through the same code path — uniform contract-check raise, uniform commit/`_phase_succeeded`, uniform resolver routing.
**Con**: Heavy use of closures-over-pipeline-state. Test surface widens.

### Shape B — Decorator wrapping the inner query

```python
@phase_runner("validation")
async def _validation_phase(_val_opts, user_prompt):
    async for msg in _query_with_idle_timeout(prompt=user_prompt, options=_val_opts):
        _log_agent_message(msg)
    if not Path("VALIDATION_REPORT.md").exists():
        raise FileNotFoundError(...)
```

`phase_runner` would wrap the outer `try/while/_invoke_resolver/_commit_phase/_phase_succeeded` boilerplate. Smaller savings but lower risk.

## Recommendation

After **Workstream B** (forum + refinement-mapping deep-dive) lands — that workstream may reshape the semiformalization council, the exploration sub-agent menu, or the formalization variant matrix, and the refactor surface changes if those shift. Then revisit with one of the two shapes above.

If you want it sooner, **Shape B** is the safer first step — it preserves each phase's existing body and only consolidates the outer loop. Approve a shape and I'll implement it as its own commit series with phase-by-phase migration.
