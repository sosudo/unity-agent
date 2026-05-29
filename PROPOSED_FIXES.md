# Unity Pipeline — Proposed Fixes (Workstream A)

Companion to `AUDIT.md`. Each entry references the finding ID from the audit, gives a minimal diff sketch, and notes risk. **No code changes have been applied.** Pick fix-by-fix; I'll implement what you approve.

Severity legend matches `AUDIT.md` (S0 = ships broken; S1 = silent harm; S2 = friction; S3 = cosmetic).

---

## S0-class — recommend before next real run

### Fix S0.1 — Resolve "ground truth" contradiction in `FORMALIZATION/T.md` and `SEMIFORMALIZATION/TT.md`

**Problem**: Same prompt file declares both "Lean project is the ground truth" and "Source is ground truth" — agents pick whichever they attend to.

**Proposal**: Choose one of these resolutions; I recommend **(a)**.

**(a) Source is authoritative for statements; Lean project for naming/style only** (recommended)
- Edit `unity_agent/PROMPTS/FORMALIZATION/T.md:43` — change "The Lean project is the ground truth — all formalization decisions must conform to it." → "The Lean project provides naming conventions, tactic style, and existing API to reuse. The source is the ground truth for statements and proof structure (see `**Source is ground truth**` below)."
- Edit `unity_agent/PROMPTS/SEMIFORMALIZATION/TT.md:26` — change "Lean is the ground truth; if the source conflicts with the existing Lean project, the Lean project wins" → "If a source result is already present in the Lean project, record it via `mathlib_refs` / external dependency tracking rather than re-deriving it. The source is authoritative for statements and proof structure; the existing project supplies the API and tactic style."
- Mirror identical edits in `DEFAULT_PROMPTS/`.

**(b) Lean is authoritative when conflict; record deviation explicitly**
- Same locations, but instruct the agent to add a `formalization_note` field on the chunk recording any source-vs-Lean deviation so retrospective can audit.

**Risk**: Low — prompt-only edit. Verify no critic check depends on the old wording.

---

### Fix S0.2 — Signal PROVE mode to subagents

**Problem**: PROVE-mode loosens proof-faithfulness to "proof-completion"; subagents (especially `DECLARATIONFORMALIZER` which is *not* swapped between modes) don't see the mode flag.

**Proposal — two parts:**

**(a) In-prompt mode header.** Prepend to `unity_agent/PROMPTS/PROVE/FORMALIZATION/F.md`, `T.md`, `STRATEGY.md`, `ESCALATION.md`, `SEMIFORMALIZATION/{FF,TF,TT}.md`, `GENERATION.md`:

```
**Mode: PROVE.** You are in proof-completion mode. Statements remain source-faithful; **proof structure is not bound to the source — any correct proof is acceptable.** Subagents you spawn inherit this mode.
```

Mirror to `unity_agent/PROMPTS/FORMALIZATION/F.md`, `T.md`, `SEMIFORMALIZATION/{FF,TF,TT}.md`, `GENERATION.md` (the non-PROVE versions) with the opposite text: *"Mode: NORMAL. Statements and proof structure both remain source-faithful — transcribe, do not re-derive."*

**(b) Swap the DECLARATIONFORMALIZER subagent under PROVE.** Currently `pipeline.py:2018, 2836, 2929, 3028, 3084, 2109, 3094, 1501` read `_SUBAGENTS_DIR / "FORMALIZATION/DECLARATIONFORMALIZER/{F,T}.md"` unconditionally. Either:
  - (i) Add `PROVE/FORMALIZATION/DECLARATIONFORMALIZER/{F,T}.md` to `SUBAGENTS/`, mirror to `DEFAULT_SUBAGENTS/`, and change `_SUBAGENTS_DIR / "FORMALIZATION/…"` → `ACTIVE_SUBAGENTS_DIR / "FORMALIZATION/…"` at those sites; OR
  - (ii) If the declaration formalizer should remain mode-agnostic, document that explicitly in its prompt.

**Risk**: Low for (a); (b)(i) requires creating new prompt files (will need your content guidance).

---

### Fix S0.3 — Bandit dead code: decide implement vs. remove

**Problem**: 100 lines of `_default_bandit_state`, `_load_bandit_state`, `_save_bandit_state`, `_resolve_escalation_outcomes`, `_stagnant_chunks` infrastructure tracks per-chunk `last_escalation.success`, but `_run_escalation_phase` hardcodes `tier = "B"`. The docstring claims a Beta(α,β) bandit.

**Proposal — two options:**

**(a) Remove the dead bandit framing.** Recommended unless you want a real bandit.
- `pipeline.py:1450-1452` — strip "Tier selection uses a Beta(α,β) bandit…" from docstring.
- `pipeline.py:739-749` — delete `_resolve_escalation_outcomes` (writes `success` field that's never read).
- `pipeline.py:741-749` reference: drop `last_escalation.success` from state schema.
- Rename `_load_bandit_state` → `_load_escalation_state`, same for `_save_bandit_state`, `_default_bandit_state`, `bandit_state.json` → `escalation_state.json`.
- Keep `_stagnant_chunks` and stagnation counter — they're load-bearing.
- Delete the `tier = "B"` line at `pipeline.py:1489` and references to `tier` in cost attribution (`pipeline.py:1576-1577`) and logging (only kept tier="B" cost separately to track against `SECONDARY_BUDGET` — the secondary-budget tracking IS used, so keep `secondary_spend` accounting but drop the "tier" naming).

**(b) Implement the bandit for real.** Reintroduce a primary-tier path with `model="opus", env=_primary_env` and use `last_escalation.success` rate per chunk to choose primary vs. secondary. More code, but answers the user's "model escalation should work" complaint properly.

**Risk**: (a) is purely subtractive; (b) is real work.

---

### Fix S0.4 — Resolver fail-open on empty success

**Problem**: Contract-breach one-shot repair was added (good) but uses the same agent context. If the agent silently ended the phase, re-prompting in the same session may not unstick it.

**Proposal**: Two-tier repair.

```python
# at each contract-breach site (pipeline.py:1873, 1916, 2147, 2352, 2394, 3067, 3123)
# replace the inline _query_with_idle_timeout(...) one-shot repair with:
if not Path("VALIDATION_REPORT.md").exists():
    logging.warning("[validation] contract breach: VALIDATION_REPORT.md missing — invoking resolver")
    await _invoke_resolver(
        "validation",
        Exception("Phase ended without writing VALIDATION_REPORT.md"),
        ctx={"missing_artifact": "VALIDATION_REPORT.md"},
    )
    # outer while-True retries the full phase with a fresh query
```

i.e. route empty-success through `_invoke_resolver` (fresh session, fresh prompt) and `continue` the outer loop instead of trying the same agent again.

**Risk**: Changes loop control flow; needs care so we don't double-resolve. Test with a deliberately-broken validator subagent.

**Companion** — add closing gates to prompts (see Fix S2.4 below).

---

## S1-class — recommend during cleanup pass

### Fix S1.1 — Reset resolver retry counter on successful phase completion

**Diff** at `pipeline.py:1359` and at each phase-success site:

```python
# pipeline.py: track success per phase to reset counter
def _phase_succeeded(phase_name: str) -> None:
    _retries.pop(phase_name, None)
```

Then call `_phase_succeeded("source-scan")` after `logging.info("Source scan phase completed successfully!")` and similar after each phase's successful completion (about 12 sites across Path 1/2/3).

**Risk**: Low; pure bookkeeping.

---

### Fix S1.2 — Bound rate-limit retries; cap retry-after

**Diff** at `pipeline.py:1378-1387`:

```python
if _RATE_LIMIT_PATTERN.search(err_str):
    wait = 60
    m = re.search(r"retry.after\s+(\d+)", err_str, re.IGNORECASE) or re.search(r"reset.in\s+(\d+)", err_str, re.IGNORECASE)
    if m:
        wait = min(int(m.group(1)), 600)  # cap at 10 min
    rate_limit_attempts = _rate_limit_retries.get(phase_name, 0) + 1
    _rate_limit_retries[phase_name] = rate_limit_attempts
    if rate_limit_attempts > 5:
        logging.critical(f"Sticky rate limit on phase '{phase_name}' — {rate_limit_attempts} retries. Giving up.")
        exit(1)
    logging.warning(f"Rate limit detected — sleeping {wait}s (attempt {rate_limit_attempts}/5).")
    await asyncio.sleep(wait)
    return
```

(Reset `_rate_limit_retries[phase_name]` on successful phase completion via Fix S1.1.)

**Risk**: Low; bounds previously-unbounded behavior.

---

### Fix S1.3 — `_assert_lsp_alive` should attempt restart, not exit

**Diff** at `pipeline.py:1146-1160`:

```python
def _assert_lsp_alive(phase: str) -> None:
    if _lean_lsp_proc is None or _lean_lsp_proc.poll() is None:
        return
    logging.warning(f"lean-lsp-mcp not alive before phase '{phase}' (exit={_lean_lsp_proc.returncode}) — attempting restart")
    try:
        _restart_lean_lsp_mcp()
    except Exception as e:
        logging.critical(f"CRITICAL: LSP restart failed before phase '{phase}': {e}")
        exit(1)
```

**Risk**: Low; reuses existing restart logic.

---

### Fix S1.4 — Worktree rescue: keep dirty worktree on rescue failure

**Diff** at `pipeline.py:454-466` (`_cleanup_worktree`):

Add a sentinel return from `_audit_worktree_commits` indicating rescue-failure chunks, and skip cleanup for those in the cleanup-loop call sites. Concretely:

```python
def _audit_worktree_commits(...) -> dict:
    ...
    report[chunk_id] = {"committed": committed, "merged": merged, "dirty": dirty, "rescue_failed": False}
    # in the rescue-failure branches at :426-434, set "rescue_failed": True
    ...

# at each cleanup site (pipeline.py:2078, 2898, 2989, 1568):
audit = _audit_worktree_commits(worktree_assignments, project_path, _main_branch)
for cid, wt in worktree_assignments.items():
    if audit.get(cid, {}).get("rescue_failed"):
        logging.error(f"[cleanup] preserving worktree {wt} for chunk {cid} — rescue commit failed; manual triage needed")
        continue
    _cleanup_worktree(Path(wt), project_path, cid)
```

**Risk**: Low; preserves on-disk state for forensics. Worktrees stay in `.gitignore`.

---

### Fix S1.5 — Defensive defaults for `MAX_VALIDATION_ITERATIONS`, `RESOLVER_MAX_RETRIES`, `MAX_LSP_RESTARTS_BEFORE_DEGRADE`

**Diff** at `pipeline.py:968-976`:

```python
max_critic_iterations = parse_int(os.getenv("MAX_CRITIC_ITERATIONS")) or 3
max_validation_iterations = parse_int(os.getenv("MAX_VALIDATION_ITERATIONS")) or 3       # was None
resolver_max_retries = parse_int(os.getenv("RESOLVER_MAX_RETRIES")) or 8                  # was None — read inside _invoke_resolver
max_lsp_restarts = parse_int(os.getenv("MAX_LSP_RESTARTS_BEFORE_DEGRADE")) or 2           # already had `if None: 2` — keep consistent
```

Then change `pipeline.py:1363` to read the closure-captured `resolver_max_retries` instead of re-reading the env var.

Update `.env.example` to document the new defaults: "leave empty for default (3 / 8 / 2)".

**Risk**: Behavioral change for users with `=None` in `.env`. Document in changelog.

---

### Fix S1.6 — Wire `PREPARATION_BUDGET` or remove it

**Diff**:
- Grep confirms `preparation_budget` is read (`pipeline.py:960`), logged (`:1024`), and never passed as `max_budget_usd`. There is no "preparation" phase code.
- **Recommendation: remove.** Delete `pipeline.py:960` and `:1024` and the line from `.env.example`. If you want a "preparation" phase later, add it then.

**Risk**: Zero. Pure dead-config removal.

---

### Fix S1.7 — Delete six stale forum directories

**Diff (filesystem)**: `rm -rf forum forum2 forum3 forum_c forum_sards a_forum` — all gitignored, none code-referenced.

**Risk**: Zero except your own desire to keep run archives. Suggest moving to `_archive_runs/` instead of deleting if you want history.

---

### Fix S1.8 — Move tool-naming reminder from runtime-append to prompts

**Diff** at `pipeline.py:1344-1356`:

Remove the runtime-append of the tool-naming paragraph in `with_library()`. Instead, add a short "Forum tool naming" block to each prompt that calls forum tools (Generation, Validation, Semiformalization variants, Exploration variants, Formalization variants, Critic) — single source of truth.

**Risk**: Low. Mirror to DEFAULT_PROMPTS/. Quick win for clarity.

---

### Fix S1.9 — Stagnation tracker fallback when chunk metadata is missing

**Problem**: `_chunk_body_signatures` returns `{}` when `lean_declaration` / `dag.json` are absent (Path 3, partial runs). Escalation then never fires even though sorries exist.

**Diff** at `pipeline.py:1456-1470`:

```python
current_sigs = _chunk_body_signatures(Path.cwd(), project_path)
if not current_sigs:
    # Fallback: track per-file sorry counts as stand-in for chunks
    file_sigs = {}
    for p in project_path.rglob("*.lean"):
        if any(part in (".lake", "lake-packages", "build") for part in p.parts):
            continue
        try:
            stripped = _strip_lean_comments(p.read_text())
            sorry_count = len(re.findall(r"\bsorry\b", stripped))
            if sorry_count:
                h = hashlib.sha256(stripped.encode("utf-8", "replace")).hexdigest()[:16]
                file_sigs[str(p.relative_to(project_path))] = (h, True)
        except Exception:
            continue
    if file_sigs:
        current_sigs = file_sigs
```

**Risk**: Medium. Changes escalation semantics for Path 3. Test with a known-stagnant project.

---

### Fix S1.10 — Call escalation in Path 3

**Diff** at `pipeline.py:1763` (after Path 3 critic-loop status check):

Mirror the Path 1 hook (`pipeline.py:3193-3196`): call `await _run_escalation_phase(iteration, None)` after critic returns NEEDS_REVISION. Requires Fix S1.9 (stagnation fallback) to function.

**Risk**: Low; isolated addition.

---

### Fix S1.11 — `_commit_phase` should log commit failures

**Diff** at `pipeline.py:86-90`:

```python
try:
    subprocess.run(["git", "add", "-A"], check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", msg, "--allow-empty"], check=True, capture_output=True)
except subprocess.CalledProcessError as e:
    logging.warning(
        f"_commit_phase('{phase_name}') failed (rc={e.returncode}): "
        f"{e.stderr.decode('utf-8', errors='replace').strip() if e.stderr else 'no stderr'}"
    )
```

**Risk**: Zero.

---

## S2-class

### Fix S2.1 — Robust `.format()` for recursive-unity and retrospective prompts

Use `string.Template` (`$depth`, `$child_depth`, `$SOURCE_PATH`, etc.) or escape literal braces in the markdown. `string.Template` is safer because it ignores unmatched `{`.

**Diff** in `RECURSIVE/UNITY.md`, `RETROSPECTIVE.md`: change `{depth}` → `$depth` etc. At `pipeline.py:1336`, replace `f.read().format(...)` with `string.Template(f.read()).safe_substitute(...)`.

**Risk**: Low. `safe_substitute` is the standard idiom.

---

### Fix S2.2 — Remove duplicate `worktree_assignments` annotations

**Diff** at `pipeline.py:2007, 2824, 2917`: declare `worktree_assignments: dict[str, str] = {}` once at the top of each function-scoped section, drop the re-annotations.

**Risk**: Zero.

---

### Fix S2.3 — Source scan: use `_PROMPTS_DIR` (current) or `ACTIVE_PROMPTS_DIR`?

Decision needed. If PROVE-with-source ever needs a different source-scan, switch `pipeline.py:2262` to `ACTIVE_PROMPTS_DIR`. Otherwise, document at the call site that source-scan is intentionally mode-agnostic.

**Risk**: Zero. Documentation only unless you want the variant.

---

### Fix S2.4 — Add closing gates to all phase prompts

**Diff**: For each prompt below, append a short closing-gate block matching the SOURCE_SCAN.md style.

| Prompt file | Required artifact + check |
|---|---|
| `GENERATION.md` | `language/chunks/*.json` non-empty + `language/chunk-schema.json` present |
| `VALIDATION.md` | `VALIDATION_REPORT.md` exists + contains `**Status:** {VALID,INVALID}` line |
| `SEMIFORMALIZATION/{FF,TF,TT}.md` | `semiformal/chunks/*.json` IDs match `language/chunks/*.json` |
| `EXPLORATION/{FF,FT,TF,TT}.md` | `semiformal/` mutated OR explicit no-op rationale posted to forum |
| `FORMALIZATION/{F,T,ESCALATION,STRATEGY}.md` | each `worktrees.json` entry has either a committed branch OR an orchestrator-merged commit |
| `CRITIC.md`, `CRITIC_STRATEGY.md` | `REPORT.md` in unity run dir (NOT project_path) with `**Status:**` line |
| `RETROSPECTIVE.md` | best-effort; verify library writes if performed |

Mirror to `DEFAULT_PROMPTS/` and (where applicable) `TEAMS/`.

**Risk**: Low. Pairs with Fix S0.4 (resolver-driven repair) — both layers protect.

---

### Fix S2.5 — Skip forum web UI when silent or non-interactive

**Diff** at `pipeline.py:1308-1320`:

```python
forum_web_disabled = parse_bool(os.getenv("DISABLE_FORUM_WEB")) or silent
if not forum_web_disabled:
    forum_dir.mkdir(exist_ok=True)
    _forum_web = subprocess.Popen([...])
    atexit.register(_forum_web.terminate)
    logging.info(f"Forum web UI: http://localhost:{forum_port}")
else:
    forum_dir.mkdir(exist_ok=True)
    logging.info("Forum web UI disabled (silent/headless).")
```

Add `DISABLE_FORUM_WEB` to `.env.example`.

**Risk**: Low. Important for recursive-unity child runs (current port-collision risk).

---

### Fix S2.6 — Delete orphan `PROMPTS/RECURSIVE/UNITY.md` and `DEFAULT_PROMPTS/RECURSIVE/UNITY.md`

The recursive-unity *subagent* prompt is at `SUBAGENTS/RECURSIVE/UNITY.md` and is loaded. The top-level `PROMPTS/RECURSIVE/UNITY.md` is never read.

**Diff**: `rm -rf unity_agent/PROMPTS/RECURSIVE unity_agent/DEFAULT_PROMPTS/RECURSIVE` (if confirmed never used).

**Risk**: Zero; verify with a grep before deletion.

---

### Fix S2.7 — Reconcile FORUM_PORT / LEAN_LSP_PORT defaults

Current state:
- `.env.example`: FORUM_PORT=6367, LEAN_LSP_PORT=6368
- shipped `.env`: FORUM_PORT=8080, LEAN_LSP_PORT=8888
- `pipeline.py:970-971`: defaults 8080, 6368
- `setup_cmd.py:125`: default 6367

**Proposal**: Pick canonical defaults (recommend 6367 / 6368) and update all four locations to match. Document why both ports exist (forum web UI vs. lean-lsp-mcp).

**Risk**: Zero for new installs; existing users may need to re-edit `.env`.

---

### Fix S2.8 — `unity setup` interactive coverage gap

Add prompts to `setup_cmd.py` for: `SECONDARY_BUDGET` (the only budget that actually caps anything per Fix S0.3 discussion), `LEAN_LSP_PORT`, `SDK_MESSAGE_IDLE_TIMEOUT`, `MAX_LSP_RESTARTS_BEFORE_DEGRADE`. Group them under an "Advanced (press enter to accept default)" section.

**Risk**: Low. Pure UX; defaults preserve current behavior.

---

### Fix S2.9 — Drop `last_escalation.success` field (rolled into S0.3)

Subset of S0.3. If you implement the real bandit (S0.3(b)), keep this field. If you remove the dead bandit framing (S0.3(a)), drop this field.

---

### Fix S2.10 — Log silent LSP kill failures

**Diff** at `pipeline.py:1185-1193`:

```python
if _lean_lsp_proc is not None:
    try:
        _lean_lsp_proc.terminate()
        _lean_lsp_proc.wait(timeout=5)
    except Exception as term_err:
        logging.warning(f"_restart_lean_lsp_mcp: terminate failed ({term_err}); attempting SIGKILL")
        try:
            _lean_lsp_proc.kill()
            _lean_lsp_proc.wait(timeout=5)
        except Exception as kill_err:
            logging.error(f"_restart_lean_lsp_mcp: kill also failed ({kill_err}); zombie LSP likely")
```

**Risk**: Zero. Logging only.

---

## S3-class — cosmetic / cleanup

### Fix S3.1 — Strip bandit docstring lie (subset of S0.3a)

If you take S0.3(a), this is automatic. Otherwise: `pipeline.py:1450-1452` — replace docstring promise with "Currently runs all candidates on the secondary tier; bandit selection is not implemented."

### Fix S3.2 — `LIBRARY_SUBAGENTS` global → local

Pass through function arguments instead of module-level mutation. Larger refactor; only worth doing if you want to support multiple parallel pipelines per process (you don't currently).

**Risk**: Refactor noise. Skip unless cleaning up for SDK fork.

### Fix S3.3 — Workspace hygiene

`.gitignore` covers most stale artifacts. PDFs and `source` file are intentional (user references). No code action.

---

## Cross-cutting suggestion (not a single fix)

**Compress repetition in `run_pipeline`.** Almost every phase has the same structure: outer `while True:` → load prompt + subagents → `async for message in _query_with_idle_timeout(...): _log_agent_message(message)` → contract-breach check → `_commit_phase` → `break`. The bodies are 30–80 lines each, and there are ~12 of them. A helper like:

```python
async def _run_phase(
    *,
    name: str,
    prompt_path: Path,
    subagent_paths: dict[str, Path],
    user_prompt: str,
    expected_artifact: Callable[[], bool] | None = None,
    artifact_name: str = "",
    options_extra: dict | None = None,
):
    ...
```

would cut 1,000+ lines and force a uniform shape for the contract-breach gate, resolver routing, and `_commit_phase` call. **Not minimal** — flag this for a separate "pipeline.py refactor" workstream after the bug fixes are in.

---

## Order I'd apply these

1. **Fix S0.1** (ground-truth contradiction) — single edit, immediate goal alignment.
2. **Fix S1.5** (defensive defaults) — protects against unbounded loops on the next run.
3. **Fix S1.7** (delete forum cruft) + **Fix S2.6** (delete orphan PROMPTS/RECURSIVE/) + **Fix S1.6** (remove PREPARATION_BUDGET) — three pure deletions in one commit.
4. **Fix S1.11** (`_commit_phase` logging) + **Fix S2.10** (LSP kill logging) — two-line observability wins.
5. **Fix S0.3(a)** (strip dead bandit framing) — cleanup.
6. **Fix S2.7** (port reconciliation) — single source of truth.
7. **Fix S0.2** (PROVE mode signaling) — pairs with content review.
8. **Fix S0.4** (resolver-on-empty-success) + **Fix S2.4** (closing gates) — both layers, ship together.
9. **Fix S1.1**/**S1.2** (retry counter + rate-limit bounds) — control-flow change, test carefully.
10. **Fix S1.3** (LSP self-heal) + **Fix S1.4** (worktree preservation) — robustness.
11. **Fix S1.9**/**S1.10** (Path 3 escalation) — only if Path 3 stays alive.
12. **Fix S2.5** (silent mode skip forum web) — recursive-unity unblocker.

Once you tell me which to apply, I'll do them as small, reviewable commits.
