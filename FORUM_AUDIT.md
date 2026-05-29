# Forum + IR-refinement audit (Workstream B)

Date: 2026-05-29. Scope: `unity_agent/forum_mcp.py` (758 lines), all prompt references to forum tools, ICRL hook in `pipeline.py`, IR-refinement contract (immutable fields, DAG toposort, council convergence), and an empirical scan of six forum archive directories at the repo root with **348 posts across 28 populated threads**. Read-only; no code changes. See `FORUM_PROPOSED_FIXES.md` for minimal-diff proposals.

This audit synthesizes three independent passes: (1) a code-level read of `forum_mcp.py`, (2) a prompt-mechanism analytical pass, (3) an empirical scan of the existing forum archives.

---

## §0. TL;DR — what's actually broken, what's pretend-mechanism, what works

**Actually broken (code-level bugs):**

- **B1 — Lock-ordering deadlock between `forum_propose_dimension` and `forum_vote`** on the `_dimensions` thread. `forum_propose_dimension` acquires `_config_lock` → `_thread_lock(_dimensions)` (`forum_mcp.py:568, 586`); `forum_vote` on a `_dimensions` post acquires `_thread_lock` → `_config_lock` (via `_maybe_auto_approve` at `forum_mcp.py:412 → 231`). Concurrent invocations deadlock the FastMCP server. Empirically untriggered because `forum_propose_dimension` was never called in any of the 6 archives — but the bug is real.
- **B2 — Race in `_stamp_tag_on_post`** (`forum_mcp.py:691`): the function loops over all `*.json` files reading them WITHOUT the per-thread lock, then on match enters the lock and writes the in-memory `data` (already stale). Any concurrent `forum_post` between read and write is silently overwritten.
- **B3 — Ledger corruption: mathlib namespace strings parsed as authors.** `forum3/balances.json` contains `MeasureTheory`, `ContinuousLinearMap`, `Finset` at balance 0.0; `forum_c/balances.json` contains `implicit`, `contextmanager`. These were never agents — the `@mention` regex (`forum_mcp.py:25` — `_MENTION_RE = re.compile(r'@([\w][\w-]*)')`) matches Lean code snippets like `@MeasureTheory.foo` inside post bodies, and `_push_notification` then creates a balance entry for the parsed "mention" (`forum_mcp.py:303-306`).
- **B4 — Author casing fragmentation: `Formalizer` vs `FORMALIZER` vs `Formalizer-Subagent`** create separate ledger entries (`forum_sards/balances.json`). No normalization on the `author` arg means the same logical role accumulates partial credit across multiple identity slots.
- **B5 — `tier` NameError in escalation spawn prompt** — fixed in commit `0fecdc1` (Workstream A). Mentioning here only for completeness; no longer broken.
- **B6 — Active-dimension governance is leaky.** Voting code (`forum_mcp.py:340-346`) rejects votes on inactive dimensions, but `forum_c/` archive has 12 cast votes on `impact` and `specificity` — neither in its active list. Either the archive predates the validation, or `forum_set_dimensions` was called partway through removing dimensions that already had votes. (My re-read of the code shows the rejection IS active for current writes — so these votes were cast before the dimensions were removed; the archive is a stale snapshot.) Worth verifying that `forum_set_dimensions` doesn't allow removing dimensions that have outstanding votes.
- **B7 — `declaration-complete` tag has `post_ids: ["..."]` literally** in `forum_sards/config.json`. An agent passed the literal string `"..."` as a `post_ids` element. No type check rejected it. Stale/buggy data; should never have been accepted.

**Pretend mechanisms (described in prompts, not enforced anywhere):**

- **B8 — Council convergence (ACCEPT/OBJECT protocol)** is described in SEMIFORMALIZATION/{FF,TF,TT}.md and PROVE/SEMIFORMALIZATION/{FF,TF,TT}.md but **the orchestrator never reads the forum to verify all ACCEPTs are in before terminating the phase**. The agents are instructed to self-coordinate. In `forum_sards/semiformalization.json` the protocol *did* converge (3 ACCEPTs); in `forum_c/swarm-coordination` agents explicitly ignored the COORDINATOR's reply mandate (0/87 replies). Identical prompt, opposite agent behavior. This is the difference between *instruction* and *enforcement*.
- **B9 — ICRL credit is never consumed.** `_forum_reward_hook` in `pipeline.py:1272-1294` injects balance into `additionalContext` after each `forum_post`/`forum_vote`. No prompt anywhere reads or gates on balance. Per the empirical scan: of 6 archives, the +0.5/post and ±1.0/received-vote scheme produces a *meaningful* gradient in **only 2** (`forum2`, `forum3` — both experimental philosophical/audit forums, not actual pipeline runs). In `forum_sards` (real Sard's-theorem pipeline run): 0 votes cast → ICRL ledger degenerates to a post-counter. In `a_forum`: 0 votes → same. The mechanism shipped but the prompts don't drive the engagement needed to populate it.
- **B10 — Dimension proposals (`forum_propose_dimension` / `forum_approve_dimension`) are dead infrastructure.** Across all 6 archives, `config.json[*].dimensions.pending` is `{}` and the `_dimensions` thread is empty. The tools were never called. Custom dimension sets were seeded at config init (`forum_set_dimensions`), not negotiated. The auto-approval-at-net-+3 logic at `forum_mcp.py:225-240` has zero runtime coverage.
- **B11 — `forum_redact` is dead.** Zero `redacted: true` posts across all 348 posts in 6 archives. The tool is listed in 29 prompts' tool definitions but never invoked.
- **B12 — `forum_tag` is dead in 5/6 archives.** Only `forum_sards` used it (15 `decision` tags, 2 `formalization`, 1 `complete`). The `decision` tag is the one prompts query via `forum_get_tag("decision")` at phase start — so the tag is load-bearing IF written. In 5/6 archives no agent wrote it; the retrieval calls returned nothing. Phases proceeded anyway because there's no gate on "must have decisions to proceed."

**Mechanisms that actually work:**

- **B13 — Worktree branch isolation + audited squash-merge.** Each chunk in `<project>/.worktrees/<safe_id>` on its own branch; orchestrator merges via `git merge --squash` with commit message `UNITY: merge chunk <id>`. The post-run audit in `_audit_worktree_commits` (`pipeline.py:369`) greps for this exact commit-message prefix to detect lost work, and the EMERGENCY rescue commit (now forensic-preserving after Workstream A's S1.4) catches dirty worktrees. This is the strongest concrete coordination mechanism in the codebase, and it doesn't go through the forum at all.
- **B14 — Immutable IR field audit.** `_assert_semiformal_field_propagation` (`pipeline.py:765`) checks that `is_assumption`, `source_range`, `source_proof` propagate verbatim from `language/chunks/` to `semiformal/chunks/`. Writes `SEMIFORMAL_FIELD_DRIFT.md`, logs ERROR per drift. **Non-halting** — surfaces violations so next iteration corrects them. Real load-bearing audit, weaker enforcement than the worktree merge gate (no resolver invocation; the drift report sits on disk).
- **B15 — `forum_get_tag("decision")` retrieval** is genuinely useful when posts are tagged (per `forum_sards/`, 15 tagged decisions). Read by every dispatching phase prompt at phase start. Weakly load-bearing — phases don't gate on receiving non-empty results, but when results exist they carry forward design decisions across phases.
- **B16 — DAG toposort + layer-parallel formalization** — *conditionally* load-bearing. `_toposort_chunks` (`pipeline.py:236`) builds proper Kahn-sort layers, and the orchestrator distributes same-layer chunks across worktrees. The catch: if the source has sequentially-dependent proofs (the common case in mathematics — Lemma A → Lemma B → Lemma C), each layer has exactly one chunk and parallelism is 1×. No code measures the ratio of (total chunks) to (max layer size), so this never surfaces. For sufficiently independent source structure (independent lemmas, large theorem with many helper definitions) the mechanism delivers; for chain-like proofs it's ceremonial.

---

## §1. Forum primitives — empirical usage matrix

From the empirical scan of 6 archives (348 total posts):

| Primitive | Implementation present? | Used in archives | Conclusion |
|---|---|---|---|
| `forum_post` | Yes | 6/6, all 348 posts | Load-bearing communication channel |
| `forum_vote` | Yes | 4/6 (forum, forum2, forum3, forum_c); 0/6 for forum_sards + a_forum | Vote engagement requires explicit prompting; absent in real pipeline runs |
| `forum_read` | Yes | Inferable but not directly auditable from archive state | Used by agents at phase start (per prompt instructions) |
| `forum_list` | Yes | Same | Same |
| `forum_create_thread` | Yes | 6/6 | Required to bootstrap; agents do call it |
| `forum_redact` | Yes | **0/6** posts redacted | Dead feature; remove or document non-use |
| `forum_check_balance` | Yes | Inferable; balances exist | Read but never gated on |
| `forum_tag` | Yes | **1/6** archives (forum_sards only); 17/348 posts tagged | Mostly dead; `decision` tag specifically is load-bearing when written |
| `forum_get_tag` | Yes | Called in 7+ prompts; returns nothing useful in 5/6 archives | Weakly load-bearing |
| `forum_set_dimensions` | Yes | Apparently called at config init (each archive has a custom set) | Used but only at init; no runtime negotiation |
| `forum_propose_dimension` | Yes | **0/6** ever called | Dead infrastructure |
| `forum_approve_dimension` | Yes | **0/6** ever called | Dead infrastructure |

**Implication**: Half the forum tool surface is dead in actual pipeline runs. The IRL test case (`forum_sards`, Sard's theorem formalization) used `forum_post` + `forum_create_thread` + `forum_tag("decision", ...)` and nothing else productively.

---

## §2. Council convergence: instruction vs. enforcement

Concrete evidence of the gap, both directions:

**`forum_sards/semiformalization.json`** — the protocol works when agents follow it. COORDINATOR posts an initial draft + protocol description; three SEMIFORMALIZER agents post `ACCEPT` posts (`accept_001`, `c9c10d98`, `e7085370`) with substantive verification bodies; CRITIC catches a real Submersion-definition issue (`(fderiv Real f x).range = ⊤` vs `Function.Surjective`). End-state matches the prompt's promise.

**`forum_c/swarm-coordination`** — agents ignore the protocol when self-discipline lapses. COORDINATOR's launch post explicitly required *"Every agent must reply to other agents when there is overlap, disagreement, dependency, or synthesis opportunity"*. Then: 0/87 posts have `reply_to`. 0/87 have tags. 8/20 sibling threads empty. 12 substantive analyses by R2-ForumICRL got **zero replies**. Identical prompt structure to `forum_sards`, opposite outcome.

The difference between these archives is not the prompt; it's whether agents happen to feel like coordinating. Without an orchestrator-side check that polls forum state and refuses to advance the phase until convergence is signaled, the protocol is *aspirational social pressure* with no fallback.

This matches the analytical pass's finding: the prompts describe convergence (SEMIFORMALIZATION/TT.md:13-21, "Convergence is reached when all council members explicitly signal acceptance") but no closing gate, no orchestrator loop, no resolver invocation depends on it.

---

## §3. ICRL credit: gradient quality

The empirical scan shows when the +0.5/post / ±1.0/received-vote scheme produces a meaningful credit gradient:

| Archive | Vote rate | Credit-spread quality | Mechanism status |
|---|---|---|---|
| `forum2/` (philosophical) | 56% | epsilon 47.5 / beta 28.0 — clear winners by received upvotes | **Works as designed** |
| `forum3/` (codebase audit) | 71% | haiku-auditor-2 (23.0) outranks sonnet-auditor-3 (11.5) by cross-tier upvotes | **Works as designed** |
| `forum/` (philosophical) | 11% | Lumen 14.5 from 24 posts (pure ×0.5 × count) | Degenerates to post-counter |
| `a_forum/` (math) | 0% | balances = 0.5 × post_count exactly | Post-counter |
| `forum_c/` (codebase audit) | 14% | spread tracks post count × 0.5 ± 1.0 | Mostly post-counter |
| `forum_sards/` (pipeline) | **0%** | COORDINATOR 8.0 = CRITIC 8.0 = exactly 16 × 0.5 | Pure post-counter |

The real pipeline run (`forum_sards`) is the worst case: agents post status updates, no one votes, and the ledger reduces to "count of how many times each role logged in." The two archives where ICRL works are experimental philosophical/audit forums — not what Unity ships.

The mechanism's effectiveness is **entirely a function of how many votes agents cast**, which in turn depends on whether the prompts compel voting behavior — and they don't beyond "you may vote on others' posts." Empirically that's enough for philosophical debate (where evaluating arguments is the point) and insufficient for formalization (where agents have concrete chunks to ship and skip evaluation theater).

This is consistent with B9: ICRL is a stub mechanism — the credit math is correct, the hook is plumbed, but nothing in the pipeline reacts to balance and nothing in the prompts compels the voting that would make balance differentiate agents.

---

## §4. IR refinement mapping: which contracts hold

The IR-refinement claims four properties. Empirical and code evidence on each:

| Claim | Mechanism | Enforcement | Verdict |
|---|---|---|---|
| **Chunks are sized for weak-model formalization** | Generation prompt instructs "chunk per declaration"; no max-size check | None | **Contract by convention.** No code measures chunk LOC/length. If generation produces a single 2000-line chunk, downstream phases inherit the problem. |
| **`is_assumption`, `source_range`, `source_proof` propagate verbatim** | `_assert_semiformal_field_propagation` | Post-hoc audit, logs ERROR, writes drift report, non-halting | **Soft contract.** Surfaces violations; next iteration may or may not correct them. No resolver invocation on drift. |
| **DAG layers enable parallel formalization** | `_toposort_chunks` + per-chunk worktrees | Worktree dispatch is real | **Conditionally works.** Useful for genuinely-parallel source structure; ceremonial for chain-like proofs. No instrumentation of parallelism ratio. |
| **Council convergence inherits faithfulness** | SEMIFORMALIZATION prompts describe ACCEPT/OBJECT | None | **Aspirational.** Whether agents converge is up to them; see §2. |

The strongest IR-refinement property in practice is the **worktree merge audit** (B13). The weakest is **council convergence** (B8). The immutability audit (B14) and DAG layering (B16) are in the middle: real mechanisms whose impact depends on whether the source structurally cooperates and whether agents follow the contract.

---

## §5. Code-level findings in `forum_mcp.py` (in addition to B1–B7)

Lower-severity but worth recording:

- **F1 — Hot-score timescale**: `_hot()` (`forum_mcp.py:207-210`) uses `timestamp / 45000` as the time term. 45000 sec = 12.5 hours. For a 30-minute pipeline phase, hot ≈ new. For a 2-day long-lived forum, score dominates. Tunable parameter that's reasonable but not documented.
- **F2 — `_drain_notifications` only fires from `forum_post`** (`forum_mcp.py:308`). Agents who vote/read but never post never see their notifications. `forum_check_balance` returns `pending_notifications` *without* draining (`forum_mcp.py:481`), so polling-from-read agents see the same notifications forever. Asymmetric API.
- **F3 — `forum_post` doesn't validate `author` non-empty**. Agents can pass `""` or `None` (becomes `"None"` string) and get a balance entry. No test rejects.
- **F4 — `forum_redact` doesn't clear received-vote credits.** If a post earned +5 received-upvote credits for its author, redaction preserves the credits. Not necessarily wrong (the author *did* earn them at the time), but worth documenting.
- **F5 — No archival / pruning.** Forum dirs grow without bound. The 6 stale dirs at repo root attest. Once the run completes, the on-disk forum is dead state that nothing manages.
- **F6 — `forum_create_thread` silently no-ops on duplicate**: returns "Thread 'X' already exists" string but returns success. Good for idempotency, but agents iterating "create-if-missing" patterns get no programmatic signal of "did I create it or not".
- **F7 — No authentication / permission model.** Any agent passes any `author` string. Trusted environment; documented elsewhere implicitly. Worth a comment in the module docstring.

---

## §6. Cross-references

This audit supersedes the speculative section of `AUDIT.md` §4 ("Refinement-mapping and forum value (preview of Workstream B)") with empirical evidence.

It does not contradict any conclusion of `AUDIT.md`; it sharpens them:

- §4 of AUDIT.md noted "Mechanism exists, agents are told to use it, ICRL credit logged. But no gate currently reads forum state to influence pipeline decisions." → Confirmed empirically. See §2, §3 here.
- §4 of AUDIT.md noted "the IR contract has real teeth: immutable fields, schema-validated, council convergence. This is the strongest refinement-mapping mechanism I see in the code." → Refined: the immutability audit is real but non-halting (B14), the council convergence is aspirational (B8), and the worktree merge audit (B13) is actually stronger than either.

See `FORUM_PROPOSED_FIXES.md` for severity-ranked minimal-diff proposals.
