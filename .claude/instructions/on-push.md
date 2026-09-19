# Pushing

Push only when the user asks, to the remote and branch they name (or the branch's upstream).

1. Make sure the task's work is committed and `precommit.ps1` was clean.
2. Run `git push` (never `--force` or `--force-with-lease` unless the user asks for that exact thing, and then say what it will overwrite).
3. **The pre-push hook runs by itself.** It refreshes the graph, saves a snapshot (`.claude/atlas/work/graph-snapshots.jsonl`), computes the risk of what is being pushed, records the push in the task, and prints one line such as:
   `[codeatlas] push to origin: LOW risk, 3 symbol(s) modified, 0 removed, 2 dependant(s), 1 flow(s). Graph updated: 412 symbols, 1650 edges.`
   Tell the user that line in your own words. The hook does not block a push unless `push.mode` in `.claude/config/policy.json` is `block` and the risk reaches `push.blockAt`. If it blocks, show the reason and let the user decide; do not change the policy yourself.
4. **On the server:** if the repo has the CodeAtlas workflow (`.github/workflows/codeatlas.yml`), the push also rebuilds the graph in CI and posts an impact summary on the run page. Mention it if the user asks how the graph stays current for the team.
5. If the push is rejected (remote has new commits), tell the user. Do not rebase or merge without being asked.

## Pull requests

`work.ps1 finish` writes `.claude/atlas/work/pr-<id>.md`. Offer it as the description. Create the pull request only if the user asks.
