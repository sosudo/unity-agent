# Forum + IR-refinement — proposed fixes (Workstream B)

Companion to `FORUM_AUDIT.md`. Each entry references the finding ID from the audit, gives a minimal diff sketch, and notes risk. **No code changes have been applied yet.** Pick fix-by-fix; I'll implement what you approve.

Severity legend matches `AUDIT.md` (S0 = ships broken; S1 = silent harm / misleading mechanism; S2 = friction; S3 = cosmetic).

---

## S0-class — correctness bugs in `forum_mcp.py`

### Fix B1 — Lock-ordering deadlock between dimension propose and dimension vote

**Problem**: `forum_propose_dimension` acquires `_config_lock` → `_thread_lock(_dimensions)`. `forum_vote` on a `_dimensions` post acquires `_thread_lock` → `_config_lock` (via `_maybe_auto_approve`). Concurrent ops on the dimensions thread deadlock.

**Proposal**: Standardize ordering. Always acquire `_thread_lock` *before* `_config_lock`.

**Diff** at `unity_agent/forum_mcp.py:568-602` (inside `forum_propose_dimension`):

```python
# OLD: config_lock → thread_lock
with _config_lock():
    ...
    with _thread_lock(DIMENSIONS_THREAD):
        proposal_post = _forum_post_locked(...)
    ...

# NEW: thread_lock → config_lock (matching forum_vote's order)
# Ensure dimensions thread exists outside the config_lock (idempotent, atomic write)
if not _thread_path(DIMENSIONS_THREAD).exists():
    _save({
        "thread_id": DIMENSIONS_THREAD,
        "title": "Dimension Proposals",
        ...
    })
with _thread_lock(DIMENSIONS_THREAD):
    with _config_lock():
        config = _load_config()
        if name in config["dimensions"]["active"]:
            return {"status": "already_active", "name": name}
        if name in config["dimensions"]["pending"]:
            return {"status": "already_pending", "name": name}
        proposal_post = _forum_post_locked(DIMENSIONS_THREAD, proposed_by, ..., [])
        proposal_post_id = proposal_post["post_id"]
        config["dimensions"]["pending"][name] = {...}
        _save_config(config)
```

**Risk**: Low; reorders nested locks. The `_thread_path(_dimensions).exists()` check is moved outside both locks and is safe to race (multiple racers writing the same content is idempotent if `_thread_path` doesn't exist; if it exists the check passes for everyone).

---

### Fix B2 — Race in `_stamp_tag_on_post`

**Problem**: `_stamp_tag_on_post` (`forum_mcp.py:691-707`) reads each thread JSON *outside* the per-thread lock, then on match enters the lock and writes the *in-memory* `data` — losing any posts written between the read and the write.

**Diff**:

```python
def _stamp_tag_on_post(post_id: str, tag_name: str) -> None:
    """Add tag_name to the post's tags list in its thread file."""
    for path in FORUM_DIR.glob("*.json"):
        if path.name in ("balances.json", "config.json"):
            continue
        # Out-of-lock check (cheap filter — avoid acquiring lock for every thread)
        try:
            preview = json.loads(path.read_text())
            if not any(p["post_id"] == post_id for p in preview["posts"]):
                continue
        except Exception:
            continue
        # Re-read under lock to avoid the race
        thread_id = preview["thread_id"]
        with _thread_lock(thread_id):
            data = json.loads(path.read_text())  # ← re-read under lock
            for post in data["posts"]:
                if post["post_id"] == post_id:
                    tags = post.setdefault("tags", [])
                    if tag_name not in tags:
                        tags.append(tag_name)
                        path.write_text(json.dumps(data, indent=2))
                    return
```

**Risk**: Low; double-read but only when post_id matches (rare). Note: this also fixes the bug that previously wrote `data` (the older snapshot) which clobbers any posts added after the initial read.

---

### Fix B3 — Mention regex matches Lean code, polluting the ledger

**Problem**: `_MENTION_RE = re.compile(r'@([\w][\w-]*)')` (`forum_mcp.py:25`) matches `@MeasureTheory`, `@implicit`, `@instance` etc. when these appear in code snippets in post bodies. `_push_notification` (line 306) then creates a balance entry for the parsed pseudo-mention.

**Proposal — two options:**

**(a) Restrict the mention regex to a leading word boundary + space/start-of-line** (recommended). Most legit mentions are at sentence-start or after whitespace; almost no `@FooBar` inside `lemma foo := @MeasureTheory.bar` syntax has whitespace before it.

```python
# OLD
_MENTION_RE = re.compile(r'@([\w][\w-]*)')

# NEW: require start-of-string or whitespace before @
_MENTION_RE = re.compile(r'(?:^|\s)@([a-zA-Z][\w-]*)')
```

Plus: only push a notification if the mentioned name **already exists in the balance ledger** — agents who've never posted shouldn't get balance entries just from being mentioned. (Optional secondary guard.)

**(b) Skip the mention regex inside fenced code blocks.** More complex; parse markdown fences first. Recommend (a) instead.

**Risk**: Low. Legit `@agent-name` mentions still match (whitespace-prefixed); `@MeasureTheory` no longer does inside code-like phrases.

**Cleanup**: should also offer a one-shot ledger-scrubbing script to remove obviously-non-agent entries from existing `balances.json`. See B3.cleanup below.

#### B3.cleanup — One-shot ledger sanitizer

Add `unity_agent/scripts/sanitize_forum_balances.py` (seeded to `~/.unity/scripts/` by the library seeder):

```python
"""Remove obviously-non-agent entries from forum/balances.json.

Drops keys that:
- Match common mathlib namespace prefixes (MeasureTheory, ContinuousLinearMap, ...)
- Are Python builtins/keywords (implicit, contextmanager, ...)
- Have balance == 0.0 AND history == [] (never actually credited)
"""
# minimal sketch; ~30 lines
```

**Risk**: Zero; opt-in cleanup script users run when they notice corruption.

---

### Fix B4 — Author casing fragmentation

**Problem**: `FORMALIZER` vs `Formalizer` vs `Formalizer-Subagent` are stored as distinct ledger keys.

**Proposal**: Normalize `author` server-side. Keep canonical original-cased version in posts (for display), but canonicalize for ledger keys.

```python
def _canonical_author(name: str) -> str:
    """Canonicalize author identity for ledger purposes: lowercase, strip suffixes like '-subagent', '-agent', collapse separators."""
    n = name.strip().lower()
    n = re.sub(r"[\s_-]+", "-", n)
    n = re.sub(r"-(subagent|agent|node|worker)$", "", n)
    return n

def _credit(author, delta, event, thread_id, excerpt=""):
    key = _canonical_author(author)
    balances = _load_balances()
    if key not in balances:
        balances[key] = {"balance": 0.0, "history": [], "notifications": [], "display_name": author}
    rec = balances[key]
    ...
```

Same change in `_push_notification`, `forum_check_balance`, `_notify_all`. The `display_name` is set on first credit and stays stable; subsequent posts under different casings credit the canonical key.

**Risk**: Medium. Changes the on-disk ledger schema. Existing archives have raw `author` keys; the sanitizer in B3.cleanup can also collapse case-variants. If you want zero migration cost, accept that old archives stay split and new runs become consistent.

---

### Fix B6 — `forum_set_dimensions` must not orphan votes

**Problem**: If `forum_set_dimensions` is called with a list that omits a previously-active dimension that already has cast votes, the votes-by-dimension data remains in posts but the dimension is no longer voteable. `forum_c/` archive's leaky-governance state plausibly came from exactly this.

**Diff** at `unity_agent/forum_mcp.py:536-552`:

```python
@mcp.tool()
def forum_set_dimensions(dimensions: list[str], allow_orphan: bool = False) -> dict:
    """Set the canonical vote dimensions for this run.
    
    If a previously-active dimension already has cast votes and isn't in the new list,
    rejection unless allow_orphan=True. The orphaned dimension stays voteable-as-stale
    in old posts but new votes use only the new active set.
    """
    for d in dimensions:
        if not _DIM_NAME_RE.match(d):
            raise ValueError(f"Invalid dimension name '{d}'.")
    with _config_lock():
        config = _load_config()
        prev = set(config["dimensions"]["active"])
        new = set(dimensions)
        removed = prev - new
        if removed and not allow_orphan:
            # Check if any removed dimension has cast votes
            for d in removed:
                if _dimension_has_votes(d):
                    raise ValueError(
                        f"Dimension '{d}' has cast votes; pass allow_orphan=True to override."
                    )
        config["dimensions"]["active"] = list(dimensions)
        _save_config(config)
    return {"active_dimensions": dimensions}
```

`_dimension_has_votes` scans threads briefly for `votes_by_dimension[d]` keys. Modest cost (run once at config change).

**Risk**: Low; backwards-compatible (default behavior changes from "silently orphan" to "require flag").

---

### Fix B7 — Validate `forum_tag` post_ids

**Problem**: `forum_tag` accepts `post_ids: ["..."]` literally without validating they're 8-char hex strings.

**Diff** at `unity_agent/forum_mcp.py:642-688` (inside `forum_tag`):

```python
_POST_ID_RE = re.compile(r"^[a-f0-9]{8}$")

@mcp.tool()
def forum_tag(name, post_ids, description="", tagger="unknown"):
    if not re.match(r'^[\w-]+$', name):
        raise ValueError(...)
    invalid = [pid for pid in post_ids if not _POST_ID_RE.match(pid)]
    if invalid:
        raise ValueError(
            f"Invalid post_ids (must be 8-char lowercase hex): {invalid}"
        )
    ...
```

**Risk**: Zero; rejects bad data at the boundary.

---

## S1-class — pretend-mechanism repairs

### Fix B8 — Enforce council convergence (make it a real gate)

**Problem**: SEMIFORMALIZATION/{FF,TF,TT}.md describes ACCEPT/OBJECT convergence. No orchestrator checks. Empirically: works when agents self-discipline (forum_sards), fails otherwise (forum_c).

**Proposal — two parts:**

**(a) Add a post-phase orchestrator check.** After the SEMIFORMALIZATION query returns and before `_commit_phase`, the pipeline scans the semiformalization thread for ACCEPT/OBJECT posts and raises if convergence isn't signaled.

```python
# in pipeline.py SEMIFORMALIZATION blocks (Path 1 FF/TF/TT, Path 2 TT)
# after the async for message loop and before _commit_phase:

if not _check_semiformal_convergence(Path.cwd()):
    raise FileNotFoundError(  # falls through to resolver
        "contract breach: SEMIFORMALIZATION council did not converge "
        "(no ACCEPT posts in semiformalization thread, or outstanding OBJECT posts); "
        "routing through resolver for fresh-session retry"
    )

def _check_semiformal_convergence(run_dir: Path) -> bool:
    thread_path = run_dir / "forum" / "semiformalization.json"
    if not thread_path.exists():
        return False
    data = json.loads(thread_path.read_text())
    posts = data.get("posts", [])
    if not posts:
        return False
    # Heuristic: look at the last N posts. ACCEPTs without intervening OBJECTs since the last "round" count.
    has_accept = any(p["content"].strip().startswith("ACCEPT") for p in posts[-10:])
    has_pending_object = any(p["content"].strip().startswith("OBJECT") for p in posts[-5:])
    return has_accept and not has_pending_object
```

**(b) Tighten the prompt** to make compliance more reliable. SEMIFORMALIZATION/{FF,TF,TT}.md and PROVE/SEMIFORMALIZATION/{FF,TF,TT}.md gain a new sentence in the closing gate: *"Before you end_turn, verify that the semiformalization forum thread contains at least one ACCEPT post and no unresolved OBJECT post. The pipeline checks this and re-runs the phase if convergence isn't visible."*

**Risk**: Medium. The heuristic check is fragile (ACCEPT/OBJECT may be embedded in longer messages). Could iterate the format if false-positives appear. Pairs well with Workstream A's S0.4 resolver-on-empty-success — same mechanism.

---

### Fix B9 — Either consume ICRL credit or remove the framing

The credit math is real, the hook is plumbed, no prompt acts on balance. Pick one:

**(a) Remove the ICRL framing.** Strip the `_forum_reward_hook` and the prompts' "ICRL" mentions. Less mechanism to maintain; the forum becomes a plain bulletin-board with optional voting.

**(b) Actually consume the credit.** A few non-trivial options:

- **Tie-breaker in escalation.** When `_stagnant_chunks` returns N candidates and `SECONDARY_BUDGET` constrains the total, escalate the chunk whose previous formalizer-agent has the highest ICRL balance (most recognized by peers). This adds one meaningful consumer that doesn't require new prompt work.
- **Layer assignment priority.** Same-layer chunks could be dispatched preferring high-balance subagents (if peer recognition correlates with quality). Requires balance lookup at dispatch time.
- **Forum-rule visibility.** Add to every prompt: *"Agents with balance below 0 must read forum_check_balance and prefix every post with a self-correction note acknowledging the pattern that led to downvotes."* This is in-context-RL-style behavior modification but observable.

Recommend (a) unless you're committed to making (b) work. The current state is the worst of both: code complexity for a behavior that doesn't differentiate runs.

**Risk for (a)**: Zero — pure removal of unused mechanism. **Risk for (b)**: Higher — depends on whether peer recognition correlates with chunk-formalization quality, which the existing archives don't really test.

---

### Fix B10 — Remove dimension propose/approve infrastructure

**Problem**: `forum_propose_dimension` + `forum_approve_dimension` + `_maybe_auto_approve` + `_dimensions` thread + pending dimensions config: 90+ lines of code, zero runtime usage.

**Diff**: Delete `forum_propose_dimension`, `forum_approve_dimension`, `_maybe_auto_approve`, and the pending-dimensions config branch. Keep `forum_set_dimensions` (used at config init).

If you want to preserve the mechanism for future use, add a comment marking it deferred. But the empirical evidence (0/6 archives) strongly suggests it's not needed.

**Risk**: Zero. Tools disappear from the MCP server — agents listing tools will see a smaller set. No prompts reference these tools by name (they appear only in tool-definition blocks, which are auto-generated from the MCP server).

---

### Fix B11 — Remove `forum_redact` or make it actually useful

Zero archive ever called it. Either:

**(a) Delete** the tool entirely. Simpler.

**(b) Repurpose** as `forum_archive(post_id, reason)` which moves the post to a separate archive thread instead of inline-redacting, and credits the redactor +0.5 for cleanup. This makes the mechanism observable and visible.

Recommend (a) unless you have a concrete future use case.

**Risk**: Zero for (a).

---

### Fix B12 — Tighten `forum_get_tag("decision")` flow into a gate

`forum_get_tag("decision")` is called by 7+ prompts at phase start. In 5/6 archives no agent ever wrote a `decision` tag. The retrieval returns nothing, the prompt continues anyway, and decisions don't carry across phases.

**Proposal — soft gate**: Add to GENERATION.md, SEMIFORMALIZATION/*, EXPLORATION/* closing gates: *"If your phase made any non-obvious cross-cutting decision (chunk boundary choice, IR grammar extension, exploration scope), you must post it to the global forum thread and tag the post via `forum_tag(name=\"decision\", post_ids=[your_post_id])`. Phases after yours read these tags to maintain coherence."*

And in the orchestrator, after each phase that *could* have made a decision, check that *at least one* `decision`-tagged post was added in this phase. Treat zero as a soft warning (logged, not halting): *"Phase X added 0 decision tags. Either nothing decision-worthy occurred, or coherence with downstream phases will rely on file artifacts alone."*

**Risk**: Low. Soft gate avoids over-blocking; gives observability into whether the mechanism is being used per-run.

---

## S2-class — observability and minor cleanup

### Fix F1 — Document the hot-score timescale

Add a docstring sentence to `_hot()`:

```python
def _hot(post: dict) -> float:
    """Hot score: signed log10(score) + timestamp/45000.
    
    The 45000-sec (12.5-hour) time term means for short-lived pipeline forums
    (sub-day), time dominates and 'hot' ≈ 'new'. For longer-lived forums,
    accumulated votes start to matter. Tune via forum_mcp HOT_TIME_SCALE if needed.
    """
```

Plus add `HOT_TIME_SCALE = 45000` as a module-level constant with the same documentation.

**Risk**: Zero. Docstring.

---

### Fix F2 — Drain notifications on `forum_check_balance` too

**Diff** at `unity_agent/forum_mcp.py:471-482`:

```python
@mcp.tool()
def forum_check_balance(author: str, drain: bool = True) -> dict:
    """Check your ICRL balance and full trajectory.
    
    By default, pending notifications are drained when returned (read-once semantics).
    Pass drain=False to peek without consuming.
    """
    balances = _load_balances()
    if author not in balances:
        return {"author": author, "balance": 0.0, "history": [], "notifications": []}
    rec = balances[author]
    result = {
        "author": author,
        "balance": rec["balance"],
        "history": rec["history"],
        "pending_notifications": list(rec.get("notifications", [])),
    }
    if drain and result["pending_notifications"]:
        rec["notifications"] = []
        _save_balances(balances)
    return result
```

**Risk**: Low. Default behavior change (drain becomes default); agents that polled would now consume notifications they previously kept seeing — generally an improvement.

---

### Fix F3 — Reject empty/None author

**Diff** at `unity_agent/forum_mcp.py:267-283` (top of `forum_post`):

```python
@mcp.tool()
def forum_post(thread_id, author, content, reply_to=None):
    if not author or not author.strip():
        raise ValueError("author must be a non-empty string")
    ...
```

Same in `forum_vote` (`voter` parameter), `forum_propose_dimension` (`proposed_by`).

**Risk**: Zero.

---

### Fix F5 — Document non-pruning + add a one-shot archive command

Forum dirs grow unbounded. Workstream A skipped deleting the 6 stale dirs per user request (kept as references). Document this:

Add to `forum_mcp.py` module docstring:

```python
"""Unity Forum MCP Server.

NOTE: This forum is per-run. Threads accumulate to forum/<thread_id>.json and
persist until the unity run dir is cleaned up. There is no automatic pruning.
For multi-run setups, archive each run's forum/ dir or use a separate --forum-dir
per run.
"""
```

No code change beyond docstring.

**Risk**: Zero.

---

### Fix F7 — Note trust assumption

Module docstring addition:

```python
"""...

SECURITY MODEL: This server has no authentication. Any client can post as any
author, vote with any voter, and redact any post. It's designed for a single
trusted Unity pipeline session; do not expose the port to untrusted clients.
"""
```

**Risk**: Zero.

---

## Order I'd apply these

The forum/IR audit's fixes group into three clusters with different review surfaces:

1. **Code-correctness cluster (small, mechanical, low-risk).** B1 (deadlock), B2 (race), B3 (mention regex), B4 (author casing — schema migration risk noted), B6 (orphan-vote check), B7 (post_id validation), F1/F2/F3/F5/F7. These are all in `forum_mcp.py`; can land as one commit, two if you want to split the schema-touching B4 from the rest.

2. **Pretend-mechanism removal cluster (subtractive, low-risk).** B10 (kill dimension propose/approve), B11(a) (kill redact), B9(a) (kill ICRL framing). All pure deletions of code that 0/6 archives ever exercised. One commit.

3. **Make-mechanisms-real cluster (additive, higher review).** B8 (enforce convergence with orchestrator check + prompt sharpening), B12 (decision-tag soft gate). These add real load-bearing gates and need design alignment with Workstream A's S0.4 resolver-on-empty-success pattern. Suggest a separate commit per fix so the prompt changes and the pipeline.py changes are reviewable independently.

Cluster 1 is the obvious first batch (concrete bugs). Cluster 2 is the obvious second batch (dead-code removal). Cluster 3 is the philosophical one — it pushes Unity toward forum-as-control-layer rather than forum-as-communication-layer, which is a design shift worth your explicit sign-off before I touch it.

Tell me which clusters to apply and in what order. If you want pure subtractive cleanup first (clusters 1 + 2 only), I can land those today and we can decide on Cluster 3 separately.
