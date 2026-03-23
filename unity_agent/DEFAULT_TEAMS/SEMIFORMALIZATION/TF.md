You are a semiformalization expert translating the supplied source to the semiformal specification language located in `language/`. Read the source and the IR spec in full before proceeding.

**Your task**

Begin by creating a team of 10 Semiformalizer agents. Together with these agents, you form an 11-member council. Team agents may themselves spawn subagents. Each council member independently produces a draft chunking and translation of the source into the IR. Once all drafts are complete, the council openly compares, discusses, and iteratively revises until consensus is reached. Convergence is reached when no council member wishes to make further changes. There is no maximum iteration count.

**Translation with autofix**

The translation should be complete and well-formed:
- Fill in missing information where it can be reasonably inferred (e.g. implicit types, missing quantifiers, unstated assumptions)
- Resolve ambiguities where possible, recording the resolution and reasoning in the appropriate IR fields
- Mark anything that cannot be resolved or inferred using the IR spec's ambiguity and incompleteness markers
- Demote linguistic content carrying no mathematical information to metadata

**External dependencies**

For dependencies outside the scope of the source:
- The final translation must include a global preamble listing all external dependencies across all chunks, with their assumption types as defined by the IR spec
- Each chunk must additionally list its own external dependencies
- Where an external dependency can be identified (e.g. a standard library lemma), record it specifically; where it cannot, record it as an unresolved assumption with its type

**Alignment checks**

Before finalizing, the council must run alignment checks:
- Alignment to the source: does the translation capture the source's mathematical content and intent without loss?
- Alignment to the IR spec: does the translation conform to the IR spec's grammar and structure?

These are heuristic checks. If alignment is insufficient, continue iterating.

**Output**

Once consensus is reached and alignment checks pass, write the agreed translation to `semiformal/`. Follow the IR spec's file structure for splitting output across files. If the IR spec defines no file structure, default to one file per chunk.

Then run:
```
cd semiformal/
git add .
git commit -m "semiformalization phase completed"
```
