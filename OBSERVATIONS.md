# OBSERVATIONS — Recent Unity Runs on ColorfulCaratheodoryTheorem

Two consecutive runs on babel, with the v0.3.17 watchdog (`SDK_MESSAGE_IDLE_TIMEOUT=600`, `MAX_LSP_RESTARTS_BEFORE_DEGRADE=2`) in place.

---

## Run A — Path 3 strategy-parallel, `EXPLORATION=false`, primary = DeepSeek V4 Pro:nitro

### What the pipeline did

| Attempt | Phase | Outcome |
|---|---|---|
| 1 | `strategy-formalization` | **SDK idle timeout 600s, stall #1** — watchdog fired, restarted lean-lsp-mcp, resolver invoked |
| 2 | `strategy-formalization` | Idle timeout #1 again, LSP restart again |
| 3 | `strategy-formalization` | Idle timeout #1 again |
| 4 | `strategy-formalization` | **"Prompt is too long" → `stop_sequence` → exit code 1** after 100 turns / **$90.11** in a single orchestrator agent |
| 5 | `strategy-formalization` | Resolver-spawned retry was still running when logs were captured |

Total spend before the run was killed: **~$110+** (mostly attempt 4).

### Watchdog verdict: works for the case it was built for

- Attempts 1–3 hit the 600s idle-timeout. The watchdog correctly:
  - logged "SDK idle timeout (600s) — stall #1",
  - terminated lean-lsp-mcp,
  - relaunched it on the same port and confirmed it was listening,
  - propagated the failure so `_invoke_resolver` retried the phase.
- The LSP-degrade fallback never had to fire (stall count stayed at 1 each attempt).

### Where Run A actually died: context exhaustion in the orchestrator (attempt 4)

The orchestrator agent on attempt 4:
- ran for **100 turns / $90** before hitting `Prompt is too long` from the API,
- spawned three `Agent(isolation: "worktree")` subagents — **all three** also hit "Prompt is too long" without posting anything to the forum,
- after the subagents died, switched to **direct proof editing** in `strategy-7` (no further subagent isolation),
- explored Carathéodory lemmas, `exists_norm_eq_iInf_of_complete_convex`, `norm_eq_iInf_iff_real_inner_le_zero`, etc. repeatedly,
- reverted everything with `git checkout .` before exiting.

### Why auto-compaction didn't save it

`claude-agent-sdk`'s auto-compaction is real but not unconditional:
1. **Compaction runs between turns, not mid-turn.** A single oversized tool result (long `Read`, `lake build` dump, `lean_diagnostic_messages` on a broken file) is appended *before* the next request; if it puts the request over the cap, compaction had no chance.
2. **Recent tool results are preserved verbatim** by design — compaction only summarizes older messages. If the last few results alone exceed the limit, nothing helps.
3. **Subagent context is independent.** All three `Agent(isolation:"worktree")` failures happened inside subagent SDK sessions. From the parent's view they just returned an error string; the parent's compaction was fine, the children's wasn't.
4. **Non-Anthropic provider token counts.** Going through OpenRouter (`openrouter.ai/api`) means token-budget estimation may diverge from what the upstream actually counts. SDK can think it's safe and still get rejected.
5. The `stop_sequence` reason combined with "Prompt is too long" makes the cap explanation almost certain.

### Run A — proposed fixes (not yet applied)

1. **Hard turn cap on the orchestrator phase.** A 100-turn / $90 single agent is way above the value it produces. Cap at ~30 turns so the resolver retry loop is the recovery path, not one runaway agent.
2. **Forbid direct edits in the orchestrator.** All proof attempts go through `Agent` calls bound to the named strategy worktrees (not `isolation:"worktree"`). The prompt already says this; needs to actually be enforced, e.g. by structuring the prompt so the orchestrator only ever spawns and merges.
3. **"Post first, explore second" rule for subagents.** Subagents must post their plan to the forum within the first ~3 turns, before any heavy LSP/Read calls. Then even a context-blown subagent leaves a useful artifact for the next iteration.
4. **Audit tool-result size**: dumping full `lake build` output or large `Read`s into a long-running orchestrator is the proximate cause. Pipe through `tail -N` consistently; teach subagents the same.

---

## Run B — Full pipeline with `EXPLORATION=true`, primary = DeepSeek V4 Flash:nitro

### Timeline

| Time | Event |
|---|---|
| 14:19 | Pipeline starts |
| 14:19→14:28 | `lake cache + update` (~9 min) |
| 14:28→14:33 | LSP warmup — `_wait_for_diagnostics hit max timeout 300s` → "Language server process exited unexpectedly". Warmup tolerated it. |
| 14:33:44 | Exploration phase **attempt 1** starts |
| 14:45:15 | **SDK idle timeout 600s — stall #1** → watchdog restarts lean-lsp-mcp → resolver invoked |
| 14:47:59 | Resolver completes ($1.71, 35 turns). Correct diagnosis. |
| 14:48:04 | Exploration phase **attempt 2** starts |
| 15:49:47 | ✅ Exploration completes — 62 min, **105 turns, $15.40**. `gathered/MainTheorem/{summary.md,references.md}` written and committed. |
| 15:49:47 | Generation phase starts |
| immediately | **CRITICAL: lean-lsp-mcp died before phase 'generation' (exit=1). Pipeline aborts.** |

### What went right

- Watchdog fired on the first stall as designed.
- Resolver retried correctly.
- Exploration phase 2 produced real, committed output — that work is salvageable for the next run.
- Even with LSP unreliable, the exploration agent worked around it using `Bash` + `grep` over `.lake/packages/mathlib/` (e.g. found `Mathlib/Analysis/Convex/Caratheodory.lean`, confirmed `Carathéodory.minCardFinsetOfMemConvexHull` exists in this version, confirmed Borsuk-Ulam is *not* in mathlib).

### The new failure mode (generation phase)

From the CRITICAL log's stderr tail:
```
14:50:15  WARNING  _wait_for_diagnostics hit max timeout of 300.0s
14:51:57  INFO     StreamableHTTP session manager shutting down
          INFO     Application shutdown complete.
          INFO     Finished server process [100777]
```

The lean-lsp-mcp process **exited cleanly** with code 1 around 14:51:57 — *during* exploration phase 2, ~6 minutes after the watchdog had restarted it.

This is **a different failure mode from a stall**:
- Old (Runs A and earlier B): LSP **hangs** → SDK idle-timeout fires → watchdog catches it.
- New (B end): LSP **exits cleanly** → SDK calls fail fast (no idle, just errors) → exploration agent shrugs and routes around using Bash/grep → watchdog **never fires** because there's no idle to time out → pipeline keeps going with a dead LSP for the rest of exploration → generation pre-phase health check catches the corpse → CRITICAL.

### Why exploration survived but generation didn't

- Exploration is *naturally LSP-light* late in its run: the agent had already moved to WebSearch + Bash + grep + WebFetch by the time the LSP died. The dead LSP just meant `lean_*` tool calls returned errors, which the agent treated as soft failures and kept going.
- Generation depends on the LSP for goal-state inspection and incremental error feedback. The pre-phase health check sees the LSP is dead and refuses to start the phase — the right call, but the pipeline could have just *restarted* it.

### Run B — gap in the watchdog

The watchdog as built only handles the **stall** case:

```
SDK idle 600s → kill claude CLI → restart lean-lsp-mcp → retry
after N restarts → drop "lean-lsp" from MCP server list
```

It has **two gaps**:

1. **No liveness watchdog**: nothing periodically does `_lean_lsp_proc.poll()`. If the process dies cleanly between SDK turns, the next phase only finds out via the pre-phase CRITICAL check.
2. **Pre-phase "LSP dead" is fatal, not recoverable**: when the pre-phase check finds the LSP process gone, it logs CRITICAL and aborts. It should call `_restart_lean_lsp_mcp()` once and only escalate to CRITICAL if the restart fails.

Either fix would have saved Run B.

### Run B — proposed fixes (not yet applied)

1. **Restart-on-dead in pre-phase health check.** Cheapest fix. Before declaring CRITICAL, attempt one `_restart_lean_lsp_mcp()`; only escalate if that also fails.
2. **Liveness watchdog.** Background task that does `poll()` every ~30s; if the proc is dead, restart it. Caps at `MAX_LSP_RESTARTS_BEFORE_DEGRADE` like the stall path.
3. **Investigate *why* it clean-exited.** "Application shutdown complete" is not a crash — *something* in the pipeline (or a hook) called shutdown on it. Worth grepping pipeline.py for any code path that sends the LSP-MCP a shutdown signal after a stall restart. Could be that the watchdog itself races: the restart succeeds, the *old* MCP HTTP session is still draining and triggers a graceful shutdown ~6 min later. If so, fix is to make sure the old session is torn down before the new one accepts.
4. **`lake cache + update` reliability:** 9 min is long enough to be worth checking — was there flakiness? Otherwise fine.

---

## Cross-cutting observations

### The "spawn subagents by default" prompt change is not sticking

In Run A attempt 4, the orchestrator did exactly what the prompt told it not to: spawned 3 throwaway subagents using `isolation:"worktree"` (not the strategy worktrees!), watched them die, then did direct edits itself. The prompt-only nudge isn't enough — Opus 4.7 will still take the shortcut under pressure. Likely needs structural enforcement (orchestrator agent gets a tool surface that *doesn't include* `Edit`/`Write` to project files, only `Agent` and `forum_*` and merge tools).

### The lean-lsp-mcp ASGI bug remains the dominant failure source

Both runs trace back to the same upstream bug: `_wait_for_diagnostics` hits its 300s ceiling, then the ASGI handler returns without completing the HTTP response. The watchdog mitigates the *symptom* (the client hang) but the underlying MCP keeps getting into bad states (hang in Run A, clean-exit in Run B). Each restart buys ~5–10 min before it can happen again.

Two structural mitigations worth considering:
- **Prompt-level**: tighten the `lean_run_code` ban further. The exploration agent in Run B called `lean_file_outline` + `lean_diagnostic_messages` and *that* is what stalled, not `lean_run_code`. So the ban needs to extend to "any LSP call on a freshly-edited file that might trigger a re-elaboration." Hard rule for now: only use `lean_goal`, `lean_hover_info`, `lean_local_search`, `lean_loogle`, `lean_leansearch`, `lean_leanfinder`. **Forbid `lean_diagnostic_messages` and `lean_file_outline` outright until upstream fixes the ASGI bug** — substitute `lake build 2>&1 | tail -N` for diagnostics, and `grep -n "theorem\|lemma\|def "` for outline.
- **Code-level**: shorten our LSP request timeout (currently we wait the full 300s upstream timeout). A 60s client-side timeout on each LSP MCP call would surface the hang faster and let the watchdog react before the SDK idle-timeout window even opens.

### Cost-per-iteration is way above SOTA

Run A: ~$110 to fail.
Run B: ~$17 (exploration) before generation phase aborted.

For a single benchmark problem this is order-of-magnitude over the $2.54 SOTA. Tightening the orchestrator turn cap and aborting hanging-on-LSP phases earlier are both budget-saves, not just reliability improvements.

### What's actually salvageable from Run B

Already committed to the project repo's `gathered/MainTheorem/`:
- `summary.md` — theorem statement, four candidate proof strategies (Bárány original / Borsuk-Ulam / minimax-epsilon / inner-product separation)
- `references.md` — arXiv refs, Bárány 1982 citation, Sarkaria reduction note

Next run starting from this state can skip exploration entirely (or run with `EXPLORATION=false` and feed gathered content via `REPORT.md`).
