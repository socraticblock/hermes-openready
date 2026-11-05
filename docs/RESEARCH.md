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

Config schema: `openready_bin` (str, default `openready`), `timeout_seconds` (int, default 120),
`guard_enabled` (bool, default true), `approval_home` (str, optional).

### The invariant

> With the plugin enabled, a `terminal` call whose command matches the publish-action grammar
> never executes on the strength of the model's own say-so. It is either **blocked** (no
> verified packet for the target) or **escalated to the human approval gate** (packet present),
> and it fails closed wherever no human can answer.

### Decision table

| condition | action |
| --- | --- |
| command does not match publish grammar | pass through (`None`) |
| matches, `guard_enabled: false` | pass through (documented off-switch, owner config) |
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
120 passed in 0.93s
```

Covers: 33 publish shapes classified, 31 non-publish shapes passed (including
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
