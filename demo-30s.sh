#!/usr/bin/env bash
# The 30-second cut. Screen content only; the words are spoken over it.
#
#   ./demo-30s.sh
#
# Shot list (narration in docs/DEMO-30S.md):
#   0:00 title + the threat      0:04 blocked
#   0:10 candidate, NOT APPROVED 0:17 approval + escalation   0:25 close
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WORK="$(mktemp -d)"
PYTHON="${PYTHON:-python3}"
export OPENREADY_APPROVAL_HOME="$WORK/approvals"
export PYTHONPATH="$HERE${PYTHONPATH:+:$PYTHONPATH}"
[ -d "$HOME/projects/openready/src" ] && export PYTHONPATH="$PYTHONPATH:$HOME/projects/openready/src"

# The screen should change with the voiceover, not before it: ~2s per beat puts
# the five shots at roughly 0:00, 0:05, 0:12, 0:19 and 0:25. Override with PAUSE=0.
beat() { sleep "${PAUSE:-1.8}"; }
line() { printf '%s\n' "$*"; beat; }

printf '\033[2J\033[H'
printf '\033[1mhermes-openready\033[0m\n'; beat
line "A Hermes plugin that refuses to publish."
echo

line '$ gh repo edit socraticblock/example --visibility public'
"$PYTHON" - "$WORK" <<'PY'
import sys
from openready_plugin import hook
r = hook.pre_tool_call(tool_name="terminal", args={
    "command": "gh repo edit socraticblock/example --visibility public",
    "workdir": sys.argv[1]})
print("\n  \033[1;31mblocked\033[0m — " + r["message"].splitlines()[1].strip())
PY
beat

echo
line '$ openready candidate --path ./project'
"$PYTHON" -m openready new --path "$WORK/project" --json >/dev/null
"$PYTHON" -m openready candidate --path "$WORK/project" --json >/dev/null
"$PYTHON" - "$WORK/project/.openready-candidate/CANDIDATE.json" <<'PY'
import json, sys
d = json.load(open(sys.argv[1]))
print("  digest  ", d["candidate_digest"][:24] + "…")
print("  status  ", d["publication_status"].split(" -- ")[0])
PY
beat

echo
line '$ openready.publisher record --actor owner'
"$PYTHON" -m openready.publisher record \
  --candidate "$WORK/project/.openready-candidate" \
  --destination "https://github.com/socraticblock/example" \
  --licence MIT --actor owner --json >/dev/null
line "  approval recorded for that digest"
beat

echo
line '$ gh repo edit socraticblock/example --visibility public'
"$PYTHON" - "$WORK/project" <<'PY'
import sys
from openready_plugin import hook
r = hook.pre_tool_call(tool_name="terminal", args={
    "command": "gh repo edit socraticblock/example --visibility public",
    "workdir": sys.argv[1]})
print("\n  \033[1;33mescalated\033[0m — approval bound to", r["rule_key"].split(":")[-1][:18] + "…")
print("  the owner still confirms; nothing was published.")
PY
beat
rm -rf "$WORK"
