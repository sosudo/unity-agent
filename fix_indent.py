import re
from pathlib import Path

p = Path("/home/engine/project/unity_agent/pipeline.py")
content = p.read_text()

def fix_formalization_indent(match):
    indent = match.group(1)
    call = match.group(2)
    # The call is something like _audit_worktree_commits(...)
    # We want to replace it with:
    # audit_results = call
    # if ...:
    #     raise ...
    return f'{indent}audit_results = {call}\n{indent}if worktree_assignments and not any(r["committed"] for r in audit_results.values()):\n{indent}    raise RuntimeError("Formalization phase failed to commit any changes to worktrees.")'

# Match _audit_worktree_commits call and its indentation
content = re.sub(r'^( +)(_audit_worktree_commits\(worktree_assignments, project_path, (?:_main_branch|main_branch)\))', 
                 fix_formalization_indent, 
                 content, 
                 flags=re.MULTILINE)

# Also fix the one that might already have audit_results = 
def fix_formalization_indent_with_assign(match):
    indent = match.group(1)
    return f'{indent}audit_results = {match.group(2)}\n{indent}if worktree_assignments and not any(r["committed"] for r in audit_results.values()):\n{indent}    raise RuntimeError("Formalization phase failed to commit any changes to worktrees.")'

# This handles if I already replaced it once but with bad indentation
content = re.sub(r'^( +)audit_results = (_audit_worktree_commits\(worktree_assignments, project_path, (?:_main_branch|main_branch)\))\n\s+if worktree_assignments and not any\(r\["committed"\] for r in audit_results.values\(\)\):\n\s+raise RuntimeError\("Formalization phase failed to commit any changes to worktrees."\)',
                 fix_formalization_indent_with_assign,
                 content,
                 flags=re.MULTILINE)

p.write_text(content)
