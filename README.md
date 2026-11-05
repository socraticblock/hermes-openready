# hermes-openready

Open-Ready, wired into Hermes Agent: an agent can **prepare** an open-source release
candidate from a private repository and **prove** what it contains — and it still cannot
publish it.

The plugin is the Hermes-facing half of [Open-Ready](https://github.com/socraticblock/open-source-ready).
It registers five tools and one `pre_tool_call` guard. There is no publish path in this
package: no push, no visibility change, no upload. The only files any tool writes are
Open-Ready's own outputs — a candidate directory, and, if the caller asks for one, a
release packet at a path it names.

## The problem it solves

An agent with a shell can flip a private repository public in one command:

```
gh repo edit owner/repo --visibility public
```

That is irreversible in the ways that matter: clones, forks and caches survive a later
revert. Prompt instructions do not stop it, and a skill cannot either — both are advice.
A `pre_tool_call` hook is the first place in Hermes where a policy can actually refuse a
command before it runs.

So the split is deliberate:

- **Agent:** reads, diagnoses, exports a frozen candidate, verifies it, reports.
- **Owner:** records an approval bound to that candidate's digest, then performs the
  publication — or confirms the escalated command.

## Install

Requirements: Hermes Agent ≥ 0.20, and Open-Ready installed so that `openready` runs and
some interpreter can `import openready` (the read-only approval check invokes
`python -m openready.publisher`).

```
git clone https://github.com/socraticblock/hermes-openready
cp -r hermes-openready ~/.hermes/plugins/
```

Then enable the plugin for the profile and, if needed, point it at your install:

```yaml
plugins:
  entries:
    hermes-openready:
      settings:
        openready_bin: /home/you/.local/bin/openready
        openready_python: /home/you/.venv/bin/python
```

Environment variables (`HERMES_OPENREADY_*`) override config; config overrides defaults.
Malformed values are reported in tool output and in guard messages, never silently coerced.

## Tools

- `openready_status` — project metadata and registry. Read-only.
- `openready_doctor` — findings for a repository: what is wrong, what was not checked, the
  current publication status. Read-only.
- `openready_candidate` — export an **unapproved** candidate. Writes only the candidate
  directory.
- `openready_verify` — verify a frozen candidate and emit its unapproved release packet.
- `openready_gate` — read-only: what would the guard decide for this destination now?

Every tool result carries `"publication": "NOT_APPROVED"` and the note that exit 0 never
means safe to publish. Open-Ready's exit codes are preserved verbatim: `0` OK, `1` FAIL,
`2` NEEDS_HUMAN, `3` NOT_RUN, `64` USAGE. Only `0` from the publisher check is an approval.

## The guard

`pre_tool_call` inspects the `terminal` tool only, and only commands that change a
repository's public surface:

| Shape | Example |
| --- | --- |
| visibility change | `gh repo edit owner/repo --visibility public` |
| public creation | `gh repo create name --public` |
| API equivalent | `gh api -X PATCH /repos/owner/repo -f visibility=public` |
| REST create (defaults public) | `gh api /user/repos -f name=x` — silence means public |
| REST visibility flip | `gh api -X PATCH /repos/o/r -f private=false` |
| REST visibility, value the guard cannot read | `gh api -X PATCH /repos/o/r -f visibility=$(echo public)` |
| direct REST | `curl -X PATCH https://api.github.com/repos/owner/repo -d '{"visibility":"public"}'` |
| behind a wrapper | `sudo …`, `timeout 30 …`, `env -S "…"`, `bash -lc "…"`, `setsid …`, `script -qec "…"` |
| behind shell syntax | `time gh …`, `! gh …`, `{ gh …; }`, `(gh …)`, `then gh …` |
| in a command list | `git status; gh repo edit owner/repo --visibility public` |

It sees through absolute paths, `--repo/-R`, `--flag=value`, attached short flags
(`-XPOST`, `-d@body.json`), stacked shell flags (`bash -lc`), and the wrappers above. It does
not try to recognise every possible way to publish: a grammar that guesses produces false
blocks, and a guard that blocks honest work gets switched off. Where it cannot resolve a
value — `--visibility $'public'`, `-F visibility=@file`, `--input body.json` — it refuses
rather than assuming the harmless reading.

Decision table:

- **no approval** → `block`, with the next step named (candidate → verify → owner records).
- **approval covers this exact digest and destination** → `approve`: the command still
  needs a human confirmation. Approval is evidence, not permission.
- **approval covers something else** → `block`. A command that does not name the repository
  it would publish (`gh repo create newname`, a GraphQL mutation) cannot be bound to an
  approval, so it is never approved on the strength of one.
- **check could not run** (missing binary, timeout, malformed config, classifier error) →
  `block`. The guard fails closed, and says why.

The approval lookup is `python -m openready.publisher check`, which re-derives the digest
from the candidate's own bytes and ignores approvals asserted inside the candidate. This
package does not re-implement that logic — it asks the tool, and treats every non-answer
(exit 1/2/3/64, timeout, crash) as a refusal. The check runs from a temporary directory with
GitHub credentials removed from its environment: `python -m` puts the working directory
first on `sys.path`, and the guard must not execute the repository it is judging.

## What this does not protect against

Stated plainly, because a guard that oversells itself is worse than none:

- **Other tools.** Only `terminal` is inspected. Two core paths reach a shell without it —
  the code execution tool (which calls `terminal` over an internal RPC socket) and stdin
  written into a live PTY by `process_manage` — plus any MCP server or browser action. If
  those are in an agent's toolset, this hook is not the control that stops them.
- **Obfuscation.** `$(echo gh) repo edit …`, a base64 payload, or a script the agent writes
  and then runs. The grammar catches the shapes in the table, not every shell construction.
- **A fake approval.** The guard trusts the publisher's own JSON: if `openready_python` is
  pointed at something that prints a plausible `check` result, the guard will believe it.
  It refuses silent success (exit 0 with no check JSON, or with no digest, is an error), it
  never runs the check from inside the repository, and it strips GitHub tokens from the
  child's environment — but it cannot authenticate the interpreter. That is a configuration
  responsibility.
- **Opaque API bodies.** `gh api … --input body.json`, `curl --data @file`, and
  `--data-urlencode field@file` against a repo endpoint are escalated, because the guard
  cannot read the body. A PATCH that only changes a description will still ask for
  confirmation; that is the deliberate cost of not guessing.
- **A writable approval store.** An approval record is a file. Anyone who can write it can
  rewrite the record and its digest together. Keep it where only the owner writes.
- **Anything after the fact.** The guard refuses a command; it does not audit what happened
  outside Hermes.

## Evidence

Verified on this host (Open-Ready `0.1.0-p0`, Python 3.14.9):

- `pytest`: **169 passed** — grammar table (67 publish shapes, 38 non-publish shapes),
  decision table, runner failure modes (including a grandchild holding the pipe open),
  settings precedence, tool envelopes, evidence lookup.
- `hermes plugins validate`: every check green, including `capability probe`
  (`register() ran in isolation`), `declared tools`, `declared hooks`, `security scan`,
  and `no core override`.
- End-to-end against the real CLI: a scaffolded project exported a candidate
  (`sha256:47e5f762…`), `openready_gate` returned `missing → block`, the hook returned
  `block` for `gh repo edit … --visibility public`, and after
  `python -m openready.publisher record` the same gate returned `approved → approve`
  (human gate, not auto-allow).
- A fresh repo with no contract: `openready_doctor` exit `1` (`FAIL`), envelope still
  `NOT_APPROVED`; oversized reports come back as a bounded summary plus a capped excerpt,
  never as truncated JSON pretending to be JSON.

## Layout

```
plugin.yaml              manifest: tools, hooks, config schema
__init__.py              entry point; imports register() only
openready_plugin/
  settings.py            env > config.yaml > defaults; problems reported
  runner.py              bounded subprocess; never raises, never hangs
  publish.py             pure grammar + decision table
  evidence.py            read-only approval lookup
  tools.py               tool schemas and handlers
  hook.py                the publish guard
tests/                   169 tests, no network, no real CLI required
docs/RESEARCH.md         why each decision was made, with source handles
```

## License

MIT.
