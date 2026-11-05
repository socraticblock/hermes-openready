# Security policy

## Reporting a vulnerability

Open a private report through GitHub's **Security → Advisories → Report a
vulnerability** on this repository. Please do not open a public issue for a bypass.

Include the command string that reaches the guarded surface without being blocked or
escalated, and the commit you tested. A command string is enough — do not attach
credentials, tokens, or a working approval store.

## What counts

This plugin's claim is narrow and testable: a `terminal` command that would change a
repository's visibility, or create a public repository, is blocked or escalated rather
than executed silently.

In scope:

- a command that publishes and is classified as safe (`classify()` returns `None`);
- an approval that authorises a repository or artifact it does not cover;
- a path where a failed check, a malformed configuration, or a crash permits instead of
  denies;
- the approval lookup executing code from the repository it is judging.

Out of scope, and stated as such in the README: shell obfuscation that never names a
GitHub client, a script written and then executed, other tools that reach a shell without
the `terminal` hook (the code execution RPC path, `process_manage` PTY input, MCP servers,
browser sessions), a writable approval store, and a deliberately misconfigured
`openready_python`.

## Response

This is a single-maintainer project. Reports are acknowledged when read, and a fix ships
with a regression test that fails on the commit that carried the bypass.
