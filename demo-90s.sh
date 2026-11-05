#!/usr/bin/env bash
# A 60-second demo of the loop, sized for a screen recording.
#
#   ./demo-90s.sh
#
# It never touches a real approval store and never publishes anything: the last
# step shows the guard escalating to a human, which is where the story ends.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WORK="$(mktemp -d)"
PYTHON="${PYTHON:-python3}"
export OPENREADY_APPROVAL_HOME="$WORK/approvals"
export PYTHONPATH="$HERE${OPENREADY_SRC:+:$OPENREADY_SRC}${PYTHONPATH:+:$PYTHONPATH}"
# A source checkout of the CLI, if one is next to this repo; otherwise it must be installed.
[ -d "$HOME/projects/openready/src" ] && export PYTHONPATH="$PYTHONPATH:$HOME/projects/openready/src"

step() { printf '\n\033[1;36m%s\033[0m\n' "$*"; sleep 1.2; }
say()  { printf '%s\n' "$*"; sleep 0.8; }

printf '\033[1mhermes-openready\033[0m — a release gate that cannot publish\n'
sleep 1.5
say "Five tools prepare and verify a candidate. One hook refuses to let it go public."
say "The plugin has no push, no visibility change, no upload — by construction."

step "1. An agent tries to publish a private repository"
say '$ gh repo edit socraticblock/example --visibility public'
"$PYTHON" - "$WORK" <<'PY'
import sys
from openready_plugin import hook
work = sys.argv[1]
r = hook.pre_tool_call(tool_name="terminal", args={
    "command": "gh repo edit socraticblock/example --visibility public",
    "workdir": work})
print("\n  hook →", r["action"])
print("  " + r["message"].splitlines()[0])
print("  " + r["message"].splitlines()[1])
PY

step "2. So the agent prepares a candidate instead"
say '$ openready candidate --path .'
"$PYTHON" -m openready new --path "$WORK/project" --json >/dev/null
"$PYTHON" -m openready candidate --path "$WORK/project" --json >/dev/null
"$PYTHON" - "$WORK/project/.openready-candidate/CANDIDATE.json" <<'PY'
import json, sys
d = json.load(open(sys.argv[1]))
print("  digest:", d["candidate_digest"][:26] + "…")
print("  publication:", d["publication_status"])
PY
DIGEST=$("$PYTHON" -c 'import json,sys;print(json.load(open(sys.argv[1]))["candidate_digest"])' \
  "$WORK/project/.openready-candidate/CANDIDATE.json")

step "3. The owner records an approval for that exact digest"
say '$ openready.publisher record --actor owner'
"$PYTHON" -m openready.publisher record \
  --candidate "$WORK/project/.openready-candidate" \
  --destination "https://github.com/socraticblock/example" \
  --licence MIT --actor owner --json >/dev/null
say "  approval recorded for ${DIGEST:0:26}… (sha256, full digest)"

step "4. The same command is now escalated — never auto-allowed"
"$PYTHON" - "$WORK/project" <<'PY'
import sys
from openready_plugin import hook
r = hook.pre_tool_call(tool_name="terminal", args={
    "command": "gh repo edit socraticblock/example --visibility public",
    "workdir": sys.argv[1]})
print("\n  hook →", r["action"])
print("  " + r["message"].split("Publication is")[0].strip())
print("  Publication is still the owner's decision — confirm to allow this command.")
print("  rule_key:", r["rule_key"][:34] + "…")
PY

step "5. Nothing was published"
say "Approval is evidence, not permission. The owner still decides."
rm -rf "$WORK"
