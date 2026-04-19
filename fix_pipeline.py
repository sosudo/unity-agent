import re
from pathlib import Path

path = Path("/home/engine/project/unity_agent/pipeline.py")
content = path.read_text()

# S1.1 & S1.2: Outer-loop fail-open
content = content.replace(
    """        except FileNotFoundError:
            logging.warning("No VALIDATION_REPORT.md found — proceeding anyway.")
            break""",
    """        except FileNotFoundError:
            logging.error("No VALIDATION_REPORT.md found — retrying validation loop.")
            validation_iteration += 1
            continue"""
)

content = content.replace(
    """            except FileNotFoundError:
                logging.warning("No VALIDATION_REPORT.md found — proceeding anyway.")
                break""",
    """            except FileNotFoundError:
                logging.error("No VALIDATION_REPORT.md found — retrying validation loop.")
                validation_iteration += 1
                continue"""
)

content = content.replace(
    """        except FileNotFoundError:
            logging.warning("No REPORT.md found after critic phase — stopping loop.")
            break""",
    """        except FileNotFoundError:
            logging.error("No REPORT.md found after critic phase — retrying critic loop.")
            critic_iteration += 1
            continue"""
)

# S1.4 & S1.5: Inner-loop "success" suppression

def add_check(content, log_msg, check_code):
    def repl(match):
        indent = match.group(1)
        indented_check = "\n".join(indent + line for line in check_code.splitlines())
        return indented_check + "\n" + match.group(0)
    
    return re.sub(r'( +)' + re.escape(log_msg), repl, content)

# 1. Generation
gen_check = """if not (Path("language/chunks").exists() and list(Path("language/chunks").glob("*.json"))):
    raise FileNotFoundError("Generation phase failed to produce any chunk JSON files in language/chunks/")"""
content = add_check(content, 'logging.info("Generation phase completed successfully!")', gen_check)

# 2. Validation
val_check = """if not Path("VALIDATION_REPORT.md").exists():
    raise FileNotFoundError("Validation phase failed to produce VALIDATION_REPORT.md")"""
content = add_check(content, 'logging.info("Validation phase completed successfully!")', val_check)

# 3. Semiformalization
semi_check = """if not (Path("semiformal/chunks").exists() and list(Path("semiformal/chunks").glob("*.json"))):
    raise FileNotFoundError("Semiformalization phase failed to produce any chunk JSON files in semiformal/chunks/")"""
content = add_check(content, 'logging.info("Semiformalization phase completed successfully!")', semi_check)

# 4. Exploration
expl_check = """if not (Path("gathered").exists() and any(Path("gathered").iterdir())):
    raise FileNotFoundError("Exploration phase failed to produce any gathered content in gathered/")"""
content = add_check(content, 'logging.info("Exploration phase completed successfully!")', expl_check)
content = add_check(content, 'logging.info("Exploration phase (rerun) completed successfully!")', expl_check)

# 5. Critic
critic_check = """if not Path("REPORT.md").exists():
    raise FileNotFoundError("Critic phase failed to produce REPORT.md")"""
content = add_check(content, 'logging.info("Critic phase completed successfully!")', critic_check)

# 6. Formalization
content = content.replace(
    'audit_results = _audit_worktree_commits(worktree_assignments, project_path, main_branch)',
    'audit_results = _audit_worktree_commits(worktree_assignments, project_path, main_branch)\n                if worktree_assignments and not any(r["committed"] for r in audit_results.values()):\n                    raise RuntimeError("Formalization phase failed to commit any changes to worktrees.")'
)
content = content.replace(
    '_audit_worktree_commits(worktree_assignments, project_path, _main_branch)',
    'audit_results = _audit_worktree_commits(worktree_assignments, project_path, _main_branch)\n                    if worktree_assignments and not any(r["committed"] for r in audit_results.values()):\n                        raise RuntimeError("Formalization phase failed to commit any changes to worktrees.")'
)

path.write_text(content)
