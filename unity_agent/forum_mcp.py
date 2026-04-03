"""Unity Forum MCP Server.

File-backed forum for formalization agents. Each thread is stored as
forum/<thread_id>.json. Run via:

    python -m unity_agent.forum_mcp --forum-dir <path>
"""

import argparse
import fcntl
import json
import math
import re
import time
import uuid
from contextlib import contextmanager
from pathlib import Path

from fastmcp import FastMCP

mcp = FastMCP("unity-forum")
FORUM_DIR: Path = Path("forum")
_MENTION_RE = re.compile(r'@([\w][\w-]*)')


@contextmanager
def _thread_lock(thread_id: str):
    """Exclusive per-thread file lock so concurrent votes/posts are serialised."""
    FORUM_DIR.mkdir(parents=True, exist_ok=True)
    lock_path = FORUM_DIR / f"{thread_id}.lock"
    with open(lock_path, "w") as fh:
        fcntl.flock(fh, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(fh, fcntl.LOCK_UN)


# ── Internal helpers ──────────────────────────────────────────────────────────

def _thread_path(thread_id: str) -> Path:
    return FORUM_DIR / f"{thread_id}.json"


def _balances_path() -> Path:
    return FORUM_DIR / "balances.json"


def _load(thread_id: str) -> dict:
    path = _thread_path(thread_id)
    if not path.exists():
        raise ValueError(f"Thread '{thread_id}' does not exist. Call forum_create_thread first.")
    return json.loads(path.read_text())


def _save(data: dict) -> None:
    FORUM_DIR.mkdir(parents=True, exist_ok=True)
    _thread_path(data["thread_id"]).write_text(json.dumps(data, indent=2))


def _load_balances() -> dict:
    path = _balances_path()
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text())
    except Exception:
        return {}


def _save_balances(balances: dict) -> None:
    FORUM_DIR.mkdir(parents=True, exist_ok=True)
    _balances_path().write_text(json.dumps(balances, indent=2))


def _credit(author: str, delta: float, event: str, thread_id: str, excerpt: str = "") -> dict:
    """Credit delta to author's balance, append history entry. Returns updated author record."""
    balances = _load_balances()
    if author not in balances:
        balances[author] = {"balance": 0.0, "history": [], "notifications": []}
    rec = balances[author]
    rec["balance"] = round(rec["balance"] + delta, 2)
    rec["history"].append({
        "event": event,
        "delta": delta,
        "balance_after": rec["balance"],
        "thread_id": thread_id,
        "excerpt": excerpt,
        "timestamp": int(time.time()),
    })
    _save_balances(balances)
    return rec


def _push_notification(author: str, delta: float, event: str, thread_id: str, post_id: str, excerpt: str) -> None:
    """Queue a ±1 notification for author (received vote). Balance is credited here."""
    balances = _load_balances()
    if author not in balances:
        balances[author] = {"balance": 0.0, "history": [], "notifications": []}
    rec = balances[author]
    rec["balance"] = round(rec["balance"] + delta, 2)
    rec["history"].append({
        "event": event,
        "delta": delta,
        "balance_after": rec["balance"],
        "thread_id": thread_id,
        "post_id": post_id,
        "excerpt": excerpt,
        "timestamp": int(time.time()),
    })
    rec["notifications"].append({
        "delta": delta,
        "event": event,
        "thread_id": thread_id,
        "post_id": post_id,
        "excerpt": excerpt,
        "balance_after": rec["balance"],
    })
    _save_balances(balances)


def _drain_notifications(author: str) -> list:
    """Pop and return all pending notifications for author."""
    balances = _load_balances()
    if author not in balances:
        return []
    notifications = balances[author].get("notifications", [])
    balances[author]["notifications"] = []
    _save_balances(balances)
    return notifications


def _hot(post: dict) -> float:
    """Reddit hot-sort score: log10(max(|score|,1)) * sign(score) + timestamp/45000."""
    score = post["upvotes"] - post["downvotes"]
    sign = 1 if score > 0 else (-1 if score < 0 else 0)
    return math.log10(max(abs(score), 1)) * sign + post["timestamp"] / 45000


def _sorted_posts(posts: list[dict], sort: str) -> list[dict]:
    if sort == "hot":
        return sorted(posts, key=_hot, reverse=True)
    if sort == "new":
        return sorted(posts, key=lambda p: p["timestamp"], reverse=True)
    if sort == "top":
        return sorted(posts, key=lambda p: p["upvotes"] - p["downvotes"], reverse=True)
    raise ValueError("sort must be 'hot', 'new', or 'top'")


# ── Tools ─────────────────────────────────────────────────────────────────────

@mcp.tool()
def forum_create_thread(thread_id: str, title: str, description: str = "") -> str:
    """Create a new forum thread.

    Call once per chunk (thread_id='chunk-1', title=declaration name) and once
    for global cross-chunk discussion (thread_id='global', title='Global Discussion').
    Agents may create additional threads as needed. Returns a confirmation string.
    """
    if _thread_path(thread_id).exists():
        return f"Thread '{thread_id}' already exists."
    _save({
        "thread_id": thread_id,
        "title": title,
        "description": description,
        "created_at": int(time.time()),
        "posts": [],
    })
    return f"Thread '{thread_id}' created."


@mcp.tool()
def forum_post(
    thread_id: str,
    author: str,
    content: str,
    reply_to: str | None = None,
) -> dict:
    """Post a message to a forum thread.

    Returns the new post's metadata: post_id, author, timestamp, upvotes,
    downvotes, reply_to, icrl_balance, and any pending icrl_notifications
    (vote feedback received since your last post). Use post_id to vote on
    or redact this post later.
    """
    with _thread_lock(thread_id):
        return _forum_post_locked(thread_id, author, content, reply_to)


def _forum_post_locked(thread_id: str, author: str, content: str, reply_to: str | None) -> dict:
    data = _load(thread_id)
    post = {
        "post_id": uuid.uuid4().hex[:8],
        "author": author,
        "content": content,
        "timestamp": int(time.time()),
        "upvotes": 0,
        "downvotes": 0,
        "voter_registry": {},
        "reply_to": reply_to,
        "redacted": False,
    }
    data["posts"].append(post)
    _save(data)
    for m in _MENTION_RE.finditer(content):
        mentioned = m.group(1)
        if mentioned != author:
            _push_notification(mentioned, 0.0, "mention", thread_id, post["post_id"], content[:100])
    rec = _credit(author, 0.5, "forum_post", thread_id, content[:100])
    notifications = _drain_notifications(author)
    result = {k: v for k, v in post.items()}
    result["icrl_balance"] = rec["balance"]
    result["icrl_delta"] = +0.5
    if notifications:
        result["icrl_notifications"] = notifications
    return result


@mcp.tool()
def forum_vote(thread_id: str, post_id: str, vote: str, voter: str = "unknown") -> dict:
    """Vote on a post. vote must be 'up', 'down', or 'remove'. voter should be your agent name.

    Each voter may hold at most one vote per post at any time:
    - 'up' when you have no vote: adds an upvote.
    - 'up' when you already upvoted: removes your upvote (toggle off).
    - 'up' when you already downvoted: removes your downvote and adds an upvote.
    - 'down' when you have no vote: adds a downvote.
    - 'down' when you already downvoted: removes your downvote (toggle off).
    - 'down' when you already upvoted: removes your upvote and adds a downvote.
    - 'remove': removes your existing vote, no-op if you have none.

    Credits +0.5 to voter for any action that changes state. ICRL notifications
    to the post author reflect the net delta (including reversals).
    Returns updated vote counts, your current vote, and your icrl_balance.
    """
    if vote not in ("up", "down", "remove"):
        raise ValueError("vote must be 'up', 'down', or 'remove'")
    with _thread_lock(thread_id):
        return _forum_vote_locked(thread_id, post_id, vote, voter)


def _forum_vote_locked(thread_id: str, post_id: str, vote: str, voter: str) -> dict:
    data = _load(thread_id)
    for post in data["posts"]:
        if post["post_id"] == post_id:
            registry = post.setdefault("voter_registry", {})
            existing = registry.get(voter)  # "up", "down", or None
            excerpt = post["content"][:100]
            author = post["author"]

            # Compute what changes
            remove_old = existing is not None
            add_new = vote != "remove" and vote != existing

            if not remove_old and not add_new:
                # No-op: remove with no existing vote
                balances = _load_balances()
                balance = balances.get(voter, {}).get("balance", 0.0)
                return {
                    "post_id": post_id,
                    "upvotes": post["upvotes"],
                    "downvotes": post["downvotes"],
                    "your_vote": None,
                    "icrl_balance": balance,
                    "icrl_delta": 0,
                    "action": "no_op",
                }

            # Apply removals
            author_delta = 0.0
            if remove_old:
                if existing == "up":
                    post["upvotes"] -= 1
                    author_delta -= 1.0
                else:
                    post["downvotes"] -= 1
                    author_delta += 1.0
                del registry[voter]

            # Apply new vote
            if add_new:
                registry[voter] = vote
                if vote == "up":
                    post["upvotes"] += 1
                    author_delta += 1.0
                else:
                    post["downvotes"] += 1
                    author_delta -= 1.0

            _save(data)

            # Notify author of net delta
            if author_delta != 0.0:
                event = "received_upvote" if author_delta > 0 else "received_downvote"
                _push_notification(author, author_delta, event, thread_id, post_id, excerpt)

            current_vote = registry.get(voter)
            action = f"vote_{vote}" if add_new else f"removed_{existing}"
            rec = _credit(voter, 0.5, f"forum_{action}", thread_id)
            return {
                "post_id": post_id,
                "upvotes": post["upvotes"],
                "downvotes": post["downvotes"],
                "your_vote": current_vote,
                "icrl_balance": rec["balance"],
                "icrl_delta": +0.5,
                "action": action,
            }
    raise ValueError(f"Post '{post_id}' not found in thread '{thread_id}'.")


@mcp.tool()
def forum_redact(thread_id: str, post_id: str) -> str:
    """Mark a post as [REDACTED]. Posts are never deleted — only their content is hidden.

    Use this when a post is outdated or wrong rather than letting stale
    information mislead other agents.
    """
    with _thread_lock(thread_id):
        data = _load(thread_id)
        for post in data["posts"]:
            if post["post_id"] == post_id:
                post["redacted"] = True
                post["content"] = "[REDACTED]"
                _save(data)
                return f"Post '{post_id}' redacted."
    raise ValueError(f"Post '{post_id}' not found in thread '{thread_id}'.")


@mcp.tool()
def forum_read(thread_id: str, sort: str = "hot") -> dict:
    """Read a forum thread.

    sort can be:
    - 'hot'  (default): Reddit algorithm — log10(max(|score|,1)) * sign(score) + timestamp/45000
    - 'new':  newest posts first
    - 'top':  highest net score (upvotes − downvotes) first

    Returns thread metadata and the full list of posts in the requested order.
    """
    data = _load(thread_id)
    return {
        "thread_id": data["thread_id"],
        "title": data["title"],
        "description": data["description"],
        "created_at": data["created_at"],
        "post_count": len(data["posts"]),
        "posts": _sorted_posts(data["posts"], sort),
        "sort": sort,
    }


@mcp.tool()
def forum_check_balance(author: str) -> dict:
    """Check your ICRL balance and full trajectory.

    Returns current balance, full history of reward events (posts, votes cast,
    votes received), and any pending notifications not yet delivered via forum_post.
    """
    balances = _load_balances()
    if author not in balances:
        return {"author": author, "balance": 0.0, "history": [], "notifications": []}
    rec = balances[author]
    return {
        "author": author,
        "balance": rec["balance"],
        "history": rec["history"],
        "pending_notifications": rec.get("notifications", []),
    }


@mcp.tool()
def forum_list() -> list:
    """List all forum threads with summary metadata.

    Returns a list of {thread_id, title, description, post_count, last_activity}.
    """
    FORUM_DIR.mkdir(parents=True, exist_ok=True)
    threads = []
    for path in sorted(FORUM_DIR.glob("*.json")):
        if path.name == "balances.json":
            continue
        try:
            data = json.loads(path.read_text())
            last_activity = max(
                (p["timestamp"] for p in data["posts"]),
                default=data["created_at"],
            )
            threads.append({
                "thread_id": data["thread_id"],
                "title": data["title"],
                "description": data["description"],
                "post_count": len(data["posts"]),
                "last_activity": last_activity,
            })
        except Exception:
            continue
    # Append ICRL leaderboard summary
    balances = _load_balances()
    if balances:
        leaderboard = sorted(
            [{"author": a, "balance": r["balance"]} for a, r in balances.items()],
            key=lambda x: x["balance"],
            reverse=True,
        )
        threads.append({"thread_id": "_leaderboard", "leaderboard": leaderboard})
    return threads


# ── Entry point ───────────────────────────────────────────────────────────────

def main() -> None:
    global FORUM_DIR
    parser = argparse.ArgumentParser(description="Unity Forum MCP Server")
    parser.add_argument("--forum-dir", default="forum", help="Directory for forum thread files")
    args = parser.parse_args()
    FORUM_DIR = Path(args.forum_dir)
    FORUM_DIR.mkdir(parents=True, exist_ok=True)
    mcp.run()


if __name__ == "__main__":
    main()
