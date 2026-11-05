# Hermes plugin adapter for Open-Ready — research and design

Status: design, pre-implementation. Every claim below carries a handle; anything I did not
execute is labelled.

## 1. The question

Open-Ready is a local-first CLI that prepares a *release candidate* for an existing private
repository and refuses to publish it. The hackathon entry is not a new scanner — it is the
Hermes-facing half: make an agent able to run the preparation, and unable to perform the
publication. This document establishes what that adapter may claim, what it may not, and how
each claim is proven.

## 2. What Open-Ready already guarantees (verified by reading and running)

- **Exit-code vocabulary is closed and normative** — `src/openready/status.py:48-54`:
  `EXIT_OK=0`, `EXIT_FAIL=1`, `EXIT_NEEDS_HUMAN=2`, `EXIT_NOT_RUN=3`, `EXIT_USAGE=64`;
  `BLOCKING_STATUSES = (FAIL, NEEDS_HUMAN, NOT_RUN)` (`status.py:57`). The module docstring
  states the rule the adapter must preserve: *"exit `0` never means safe to publish"*.
- **There is no publish verb.** `openready --help` lists 28 subcommands; none publishes,
  pushes, uploads or changes repository visibility (`publisher.py:1-45`).
- **Approval is bound to an exact artifact.** `publisher.py` re-derives the digest from the
  frozen candidate's own bytes, never from candidate metadata, and ignores self-asserted
  fields (`SELF_ASSERTED_FIELDS`). An approval is a record the builder never writes.
- **The approval store lives outside the project** (`$OPENREADY_APPROVAL_HOME` or
  `<private-home>/approvals/approvals.json`) and an unreadable store is a refusal, never a
  pass (`publisher.py:34-40`).
- **The packet is unambiguous by construction.** `src/openready/contracts/release-packet.schema.json`
  pins `status: const "REVIEW_REQUIRED"` and `decision: const "NOT APPROVED"`.
- **Measured**: `openready doctor --path . --json` on the Open-Ready repo itself exits **2**,
  reports `publication_status: NOT_APPROVED`, and takes **1.68 s** (run 2026-11-05).
- **The limitation Open-Ready states about itself**, which the adapter inherits verbatim:
  there is no credential custody on this host, so `trust.publisher-separation` is
  `NOT_ENFORCED` (`publisher.py:30-33`). An approval file is not a signature.

## 3. What Hermes requires of a plugin (verified against the installed source)

`hermes plugins validate` runs these checks (`hermes_cli/plugin_validate.py`):
manifest fields (`name`, `version`, `description` required), `requires_hermes` spec,
`config_schema` shape, `requires_env`, oauth blocks, **capability probe**, builtin tool
collisions, loadability, python dependency compatibility, and the install security scan.

- **The capability probe imports the plugin in a scratch subprocess** with `HERMES_HOME`
  pointed at a temp dir, calls `register(ctx)` on a recording context, and records
  `{tools, hooks, middleware, commands, providers}` (`_run_capability_probe`). Declared
  `provides_*` must match what actually registers. Consequence: **`register()` must do no
  work** — no subprocess, no filesystem scan, no network. Registration only wires handlers.
- **Hook returns** (`website/docs/developer-guide/observer-hooks.md:63`, implemented in
  `hermes_cli/plugins.py:1957`): `{"action": "block", "message": ...}` vetoes the call;
  `{"action": "modify", "args": {...}}` rewrites arguments; `{"action": "approve", ...}`
  routes to `tools/approval.py:1134 request_tool_approval()` — the *same* human gate used for
  Tier-2 dangerous shell patterns, which **fails closed** in any non-interactive context.
- **The install scanner** (`tools/plugin_guard.py` → patterns in `tools/skills_guard.py`)
  grades `subprocess.run(...)` as **medium / caution** (`skills_guard.py:383-384`) and
  `os.system(...)` as **high** (`:385`). `dangerous` blocks installation and cannot be
  overridden; `caution` is accepted for a reviewed catalog pin. A CLI wrapper will always
  carry one honest `python_subprocess` caution finding.
- **Catalog admission** (`plugin-catalog/README.md`, mirrored in the docs page): public
  `https://` repo, loadable plugin at the pinned commit, public surfaces only, local
  `hermes plugins validate` green, one `<name>.yaml` entry with a full 40-char SHA, owner or
  major-contributor submission, declared capabilities matching reality, no self-updating code.
- **The terminal tool's arguments** are `command` (required) and optional `workdir`
  (`tools/terminal_tool.py:1620-1665`). That is the entire surface a publish gate can inspect.

## 4. Design options

**A. Import Open-Ready in-process.** Fast, no subprocess finding. Rejected: pulls 12,771
lines and 30 modules into the gateway process, couples the plugin to Open-Ready's Python and
dependency set, and turns an Open-Ready bug into a gateway incident. It also breaks the
isolation that makes the CLI's exit-code contract trustworthy.

**B. Subprocess to the installed CLI.** Chosen. The CLI's JSON plus exit codes *are* the
published contract (`status.py`), so the adapter depends on a stable interface rather than
internals. Cost: one medium/caution scanner finding, ~1.7 s per doctor call, and a hard
requirement that `openready` exists on PATH (or at a configured path).

**C. Skills-only.** Rejected on the hackathon's own stated preference for plugins that do
more than a prompt or a skill could, and because a skill cannot refuse a tool call.

## 5. Chosen surface

Tools (all read-only or candidate-producing; **none publishes**):

| tool | runs | side effect |
| --- | --- | --- |
| `openready_status` | `openready status --json` | none |
| `openready_doctor` | `openready doctor --path <p> --json` | none |
| `openready_candidate` | `openready candidate --path <p> --out <dir> --json` | writes the candidate dir only |
| `openready_verify` | `openready verify <candidate> --json` | writes the packet next to the candidate |
| `openready_gate` | reads packet + approval store | none |

Hook: `pre_tool_call` on `terminal` only.

Config schema: `openready_bin` (str, default `openready`), `openready_python` (str, default
`python3`), `timeout_seconds` (int, default 120), `approval_timeout_seconds` (int, default 20),
`candidate_dir` (str), `licence` (str, default `MIT`), `destination` (str, optional override).

There is no `guard_enabled` flag. A guard that a config value can turn into a pass-through is
itself a publish path, and this plugin's claim is that it has none; to stop guarding, disable
the plugin.

### The invariant

> With the plugin enabled, a `terminal` call whose command matches the publish-action grammar
> never executes on the strength of the model's own say-so. It is either **blocked** (no
> verified packet for the target) or **escalated to the human approval gate** (packet present),
> and it fails closed wherever no human can answer.

### Decision table

| condition | action |
| --- | --- |
| command does not match publish grammar | pass through (`None`) |
| matches, no packet / unreadable / digest mismatch | `block` with the reason and the next command to run |
| matches, verified packet + approval record for digest+destination | `approve` (human gate, message carries digest + packet path) |
| `openready` missing, times out, or returns unusable JSON | `block` (fail closed), message names the fault |
| hook raises | block (the dispatcher skips a raising hook — so the handler catches everything and returns block itself) |

## 6. Bypass surface — stated, not hidden

The gate matches command *shape*. An agent that wants to evade it can run `python -c`,
`gh api` with a PATCH, or write the approval store itself. Therefore:

- The plugin claims a **guardrail on the agent's own tool calls**, not an OS sandbox.
- It never *allows* a publish automatically: even with a valid approval record the call goes
  to the human gate, so a forged record buys an informed prompt, not a silent pass.
- The approval store is only as strong as its location — the same `NOT_ENFORCED`
  publisher-separation limitation Open-Ready already documents (`publisher.py:30-33`).

These three sentences go in the README's first paragraph and in the catalog PR disclosure.

## 7. Test matrix (pragmatic-engineering)

Invariant-level falsifiers, each asserting an externally observable outcome:

1. **Block** — 10 publish shapes (`gh repo edit --visibility public`, `gh repo create --public`,
   `gh api -X PATCH .../repos/... -f visibility=public`, `git push` to a public remote when
   unverified, …) return `block` and never `None`.
2. **False-positive control** — 12 non-publish commands (`gh repo view`, `git push origin main`
   on a private remote, `gh pr create`, `npm publish --dry-run`, `openready doctor`) return `None`.
3. **Escalation** — packet + matching approval ⇒ `approve`, and the message contains the digest.
4. **Fail closed** — binary missing / timeout / malformed JSON / non-zero-but-unmapped exit ⇒ `block`.
5. **No mutation** — the hook never returns `modify`; args are never rewritten.
6. **Exit-code mapping** — 0/1/2/3/64 map to `ok/fail/needs_human/not_run/usage`, and exit 0 is
   never reported as "safe to publish".
7. **Capability honesty** — `hermes plugins validate` green, with declared `provides_tools` and
   `provides_hooks` exactly matching the probe.
8. **Integration** — a real `terminal` dispatch through the hook returns the block directive
   (E3), not just a unit call (E2).

Evidence levels: unit tests E2; validate E3; the demo recording E4. No claim above E4 without
an actual publish attempt in a throwaway repo.

## 8. Packaging and submission

- New public repo `socraticblock/hermes-openready`, plugin at the root (`plugin.yaml`,
  `__init__.py`, package dir, `tests/`, `README.md`), MIT, no vendored code.
- `hermes plugins validate . --install-deps` must be green locally before the PR.
- One file `plugin-catalog/hermes-openready.yaml` in `NousResearch/hermes-agent`, pinned to a
  full SHA, `capabilities` mirroring the probe, `docs_url` to the README.
- PR description discloses: subprocess execution of a user-installed CLI, the fail-closed
  behaviour, the bypass surface, and that the plugin has no publish path.
- Demo (≤90 s): agent prepares a candidate → packet says `NOT APPROVED` → agent tries to flip
  the repo public → blocked → owner records approval → agent retries → human gate card.

## 9. Open questions for Jeff

1. Do we gate `git push` as well as visibility flips, or only the irreversible public-surface
   actions? (I propose visibility flips and public repo creation only — `git push` to an
   existing private remote is normal work.)
2. Should the adapter ship its own copy of the publish grammar as data (`publish_shapes.json`)
   so users can extend it without editing code?
3. Repo name: `hermes-openready` vs `openready-hermes-plugin`.

**Resolved while building (2026-11-05):** (1) gate visibility flips and public creation only —
`git push` to an existing private remote is normal work, and blocking it would be the false
positive that gets the guard switched off; (2) grammar stays in code for v0.1.0 — a data file
invites untested shapes and a second source of truth, and the decision table already covers
the three irreversible verbs; (3) `hermes-openready`.

## 10. Evidence log (measured, 2026-11-05)

Host: WSL2, Open-Ready `0.1.0-p0`, Python 3.14.9, pytest 9.2.0.

**Unit suite** — `python -m pytest -q` in `~/projects/hermes-openready`:

```
162 passed in 2.01s
```

Covers: 63 publish shapes classified, 35 non-publish shapes passed (including
`echo "gh repo edit --visibility public"`, `git push origin main`, `gh api` GET,
`-f visibility=private`, `GH_TOKEN=x gh repo view`, `env -u FOO git status`), decision
table (approved → `approve`, missing → `block`, error → `block`), runner (missing binary,
timeout, a grandchild holding the pipe open, non-UTF-8-safe decode, 200k capture cap,
empty argv), settings (env > config > defaults, malformed int/bool/text reported), tool
envelopes (exit 2 → `NEEDS_HUMAN`, exit 0 still `NOT_APPROVED`, no argument and an
unresolvable path both return an error envelope instead of raising), evidence lookup
(exit 0 with a real `check` payload → approved + verified digest; exit 0 without check JSON
→ error; 1/2/3 → refusal with no borrowed digest; 124 → error; argv is
`-m openready.publisher`, never a shell string).

**Admission** — `validate_plugin_dir(Path("~/projects/hermes-openready"))` using the
Hermes venv interpreter:

```
manifest                     True   plugin.yaml parses
manifest fields              True   name, version, description present
requires_hermes              True   spec '>=0.20' parses
config schema                True   shape valid
requires_env                 True   all entries UPPER_SNAKE
oauth                        True   not declared
loadable                     True   entry: __init__.py
python dependencies          True   none declared
capability probe             True   register() ran in isolation
declared tools               True   matches registrations
declared hooks               True   matches registrations
declared middleware          True   matches registrations
built-in tool collisions     True   no collisions
security scan                True   safe
no core override             True   no runtime rebinds of Hermes core
OK: True | exit: 0 | warnings: []
```

**End-to-end, real CLI** (scratch project created by `python -m openready new`, approval
store redirected with `OPENREADY_APPROVAL_HOME`):

- `openready_doctor` on a bare git repo → exit `1` (`FAIL`), `publication: NOT_APPROVED`.
- `openready_gate` with no candidate → `missing → block`, detail names the missing path.
- `pre_tool_call` for `gh repo edit socraticblock/example --visibility public` → `block`;
  `git status` → `None` (pass-through).
- After `python -m openready.publisher record … --actor owner`: gate → `approved → approve`,
  digest `sha256:47e5f762b7c7b01c907c9a6383d61f73a7a5954bfe038ec34c0eda9c87c2616f`
  (read from `CANDIDATE.json` only after the publisher check re-derived it); the hook returns
  `approve`, never a silent pass.

**External adversarial review** — two free models on opencode-go (`gpt-6-luna`, effort
high; `beam-free`, effort high), each given the full source and a brief asking for concrete
bypasses and contract violations. Two rounds, both read in full.

Round 1 (before the grammar expansion) found:

1. `exit 0` from the configured `openready_python` was treated as approval. A succeeding
   non-Open-Ready executable would have produced a borrowed digest. Now the stdout must
   parse as the publisher's own result: `command == "check"`, `exit_code == 0`, and a
   non-empty `approval_file`; otherwise the lookup returns `error` and the guard blocks.
2. Block directives carried `rule_key` while approve directives carried a different one.
   `rule_key` scopes an approval, so blocks no longer carry it.
3. Grammar gaps: `curl` REST calls, wrapper prefixes (`sudo`, `timeout`, `env -S`,
   `bash -lc`), command lists (`;`, `&&`), attached short flags (`-XPOST`).

Round 2 (after those fixes) found, and this is the useful part — both models independently
hit the same shell-prefix hole:

4. `GH_TOKEN=x gh repo edit … --visibility public` passed, because the assignment was read
   as the command name. Leading `NAME=value` tokens are now stripped before classification.
5. `gh -R owner/repo repo edit --visibility public` passed: `gh` accepts global flags before
   the subcommand. Global flags are skipped (and `-R/--repo` still supplies the target).
6. Repeated `--visibility private --visibility public` used the first value. Any `public`
   now wins, which is both the conservative and the CLI-compatible reading.
7. Public creation over REST (`gh api -X POST /user/repos -f visibility=public`,
   `curl -X POST …/user/repos`) was invisible: the old code only recognised `/repos/owner/name`.
   Creation endpoints are now a separate case.
8. `gh api -X PATCH /repos/o/n --input body.json` was passed although the body could set
   `visibility: public`. It is now escalated; the README states the false-positive cost.
9. `env -S "…"` whose inner string would not tokenise was treated as empty and passed. It
   now fails closed when the raw text names `public`.
10. The hook's classifier-exception path only blocked commands containing `gh`, `repo` and
    `public`; a `curl` command would have passed. Any non-empty command now blocks when the
    classifier raises.
11. `rule_key` was a single constant, so one "always allow" could cover a later, different
    artifact. It is now `hermes-openready:publish:<target>:<digest-prefix>`.
12. `runner` used `subprocess.run(timeout=…)`, which kills the direct child only; a
    grandchild holding stdout could outlive the deadline. It now starts a new session and
    kills the process group, pinned by a test that spawns a pipe-holding grandchild.

Declined: writing a general shell parser (a grammar that guesses produces false blocks),
and treating every `gh api` write as a publish (false positives on ordinary metadata edits).
Both are recorded as limits in the README rather than hidden.

**Round 3** — `gpt-reserve` through the Codex CLI with a read-only sandbox, so unlike the
chat-only reviewers it *ran* the classifier against its own test strings and pasted the
output. Ten findings, all acted on:

13. `sudo sudo sudo sudo sudo gh repo edit … --visibility public` passed: each wrapper consumed
    the nesting budget, so the fifth one fell through. Wrappers are now stripped in a loop
    (budget 64) that does not touch the shell-nesting budget, and hitting either limit fails
    closed when the text still names a public flip.
14. `bash -lc "true; gh repo edit … --visibility public"` passed: the inner string was
    `shlex`-split once and only its first token examined, so `true;` hid the rest. Inner shell
    strings are now re-segmented as command lists.
15. `curl -X PATCH … --data @payload.json` passed: a body file is opaque to the guard. It is
    now escalated, like `gh api --input`.
16. `curl … --data-urlencode visibility=public` passed: the flag was not in the payload
    reader. Added, with `--data-binary`/`--json`.
17. `gh api graphql -f query='mutation { updateRepository(… visibility: PUBLIC) }'` passed:
    GraphQL names no `/repos` path. A GraphQL query that mentions visibility and PUBLIC is
    now a publish intent.
18. `guard_enabled: false` was a documented off-switch that turned the guard into a
    pass-through. Removed: a flag that disables the guard *is* a publish path. To stop
    guarding, disable the plugin.
19. `evidence.py` accepted `{"exit_code": false}` as success, because Python's `False == 0`.
    The value is now checked as a strict `int` and `bool` is rejected.
20. A digest that was not `sha256:<64 hex>` was passed through into the approval message.
    It is now an error.
21. `runner` buffered the child's entire output before applying the 200 kB cap, so a noisy
    process could exhaust memory first. Output is now read in 64 kB chunks and only the cap
    is kept; `truncated`/`captured_bytes` are reported.
22. `openready_available()` raised on `~no-such-user/openready` (expanduser raises
    `RuntimeError`); it now returns `False`, and every handler is wrapped so no path can
    raise into dispatch.

**Round 4** — two independent source audits of the public commit `03bf629`, one by GPT and
one by a second Hermes lane (`assistant-1`), the second with the repository cloned and the
classifier executed. They converged on the same first finding, which is the one that
mattered most: **the guard was protecting the wrong default.** Findings and what changed:

23. `gh api /user/repos -f name=example` and `curl -X POST …/user/repos -d '{"name":"x"}'`
    passed. GitHub defaults `private` to `false` when a repository is created, so silence
    *is* a public creation. The create branch required the word `public` in the body; the
    test suite had blessed the omission as a non-publish shape. Creation endpoints now
    default to `create_public` unless the body explicitly says `private: true` (or
    `visibility: private`), through both clients.
24. `gh api -X PATCH /repos/a/b -f private=false` passed: only the `visibility=public`
    spelling was recognised. `private=false` is now the same intent.
25. Implicit methods were read as GET. `gh api` switches to POST as soon as a field is
    supplied and `curl --json`/`-d` do the same, so `gh api /user/repos -f name=x` (no `-X`)
    slipped through the API branch entirely. The effective method is now inferred from the
    client's own semantics.
26. `gh repo edit a/b --visibility $'public'` passed: bash expands ANSI-C quoting before
    `gh` runs, `shlex` leaves `$public`. Any unresolved `$` or backtick in a visibility
    value is treated as public, because the guard cannot prove what it evaluates to.
27. `time gh …`, `! gh …`, `{ gh …; }`, `(gh …)`, `then gh …`, `setsid …`, `script -qec "…"`
    passed: only a fixed wrapper list was stripped, and shell syntax is not the command.
    Reserved words and grouping characters are stripped, and `setsid`/`script`/`chroot`/
    `unshare` joined the wrapper table (`script` re-splits its quoted command).
28. `curl -XPATCH … -d'{"visibility":"public"}'` (attached body) and
    `--data-urlencode visibility@payload.txt` (field value from a file) passed. Both are
    read now; an `@` that is not inline JSON marks the body opaque and escalates.
29. `gh api graphql --input mutation.json`, `gh api graphql -F query=@mutation.graphql`, and
    `curl …/graphql --json @mutation.json` passed. A GraphQL body the guard cannot read is
    now escalated, and so is any field value containing `@`.
30. A wrapper chain past the budget, or a shell nested past the depth limit, passed when the
    text did not contain the word `public`. Both limits now refuse unconditionally: at that
    point the guard has admitted it cannot classify.
31. **The approval check executed the repository it was judging.** `python3 -m
    openready.publisher` ran with `cwd=<repo>`, and `-m` puts the working directory first on
    `sys.path` — a repository containing `openready/publisher.py` answered its own approval
    check and returned a forged `check` result. Reproduced, then fixed: the check runs from a
    temporary directory, and GitHub token variables are removed from the child's environment.
    Pinned by a test that plants a repo-local `publisher.py` and asserts it never runs.
32. An approval for repository A authorised `gh repo create unrelated-repo --public`: the
    command names no owner/repo, so the hook fell back to the configured destination and
    approved against A's evidence. A command whose target cannot be resolved is now never
    approved, and the rule key carries the full digest rather than a 12-character prefix.
33. A malformed configuration was normalised to defaults and the guard continued to the
    approval lookup, contradicting the README's fail-closed claim. Configuration problems
    now block before any lookup.
34. `runner` enforced its timeout on `proc.wait()` only: a child that exited 0 while a
    grandchild held the pipes returned `ok=True` with an empty capture after two 5-second
    joins. One monotonic deadline now governs exit *and* draining; if readers are still
    alive at the deadline the process group is killed and the result is `timed_out`. The
    group id is captured at start, because after the direct child exits its pid no longer
    resolves and a surviving descendant would escape `killpg`.
35. An approved result with **no digest** was accepted (the `if verified_digest and …`
    guard skipped the check on an empty string), and `CANDIDATE.json` was read as a fallback
    source. Both removed: an approval that names no artifact is an error.
36. A missing working directory was reported as "the openready package must be importable".
    It now says the path does not exist.
37. Governance: no CI, no `SECURITY.md`, no private disclosure path for a security tool.
    Added a GitHub Actions matrix (3.11/3.12/3.13) and a security policy that names the
    in-scope and out-of-scope claims.

The audits also stated the boundary this hook does not cover, and the README now says it
without softening: the code execution tool reaches `terminal` over an internal RPC socket
that bypasses dispatch, and `process_manage` can write into a live PTY. Those are named as
out of scope rather than implied away.

**Bugs the evidence caught during the build** (all fixed, each now pinned by a test):

1. `Evidence.__init__` required `detail`, so an approved result raised inside the hook and
   the guard blocked — correct fail-closed behaviour, wrong cause. `detail` now defaults to `""`.
2. `gh api --method PATCH repos/owner/name …` (no leading slash) was not recognised. Fixed,
   with both forms in the grammar table.
3. Oversized doctor JSON was being truncated into a *string* in `data`, silently destroying
   the shape callers parse. Now: structured data when it fits, otherwise `data_summary` +
   capped `data_text` + `truncated: true` + `full_chars`.
4. `destination` (legitimately empty) was reported as a malformed setting on every load.
   `_as_text` now takes `allow_empty` for that key only.
5. Settings problems were collected and then discarded. They are now surfaced in every tool
   envelope and appended to any guard block message — a guard with wrong config must say so.
6. `hermes plugins validate`'s capability probe imports `__init__.py` as a *package*, while
   pytest imports it as a top-level module. The entry point now prefers the relative import
   and falls back only for tooling, with the reason written down.

**Unverified:** behaviour on macOS/Windows hosts, and the guard under a real GitHub token
(no publish was attempted — by design).
