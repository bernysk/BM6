@AGENTS.md

## Claude in this repo

- **Coordinator:** if you are the agent the user started (no Task or Dispatch IDs in your prompt), you are the coordinator. Use the Orca `orchestration` skill to dispatch Codex and OpenCode workers, as AGENTS.md describes. Don't implement or write tests yourself, and don't use your own subagents for that work.
- **Worker:** if your prompt has Task and Dispatch IDs, you are a worker. Follow the "Workers" section of AGENTS.md.
