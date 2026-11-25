# Knowledge sources

Use what the repo and machine already have before reading source code.
Check once, save the result in `.reckon/sources.json`, and reuse it.

## Contents

- Priority order
- How to detect each source
- What to take from each
- Rules

## Priority order

1. **Repo docs**: AGENTS.md, CLAUDE.md, GEMINI.md,
   .github/copilot-instructions.md, README, ARCHITECTURE.md,
   CONTRIBUTING.md, docs/, ADR folders (adr/, docs/adr/, docs/decisions/),
   llms.txt.
2. **Existing code indexes**: codegraph (`.codegraph/`), graphify
   (`graphify-out/GRAPH_REPORT.md`, `graphify-out/graph.json`), ctags
   (`tags`), Serena (`.serena/`), and any code-intelligence or LSP tools
   the agent already has.
3. **Cheap CLIs**: `rg` (ripgrep), `ctags`, `ast-grep`/`sg`, `git log`,
   `git grep`.
4. **Manual reading**: list folders, then read only the files a module
   will touch, signatures first.

## How to detect each source

| Source | Check |
|---|---|
| docs | file or folder exists at the repo root |
| codegraph | `.codegraph/` exists; CLI `codegraph` on PATH, or codegraph MCP tools available to the agent |
| graphify | `graphify-out/` exists; read `GRAPH_REPORT.md` first, it is a summary |
| ctags | a `tags` file exists, or `ctags` is on PATH |
| ast-grep | `ast-grep` or `sg` on PATH |
| Serena / LSP | the agent's own tool list |

## What to take from each

| Source | Take | Skip |
|---|---|---|
| AGENTS.md / CLAUDE.md | build and test commands, conventions, known traps | style rules |
| README / ARCHITECTURE | modules and how they connect | install steps for end users |
| ADRs | decisions that constrain the change | history |
| codegraph / graphify | callers and dependents of the symbols the task names: this is the blast radius that drives round counts | full graph dumps |
| git log | how often the touched files change, recent related commits | diffs |
| rg / ast-grep | where a symbol or string lives, how many call sites | whole files |

## Rules

- Never install a tool, build an index, or write outside `.reckon/` while
  estimating. If a tool is installed but the repo has no index, put a one-
  line suggestion in the profile (for example, that the owner can build a
  codegraph index) and move on.
- Trust docs less than code when they disagree; note the disagreement in
  the profile Notes.
- Stop reading as soon as you can place every module in the anchors table.
