# Unity Helper Scripts

Helper scripts for Lean 4 formalization tasks, available at `~/.unity/scripts/`.

Use `python3 ~/.unity/scripts/<script>.py` or `bash ~/.unity/scripts/<script>.sh`.

## sorry_analyzer.py

Extract all `sorry` placeholders from a Lean project with context (declaration name, file, line, goal type if available). Useful for surveying what remains to be proved.

```bash
python3 ~/.unity/scripts/sorry_analyzer.py . --format=json --report-only
python3 ~/.unity/scripts/sorry_analyzer.py . --format=text
```

## parse_lean_errors.py

Parse Lean compiler output into structured JSON with error type, location, and message. Useful for programmatic error handling or summarizing failures across multiple files.

```bash
python3 ~/.unity/scripts/parse_lean_errors.py error_output.txt
lake build 2>&1 | python3 ~/.unity/scripts/parse_lean_errors.py /dev/stdin
```

## search_mathlib.sh

Search for lemmas in the local Mathlib cache (`.lake/packages/mathlib/`) by keyword, with optional name-pattern filtering. Faster than web search for confirming a lemma exists locally.

```bash
bash ~/.unity/scripts/search_mathlib.sh "continuous compact" name
bash ~/.unity/scripts/search_mathlib.sh "measurable" type
```

## smart_search.sh

Multi-source lemma search: queries leansearch, loogle, and/or local Mathlib in one call. Aggregates results and deduplicates.

```bash
bash ~/.unity/scripts/smart_search.sh "description of goal" --source=leansearch
bash ~/.unity/scripts/smart_search.sh "description of goal" --source=all
```

## minimize_imports.py

Remove unused `import` statements from a Lean file. Run after formalization to keep files clean.

```bash
python3 ~/.unity/scripts/minimize_imports.py MyFile.lean
python3 ~/.unity/scripts/minimize_imports.py MyFile.lean --dry-run
```

## check_axioms_inline.sh

Verify which axioms each declaration in a file depends on. Useful for ensuring no unintended `sorry` or classical axioms remain.

```bash
bash ~/.unity/scripts/check_axioms_inline.sh MyFile.lean --report-only
bash ~/.unity/scripts/check_axioms_inline.sh MyFile.lean
```

## find_golfable.py

Identify proofs that may be unnecessarily long or have obvious simplification opportunities (long `simp` lists, redundant `have`s, etc.).

```bash
python3 ~/.unity/scripts/find_golfable.py MyFile.lean --filter-false-positives
```

## solver_cascade.py

Run a cascade of automated tactics against a set of goals and report which tactics succeed. Takes a JSON context file describing goals.

```bash
python3 ~/.unity/scripts/solver_cascade.py context.json MyFile.lean
```

## find_usages.sh

Find all locations in the project that reference a specific declaration.

```bash
bash ~/.unity/scripts/find_usages.sh theorem_name
bash ~/.unity/scripts/find_usages.sh MyModule.myLemma
```

## unused_declarations.sh

Find declarations in a directory that are never referenced anywhere in the project.

```bash
bash ~/.unity/scripts/unused_declarations.sh src/ --report-only
```

## find_instances.sh

Search for type class instances in the project and Mathlib cache.

```bash
bash ~/.unity/scripts/find_instances.sh "MeasurableSpace"
```

## analyze_let_usage.py

Analyze `let` bindings in Lean files for potential issues (unused bindings, shadowing, etc.).

```bash
python3 ~/.unity/scripts/analyze_let_usage.py MyFile.lean
```

## find_exact_candidates.py

Given a goal type, search for declarations whose type exactly matches or closely matches. Useful when `exact?` is too slow.

```bash
python3 ~/.unity/scripts/find_exact_candidates.py "Nat → Nat → Nat"
```

## try_exact_at_step.py

Attempt `exact` with a list of candidate lemma names at a specific proof position and report which succeed.

```bash
python3 ~/.unity/scripts/try_exact_at_step.py MyFile.lean 42 lemma1 lemma2 lemma3
```
