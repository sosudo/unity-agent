# Unity Pipeline Audit (Workstream A)

Date: 2026-05-28. Scope: `unity_agent/pipeline.py` (3,242 lines), `cli.py`, `PROMPTS/`, `DEFAULT_PROMPTS/`, `TEAMS/`, `SUBAGENTS/`, `DEFAULT_SUBAGENTS/`, `.env.example`. Read-only; no code changes. See `PROPOSED_FIXES.md` for minimal-diff proposals.

This report supplements (does not replace) existing `ISSUES.md` and `OBSERVATIONS.md`. Where a finding is already tracked there, it is cross-referenced and only re-flagged if its status has changed since those documents were written.

---

## §0. TL;DR — what's actually broken vs. what looks broken

**Actually broken or load-bearing wrong:**
- **F1 — Internal contradiction in `FORMALIZATION/T.md`**: line 43 says "The Lean project is the ground truth"; line 237 says "Source is ground truth". Both directives apply to the same agent on the same chunk. (verified by grep)
- **F2 — Bandit escalation is dead code, `tier` hardcoded to "B"**: `_load_bandit_state`, `_save_bandit_state`, `_resolve_escalation_outcomes`, `_stagnant_chunks`, `_default_bandit_state` track outcomes per chunk, but `_run_escalation_phase` at `pipeline.py:1489` hardcodes `tier = "B"` and never reads `state["chunks"][cid]["last_escalation"]["success"]` to pick a tier. About 100 lines of infrastructure with no effect on behavior beyond logging. This is the "model escalation mechanic that doesn't work" the user mentioned.
- **F3 — Resolver retry counter never decrements/resets on success** (`pipeline.py:1359, 1364`). `_retries[phase_name]` accumulates across the entire pipeline run; a flaky-but-eventually-successful phase consumes the cap earlier than expected. Already noted as `ISSUES.md` S2.4 — still standing.
- **F4 — Rate-limit handler sleeps then returns without retry cap accounting** (`pipeline.py:1378-1387`). If a rate-limit error is sticky, the resolver sleeps, returns, the phase retries, gets the same rate-limit error, sleeps again, indefinitely. `RESOLVER_MAX_RETRIES` is bypassed because the rate-limit branch increments `_retries` (line 1364) but the sleep-then-return path masks the failure as a "successful resolver." Already noted as S2.3 — still standing, partially mitigated only by the `_retries` accumulation in F3.
- **F5 — `max_validation_iterations` default is `None`** (`pipeline.py:969`) → an INVALID report indefinitely loops generation+validation. The `.env.example` documents `None|int` as the type but does not flag the unboundedness. Same risk applies to other `*_BUDGET` env vars.
- **F6 — `_assert_lsp_alive` is a hard `exit(1)`** (`pipeline.py:1160`). The watchdog (`_restart_lean_lsp_mcp`, `pipeline.py:1180`) can restart the LSP *mid-phase* on idle timeout, but if the LSP died silently *between* phases, the next `_assert_lsp_alive` call ends the run instead of attempting the same restart logic. Asymmetric recovery.
- **F7 — Worktree EMERGENCY-commit rescue exists but worktree itself is `--force` removed regardless** (`pipeline.py:454`). If the rescue commit on the worktree branch succeeded, work survives; if the worktree subagent quit without committing and the rescue's `git add -A` failed (rare but possible — e.g., on a corrupted index), the `_cleanup_worktree` step still runs and the worktree is deleted. The audit log says "work will be LOST at cleanup" — and it is, immediately.

**Looks broken but isn't:**
- Path 2's read of `ACTIVE_PROMPTS_DIR / "EXPLORATION.md"` and Path 3's reads of `FORMALIZATION/STRATEGY.md` / `CRITIC_STRATEGY.md` — these resolve to `PROMPTS/PROVE/*` because both paths are guarded by `prove=True`, and `ACTIVE_PROMPTS_DIR = PROVE_PROMPTS_DIR` in that case. All required files exist under `PROMPTS/PROVE/`. (I had flagged this as missing in an earlier pass; verified false alarm.)
- `model="sonnet"` / `model="haiku"` in source-scan, validation, resolver, inference (`pipeline.py:1438, 1904, 2291, 2383, 859, 860, 2292`) — looks like a model misconfig vs. the user's "everything opus" rule, but the `_primary_env` / `_secondary_env` builders pin **all three slots** (`ANTHROPIC_DEFAULT_OPUS_MODEL`, `ANTHROPIC_DEFAULT_SONNET_MODEL`, `ANTHROPIC_DEFAULT_HAIKU_MODEL`) to `PRIMARY_MODEL` / `SECONDARY_MODEL` (`pipeline.py:994-1013`). So whatever the SDK picks for `model="sonnet"` is `PRIMARY_MODEL`. The `model=` field is decorative — the env pinning makes it impossible for a phase to land on a different model than `PRIMARY_MODEL` (or `SECONDARY_MODEL` for escalation). This supersedes `ISSUES.md` S2.2 which assumed the tier-A escalation actually used sonnet — it doesn't.
- `PROMPTS/` vs `DEFAULT_PROMPTS/` drift — verified by subagent: zero meaningful divergence. The lockstep is being maintained.

---

## §1. Computation paths

`run_pipeline()` at `pipeline.py:885` dispatches on `(prove, source, exploration)` to one of three paths.

| Path | Trigger | Phases (in order) | `ACTIVE_PROMPTS_DIR` | Notes |
|---|---|---|---|---|
| **1 — Normal** | `not prove` OR (`prove and source is not None`) | Source Scan → [GEN ↔ VAL loop] → Semiformalization (FF\|TF\|TT) → critic loop {Exploration (FF\|FT\|TF\|TT) → Formalization (F first iter, T thereafter) → Critic → Retrospective → Escalation} | `PROMPTS/` (or `PROMPTS/PROVE/` if `--prove --source X`) | `pipeline.py:2254-3242` |
| **2 — Prove, source-less, EXPLORATION=true** | `prove and source is None and exploration` | Exploration (single variant) → [GEN ↔ VAL loop] → Semiformalization (TT only) → critic loop {Formalization (T) → Critic → Retrospective → Escalation} | `PROMPTS/PROVE/` | `pipeline.py:1781-2253` |
| **3 — Prove, source-less, EXPLORATION=false** | `prove and source is None and not exploration` | critic loop {Strategy Formalization → Strategy Critic} | `PROMPTS/PROVE/` | `pipeline.py:1658-1779` — no semiformal, no DAG, no worktree-per-chunk; orchestrator manages strategy worktrees ad-hoc. This is where OBSERVATIONS.md Run A blew up with 100 turns / $90 / context exhaustion. |

**Variant resolution in Path 1:**

| Phase | Variant axis | Resolution |
|---|---|---|
| Semiformalization | `(autofix, context)` | `(F,F)=FF`, `(T,F)=TF`, `(T,T)=TT`, `(F,T)=hard exit` at `pipeline.py:2574` |
| Exploration | `(recurse, context)` | `(F,F)=FF`, `(F,T)=FT`, `(T,F)=TF`, `(T,T)=TT`. All 4 wired. |
| Formalization | `(context, iteration)` | `(F, 0)=F`, `(else)=T`. So `T` is hit on iteration ≥ 1 even without `--context`. |
| Critic | `(context, iteration)` | Same: `(F, 0)=F`, `(else)=T`. Critic subagent prompts switch but the system prompt is `CRITIC.md` (no variant). |

**Prompts that bypass `ACTIVE_PROMPTS_DIR`** (read from top-level `_PROMPTS_DIR` even in `--prove` mode):
- `VALIDATION.md` (`pipeline.py:1890, 2369`) — intentional? prove mode never gets to customize validation.
- `RETROSPECTIVE.md` (`pipeline.py:2163, 3144`) — intentional? prove mode never gets a custom retrospective.
- `RESOLVER.md` (`pipeline.py:1413`) — shared.
- `INFERENCE.md` (`pipeline.py:847`) — shared.
- `SOURCE_SCAN.md` (`pipeline.py:2262`) — only called in Path 1; no PROVE variant needed.
- `RECURSIVE/UNITY.md` (`pipeline.py:1335`) — shared (uses `.format(depth=…, child_depth=…)`, fragile if prompt contains stray `{`/`}`).

This is asymmetric — generation/semiformalization/formalization/critic are prove-customizable, but validation and retrospective are not. Either intentional (validation is structural; retrospective doesn't care) or an inconsistency worth tightening.

---

## §2. Severity-ranked findings

Severity scale: **S0** = ships broken behavior to user; **S1** = wastes runs / money / produces incorrect output silently; **S2** = friction or fragility; **S3** = cosmetic.

### S0-class

**S0.1 — `FORMALIZATION/T.md` contains contradictory ground-truth rules.** (Goal-fit, F1)
- `unity_agent/PROMPTS/FORMALIZATION/T.md:43` — "The Lean project is the ground truth — all formalization decisions must conform to it."
- `unity_agent/PROMPTS/FORMALIZATION/T.md:237` — section `**Source is ground truth**`, "Read `source_proof` first — it is ground truth."
- Same agent, same prompt, opposing directives. Resolution likely depends on which line the model attends to most.
- Same conflict appears across `SEMIFORMALIZATION/TT.md:26` ("Lean is the ground truth; if the source conflicts with the existing Lean project, the Lean project wins") and `FORMALIZATION/ESCALATION.md:126` / `FORMALIZATION/F.md:233` ("Source is ground truth").
- This collides with the stated Unity goal: *source faithfulness is the contract for source-derived chunks; helpers are free to be merely correct*. The "Lean wins" rule contradicts source-faithfulness for the source-derived parts.

**S0.2 — Mode-conflict between Normal and PROVE is not surfaced to subagents.** (Goal-fit)
- `PROMPTS/PROVE/GENERATION.md` permits proof-freedom (proof structure is "advisory"); `PROMPTS/PROVE/FORMALIZATION/F.md` likewise.
- Normal `PROMPTS/FORMALIZATION/F.md` mandates transcription ("the formalizer's job is mechanical translation plus type glue, not re-derivation").
- Subagent prompts (DECLARATIONFORMALIZER, PROOFFORMALIZER) don't carry a "you are in PROVE mode" flag. Subagents reading the system prompt see whichever directive the parent loaded; if those subagent prompts are recycled across modes (which they are — `_SUBAGENTS_DIR / "FORMALIZATION/DECLARATIONFORMALIZER/F.md"` is loaded for **both** Normal-Path 1 and Prove-Path 2 formalization at `pipeline.py:2018-2020` and `pipeline.py:2836-2839`), there's no in-prompt cue distinguishing modes.

**S0.3 — Bandit escalation is dead code.** (F2)
- `pipeline.py:700-762` defines bandit state machinery (alpha/beta tracking via `last_escalation.success`, secondary spend tracking).
- `pipeline.py:1450` docstring claims: *"Tier selection uses a Beta(α,β) bandit over prior outcomes with running wall-clock means."*
- Actual code at `pipeline.py:1489`: `tier = "B"`. Hardcoded. Nothing reads the bandit state to make a decision.
- `_resolve_escalation_outcomes` writes `success=not sig[1]` at line 749 but no consumer.
- Effect: escalation runs every iteration with the same secondary tier regardless of past success rate. Defensible behavior (always escalate to most-capable) but the surrounding 100 lines of dead machinery is misleading. The user's claim that "model escalation doesn't work" is correct in the sense that it doesn't *choose* a tier.

**S0.4 — Resolver fails open: agent ends_turn cleanly with no artifact.** (Pre-existing `ISSUES.md` S1.4, S2.1; partially mitigated)
- New since `ISSUES.md` was written: contract-breach one-shot repair blocks were added at `pipeline.py:1873, 1916, 2147, 2352, 2394, 3067, 3123`. These catch the most common cases (generation→no chunks, validation→no report, critic→no report) and *do* re-prompt the agent. Good improvement.
- Still missing: closing gates inside the prompts themselves. The prompt-diff subagent confirmed only `SOURCE_SCAN.md` has an explicit "do not end_turn until <file> exists" gate. Gates in prompts would prevent the one-shot repair from being needed in the first place, and would also cover gaps that aren't repaired today (exploration, semiformalization).
- The contract-breach repair uses the *same agent context* (same `query()` continuation). If the model already silently decided the phase was done, a second "no really, write the file" prompt may not unstick it. A new agent invocation would be more robust.

### S1-class

**S1.1 — `_retries[phase_name]` never resets on success.** (F3, pre-existing S2.4)
- `pipeline.py:1364`: increments unconditionally on every resolver invocation.
- Nothing decrements or zeroes it after a successful retry.
- With `RESOLVER_MAX_RETRIES` set, a phase that flakes-then-succeeds N times during a long run hits the cap at the wrong point.
- With `RESOLVER_MAX_RETRIES` unset (default), infinite loop risk remains.

**S1.2 — Rate-limit branch returns instead of bounded retry.** (F4, pre-existing S2.3)
- `pipeline.py:1378-1387`: sleeps based on `retry-after`, then `return`s. The outer phase loop retries the query. If the rate limit is sticky, this loops indefinitely. The increment at line 1364 *is* counted, but only `RESOLVER_MAX_RETRIES` caps it — and unsetting it is the default in `.env.example`.
- The rate-limit sleep can also exceed any reasonable budget if `retry-after` parses to a large number (the regex captures arbitrary integers).

**S1.3 — `_assert_lsp_alive` hard-exits on dead LSP.** (F6)
- `pipeline.py:1146-1160`: if `_lean_lsp_proc.poll() is not None`, `exit(1)`. No restart attempt.
- The watchdog `_restart_lean_lsp_mcp` (`pipeline.py:1180`) only fires on **idle timeout during** a query — not on LSP death between phases.
- Real failure mode: LSP crashes silently during semiformalization, next `_assert_lsp_alive("formalization")` kills the run, hours of work lost.

**S1.4 — Worktree EMERGENCY-commit rescue is best-effort, then cleanup runs unconditionally.** (F7)
- `pipeline.py:405-434`: if `git add -A` fails or `git commit` fails, logs a warning that "work will be LOST at cleanup" — but cleanup still runs.
- In practice unlikely to fire (git is robust), but the "best-effort then hard delete" pattern is fragile.
- Cheaper alternative: on rescue failure, stash the worktree branch ref and skip the `worktree remove --force`, keeping the on-disk worktree until manual triage.

**S1.5 — `max_validation_iterations` default `None` = unbounded loop.** (F5)
- `pipeline.py:969`. If validator keeps returning INVALID and no cap is set, the gen↔val loop never terminates.
- Symmetric risk with `MAX_CRITIC_ITERATIONS` (default 3 — bounded ✓) and `RESOLVER_MAX_RETRIES` (default None — unbounded ✗).
- All three should have defensive defaults.

**S1.6 — Variant-resolution asymmetry: Semiformalization in `--prove` ignores autofix/context.** (Code consistency)
- `pipeline.py:1957`: Path 2 always uses `SEMIFORMALIZATION/TT.md`. Comment says "always TT: autofix + context, required for Path 2".
- But Path 2's enforcement of context is in CLI (`cli.py:79-85`) — the env var `AUTOFIX` is read at `pipeline.py:965` but never validated for Path 2. If user sets `AUTOFIX=False`, Path 2 still uses the TT prompt that assumes autofix.
- Likely benign in practice, but the inconsistency between "respect env vars" (Path 1) and "ignore them" (Path 2) is silent.

**S1.7 — Six stale forum directories at repo root.** (Cruft confirmed by gitignore inspection)
- `forum/`, `forum2/`, `forum3/`, `forum_c/`, `a_forum/`, `forum_sards/` — all gitignored.
- Only `Path.cwd() / "forum"` is referenced in code (`pipeline.py:1170, 1256, 1309`). The numbered/suffixed ones are leftover from prior runs.
- Safe to delete (already gitignored, no code references).

**S1.8 — Tool-naming reminder is runtime-appended to every prompt** (`pipeline.py:1344-1356`).
- The `with_library()` helper appends a paragraph clarifying `mcp__unity-forum__forum_post` (hyphen) vs `mcp__unity_forum__*` (underscore).
- This is essentially a workaround for a bug agents kept hitting. Belongs in the prompts where the agent reads forum tool names, or in a single MCP server description, not appended at every system-prompt construction.

### S2-class

**S2.1 — Recursive-unity prompt loaded via `.format()`.** (`pipeline.py:1336`)
- `recursive_prompt = f.read().format(depth=depth, child_depth=child_depth)`.
- If `RECURSIVE/UNITY.md` ever contains an unescaped `{` or `}` (markdown code block placeholder, JSON example, etc.), `str.format` raises `KeyError` and the recursive subagent never registers. Silent degradation: pipeline runs without the recursive escape hatch.
- Same risk in `RETROSPECTIVE.md` `.format(SOURCE_PATH=…, LIBRARY_DIR=…, ...)` at `pipeline.py:2164, 3145`.

**S2.2 — Variable shadowing of `worktree_assignments`.** (Cosmetic, ruff would catch)
- `pipeline.py:2007, 2824, 2917`: re-annotated three times within `run_pipeline`. Not buggy, but a type checker complains.

**S2.3 — Path 1 source-scan loads `_PROMPTS_DIR` (not `ACTIVE_PROMPTS_DIR`).** (`pipeline.py:2262`)
- Source scan is normal-only (no PROVE variant), and `_PROMPTS_DIR` ≠ `ACTIVE_PROMPTS_DIR` only when `--prove --source` and either Teams or not. In `--prove --source` Path 1, `ACTIVE_PROMPTS_DIR = PROMPTS/PROVE` but source-scan reads top-level. Intentional? Comments don't say.

**S2.4 — Closing-gate coverage at 1/14 phases.**
- Confirmed by prompt-diff subagent: only `SOURCE_SCAN.md` has an end_turn closing gate. All other phase prompts lack them. The contract-breach one-shot repair blocks at runtime partially mitigate but don't replace gates.

**S2.5 — Forum web UI launched even when `SILENT=True` or for short runs.** (`pipeline.py:1311-1320`)
- Always-on subprocess with `atexit` cleanup. For headless CI/cron runs (e.g. recursive child unity calls), this is unwanted overhead and a port collision risk if multiple unity instances start.
- The recursive-unity case in particular: child runs inherit `FORUM_PORT` from env unless overridden, so port collision is a known failure mode for `depth > 1`.

### S3-class

**S3.1 — Comment lies in `_run_escalation_phase` docstring.** (`pipeline.py:1450`)
- Claims "Tier selection uses a Beta(α,β) bandit over prior outcomes". It doesn't. (See S0.3.)

**S3.2 — `LIBRARY_SUBAGENTS` is a module-level global mutated in `run_pipeline`** (`pipeline.py:209, 1325-1326, 1337`).
- Works because there's only one pipeline per process, but fragile. The `global LIBRARY_SUBAGENTS` declaration is in a function that runs once.

**S3.3 — Repo root has stale build artifacts.** (Workspace hygiene)
- `2512.24601v2.pdf`, `Cuddy.pdf`, `poster.pdf`, `poster.aux`, `poster.log`, `poster.tex`, `sample.pdf`, `unity.pdf`, `source`, `logs`, `TODO` — these are gitignored or untracked. Not a code issue; just project hygiene.

---

## §3. Goal-conduciveness gaps (Workstream A subset of Task 5)

From the goal-fit subagent's analysis, verified spot-checks against the actual prompt files. Findings most relevant to the **scalability + faithfulness** goal:

### Where the prompts pull toward the goal

- **SOURCE_SCAN.md**: front-loads mathlib context before IR design — directly supports "don't reinvent helpers". Strong.
- **GENERATION.md** (Normal): immutable `is_assumption`, `source_range`, `source_proof` fields are a hard contract. Strong.
- **SEMIFORMALIZATION/FF.md**: council convergence + immutable fields + ACCEPT/OBJECT protocol push parallel agents toward convergence on a faithful translation. Strong.
- **EXPLORATION/*.md**: clean "source priority — do not resolve any source-declared chunk" rule lets helpers be free without contaminating source-derived parts. Strong (this is the cleanest implementation of the goal's source-vs-helper distinction).
- **FORMALIZATION/{F,T}.md "Source is ground truth"** section (line 233-249 in F, 237-249 in T): explicit "transcribe, don't re-derive" pressure. Strong.

### Where the prompts pull against the goal

- **S0.1 (above)** — internal contradictions on ground truth.
- **S0.2 (above)** — mode-conflict between Normal and PROVE invisible to subagents.
- **VALIDATION.md check 8 ("IR expressiveness") is a WARN, not a FAIL.** Goal demands the IR be a refinement mapping that preserves source structure; a WARN-level expressiveness check lets a structurally lossy IR ship downstream. The prompt-diff subagent noted TEAMS/VALIDATION.md adds stricter field-validation (is_assumption / source_range / source_proof structural checks) that PROMPTS/VALIDATION.md lacks — TEAMS is closer to the goal contract.
- **CRITIC.md faithfulness checks are heuristic.** Lines 49-61 describe semantic + structural checks but provide no rubric or examples. Agents fall back to "looks fine" judgments.
- **RETROSPECTIVE.md doesn't extract faithfulness lessons.** Captures tactic patterns and lemma usage, but doesn't ask "were any source chunks paraphrased? was a helper allowed to contaminate a source chunk?" — so the library can't accumulate faithfulness-specific tips.
- **Forum coordination is mandated but not gated.** Many prompts say "post to forum at start" or "use forum_post when X" but the pipeline's reward hook (`_forum_reward_hook`, `pipeline.py:1258`) only gives +0.5 ICRL credit per post — and nothing reads that credit to alter behavior. ICRL credit is logged in `additionalContext` but no downstream gating. Forum participation is "encouraged" but not "required to terminate".

---

## §4. Refinement-mapping and forum value (preview of Workstream B)

Quick observations to motivate the deeper Workstream B review:

**Refinement mapping (semiformal IR + chunking):**
- The IR contract has real teeth: immutable fields, schema-validated, council convergence. This is the *strongest* refinement mapping mechanism I see in the code.
- The DAG parallelism payoff is real (per-chunk worktrees + topological layers) but the chunk granularity decision is left to generation, and large-chunk failures (one chunk = entire theorem statement + proof) blow up Path 3's strategy mode (see OBSERVATIONS Run A: 100 turns / $90 in a single orchestrator).
- Generation prompt doesn't enforce a maximum chunk size or recommend recursive-unity for oversized chunks. The recursive-unity mechanism exists but is opt-in.

**Forum:**
- Mechanism exists, agents are told to use it, ICRL credit logged. But **no gate currently reads forum state to influence pipeline decisions**. The reward hook only logs balances in `additionalContext`; nothing inspects them. Forum is communication, not coordination control.
- The "decision" tag retrieval (`forum_get_tag("decision")`) appears in TF.md but not all variants. Cross-variant inconsistency on forum usage.
- Without forum-state gating, the forum mostly serves as a *human-readable transcript* — useful for interpretability and post-hoc review, but not driving runtime behavior. That's not nothing (and matches the user's "interpretability" mention), but it is less than the framing suggests.

These observations are intentionally tentative; Workstream B should dig deeper.

---

## §5. Timeout & retry semantics

Summary (with concrete config defaults from `.env.example`):

| Mechanism | Where | Default | Behavior on failure |
|---|---|---|---|
| `SDK_MESSAGE_IDLE_TIMEOUT` | `pipeline.py:974, 1229` | 600s | Raises `asyncio.TimeoutError`, increments stall counter |
| `MAX_LSP_RESTARTS_BEFORE_DEGRADE` | `pipeline.py:975, 1239` | 2 | After N stalls, drop lean-lsp from MCP servers for rest of run |
| `_restart_lean_lsp_mcp` | `pipeline.py:1180` | — | Terminate, respawn, wait up to 30s for port |
| `_assert_lsp_alive` | `pipeline.py:1146` | — | **Hard exit on dead LSP** (S1.3) |
| `RESOLVER_MAX_RETRIES` | `pipeline.py:1363` | None (unbounded) | If cap exceeded, exit(1); never resets on success (S1.1) |
| `MAX_VALIDATION_ITERATIONS` | `pipeline.py:969` | None (unbounded) (S1.5) |
| `MAX_CRITIC_ITERATIONS` | `pipeline.py:968` | 3 | Loop stop |
| `CLAUDE_CODE_STREAM_CLOSE_TIMEOUT` | `pipeline.py:972` | 180000 ms | SDK internal |
| Rate-limit handler | `pipeline.py:1378` | — | Sleep retry-after seconds (capped only by `RESOLVER_MAX_RETRIES`) (S1.2) |
| Per-phase `max_budget_usd` | per-`ClaudeAgentOptions` | `*_BUDGET` env vars, default None | SDK-side stop |

**On the user's question of "should we just set no timeout":**
- Removing `SDK_MESSAGE_IDLE_TIMEOUT` is *unwise* — OBSERVATIONS Run A shows the watchdog working as designed; removing it means a stuck LSP/agent never gets retried.
- The actual problem isn't the timeout being too short; it's the *retry budget* being unbounded (`RESOLVER_MAX_RETRIES` default None → infinite respin). Pair the existing timeout with bounded retries + a sane orchestrator turn cap (OBSERVATIONS recommends ~30, current 100+ has been observed).
- For LSP-MCP specifically: increase `MAX_LSP_RESTARTS_BEFORE_DEGRADE` to 3-4 and consider making the degraded (LSP-less) mode log a warning rather than silently losing tool access.

---

## §6. Computation-path-by-path consistency check

Pulling everything together, here's what each path actually loads:

**Path 1 (Normal):**
- Prompts: `SOURCE_SCAN`, `GENERATION`, `VALIDATION` (top-level only), `SEMIFORMALIZATION/{FF,TF,TT}`, `EXPLORATION/{FF,FT,TF,TT}`, `FORMALIZATION/{F,T,ESCALATION}`, `CRITIC`, `RETROSPECTIVE` (top-level only), `RESOLVER`, `RECURSIVE/UNITY`.
- Subagents: `SOURCE_SCAN/SCANNER`, `GENERATION/GENERATOR`, `SEMIFORMALIZATION/{FF,TF,TT}`, `EXPLORATION/EXPLORER/{F,T}` + `SEMIFORMALIZER/{F,T}` + `EXPLORATIONGENERATOR`, `FORMALIZATION/DECLARATIONFORMALIZER/{F,T}` + `PROOFFORMALIZER/{F,T}`, `CRITIC/DECLARATIONFORMALIZER/{F,T}` + `PROOFFORMALIZER/{F,T}`.

**Path 2 (Prove + source-less + EXPLORATION=true):**
- Prompts: `PROVE/EXPLORATION` (single, no FF/FT/TF/TT split — note inconsistency with Path 1 which has 4 exploration variants), `PROVE/GENERATION`, `VALIDATION` (top-level), `PROVE/SEMIFORMALIZATION/TT` (only — autofix/context ignored), `PROVE/FORMALIZATION/T`, `PROVE/CRITIC`, `RETROSPECTIVE` (top-level), `PROVE/FORMALIZATION/ESCALATION`.
- Subagents: `PROVE/EXPLORATION/EXPLORER`, `GENERATION/GENERATOR` (shared with Path 1 — note: `_SUBAGENTS_DIR` not `ACTIVE_SUBAGENTS_DIR` at `pipeline.py:1839`), `PROVE/SEMIFORMALIZATION/TT`, `FORMALIZATION/DECLARATIONFORMALIZER/T` (shared from non-PROVE — `_SUBAGENTS_DIR` not `ACTIVE_SUBAGENTS_DIR` at `pipeline.py:2018`), `PROVE/FORMALIZATION/PROOFFORMALIZER/T`, `CRITIC/DECLARATIONFORMALIZER/T` (shared, line 2109), `CRITIC/PROOFFORMALIZER/T` (shared, line 2111).
- **Inconsistency**: DECLARATIONFORMALIZER subagents are *not* swapped for PROVE mode; PROOFFORMALIZER subagents *are*. So in PROVE mode, the declaration formalizer doesn't know about proof-freedom but the proof formalizer does. Compounds S0.2.

**Path 3 (Prove + source-less + EXPLORATION=false):**
- Prompts: `PROVE/FORMALIZATION/STRATEGY`, `PROVE/CRITIC_STRATEGY`.
- Subagents: `PROVE/EXPLORATION/EXPLORER` only.
- No semiformal, no DAG, no per-chunk worktrees. Orchestrator is on its own to manage strategy worktrees, which OBSERVATIONS Run A documents as the cause of the $90 runaway.

---

## §7. What this audit does NOT cover

These are out of scope for Workstream A but flagged for downstream work:

- **`forum_mcp.py` (758 lines)** — not audited line-by-line. ICRL math, voting weights, dimension proposal/approval flow all need separate review. Workstream B.
- **`forum_web.py` (1,125 lines)** — UI; not on the autoformalization critical path.
- **`setup_cmd.py`** — Workstream D.
- **`scripts/`** — only verified that seeding works; haven't audited whether each script is actually invoked.
- **Prompt content quality** — Workstream A reviewed *consistency* and *goal-fit*; it did not deeply review whether the math in the prompts is accurate or whether examples are good.
- **Library subagent prompts** in `DEFAULT_LIBRARY/subagents/` — not audited.

---

## §8. Cross-references

- This audit supersedes `ISSUES.md` for findings: S2.2 (escalation tier model) — now N/A because env-pinning makes `model=` decorative.
- This audit reaffirms: S1.4 (resolver doesn't fire on empty success — partially mitigated by contract-breach repair), S2.1 (closing gates), S2.3 (rate-limit retry semantics), S2.4 (retry counter never resets).
- This audit adds: S0.1, S0.2, S0.3, S1.3, S1.4, S1.5, S1.6, S1.7, S1.8, S2.1, S2.5, S3.1, S3.2.

See `PROPOSED_FIXES.md` for minimal-diff proposals organized by severity.
