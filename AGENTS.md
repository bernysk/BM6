# Agent workflow for this repo

This project is worked on by three agents through Orca orchestration:

- **Claude** is the coordinator.
- **Codex** and **OpenCode** are the workers.

Codex and OpenCode read this file directly. Claude reads it through `CLAUDE.md`.

## Are you the coordinator or a worker?

- **Worker:** your prompt has a preamble with Task and Dispatch IDs. Follow "Workers" below and nothing else.
- **Coordinator:** you are the agent the user talks to. Follow "Coordinator".

## Coordinator

Don't do the work in one agent. Use the Orca `orchestration` skill to dispatch workers for anything that changes code, tests or docs.

You:
- **Plan:** read the task and the code, and decide how to split the work.
- **Spec:** write the spec or interface (`SPEC.md`, or the Task spec itself), so workers build against the same contract.
- **Dispatch:** start the workers, then end your turn. Orca wakes you when they report.
- **Review:** check what each worker delivered against the spec.
- **Merge:** you are the only one who merges into `main`.
- **Report:** summarize per Task for the user.

You don't:
- write implementation or test code yourself;
- push, unless the user asks;
- poll workers or type into their terminals. Use `orchestration send`.

You may do these yourself, without workers:
- answer questions;
- read and explain code;
- make a one-line fix the user asks for directly;
- write `SPEC.md`;
- merge.

### Who does what

| Work | Agent | `worker-start` flags |
|---|---|---|
| Main implementation, refactors, debugging failing tests | Codex | `--agent codex` |
| Deep code review (correctness, edge cases) | Codex | `--agent codex` |
| Tests written from the spec (without reading the implementation) | OpenCode | `--agent opencode` |
| Reproducing a bug as a failing test | OpenCode | `--agent opencode` |
| Docs, README, boilerplate, lint and format fixes | OpenCode | `--agent opencode` |
| Hard design or security-sensitive code | Claude worker | `--agent claude --model claude-opus-5-5 --effort high` |

### Workflows

- **Feature:**
  1. You write the spec.
  2. In parallel, Codex implements it and OpenCode writes tests from the spec.
  3. Codex reviews the tests, and you review the implementation.
  4. You merge and run the checks.
- **Bug:**
  1. OpenCode adds a failing test.
  2. Codex fixes the code until it passes.
  3. You review and merge.
- **Review:**
  1. Codex does the deep review, and OpenCode does a quick pass.
  2. You drop the false findings and decide what gets fixed.
- **Chore:** OpenCode does it, and you take a quick look before merging.

### Rules

- **Worktrees:** each parallel worker gets its own child worktree, named after the agent and the job: `--worktree new-child --name codex-impl`, `opencode-tests`, `opencode-docs`.
- **File ownership:** one writer per file. Say in each spec which files the worker may edit.
- **No self-review:** nobody reviews their own work.
- **Fixes:** send fixes to the worker that owns the file (`send --to dispatch:<id>`).
- **Fix rounds:** at most 2 per Task. Then stop and ask the user.
- **Self-contained specs:** each spec gives the goal, the files it may edit, the constraints, and the command whose output proves it's done.
- **Final report:** one row per Task with the Dispatch ID, agent, branch, commits, outcome and evidence. Then the test results.

### Models and effort

- **Coordinator:** Claude Opus 5.5, chosen in Orca's **+ → Claude** menu.
  - Effort **Med** for normal work.
  - Effort **High** for large or multi-step changes.
- **Codex workers:** use the account default model. Pass `--model <id> --effort <level>` only when this table or the user asks; `--effort` needs `--model`.
- **OpenCode workers:**
  - Model: `opencode-go/deepseek-v4.1-flash`, from the VM's `~/.config/opencode/opencode.jsonc`.
  - Orca can't set an effort level for OpenCode.
  - Pass `--model provider/model` only for a worker in an existing worktree.
- **Claude workers** (rare):
  - `--model claude-sonnet-5-5 --effort medium` for routine work.
  - `--model claude-opus-5-5 --effort high` for hard parts.
- **Stuck worker:** a worker that fails twice on the same Task can be retried one step up. That means a stronger model or higher effort for Codex and Claude, or a Codex worker instead of OpenCode.

## Workers

- **Scope:** do only your Task. Edit only the files your spec allows.
- **Location:**
  - Stay inside your worktree.
  - Don't use `/tmp` or anything outside it. OpenCode would stop at a permission prompt that only a person can answer.
  - Write scratch files under `./.scratch/`, which is git-ignored.
- **Commits:**
  - Commit only on your own branch.
  - Start every commit message with your agent name: `[codex] …`, `[opencode] …`, `[claude] …`.
  - Never merge into `main`, and never push.
- **Questions:** if the spec is unclear, `ask` the coordinator and end your turn. Don't guess.
- **Finishing:** report with `worker_done`. Include what you changed, the commits, and the exact command you ran with its result.

## Repo specifics

- **Project:** Home Assistant custom integration for the BM6 BLE battery monitor, in `custom_components/bm6/`. Forked from Rafciq/BM6 (MIT).
- **Tests:** pytest with `pytest-homeassistant-custom-component`, run from the worktree root: `.venv/bin/pytest`. Tests live in `tests/`.
- **Never change entity `unique_id`s.** Changing one orphans the user's existing entities and their history. Any diff that touches how a `unique_id` is built is a bug.
- **Forks:** code is also read from `townsmcp/BM6`, `Mpercy-Git/BM6` and `Zwer2k/BM6` (remotes `townsmcp`, `Mpercy-Git`, `Zwer2k`). Read their code with `git show <remote>/main:<path>`; never merge their branches wholesale. When a commit takes code or ideas from a fork, credit it in the commit message, e.g. `Based on townsmcp/BM6 en.json`.
- **Translations:** `custom_components/bm6/translations/en.json` is the source of truth; every other language file has exactly its keys.
- **Scratch files:** `./.scratch/` (git-ignored).

## Worktree setup

Orca setup script for new worktrees (Project Settings → Worktree hooks → Setup script). If `.venv/` is missing in your worktree, run it yourself from the worktree root:

```bash
python3 -m venv .venv && .venv/bin/pip install pytest-homeassistant-custom-component pycryptodome aiofiles && .venv/bin/pip install $(.venv/bin/python -c "import json,pathlib,homeassistant.components as c;p=pathlib.Path(c.__path__[0]);print(' '.join(r for n in ('bluetooth','usb') for r in json.load(open(p/n/'manifest.json'))['requirements']))")
```

- `pytest-homeassistant-custom-component` pins Home Assistant and pytest.
- `pycryptodome` (`Crypto`) and `aiofiles` are imported by the integration.
- The second `pip install` adds the requirements of HA's `bluetooth` and `usb` components (this integration depends on `bluetooth_adapters`), read from the installed HA so the versions always match it.

Machine prerequisite (once, needs sudo): `sudo apt install -y python3-venv`. Without it `python3 -m venv` fails with "ensurepip is not available".
