#!/usr/bin/env bash
# Reproducible demo of the whole loop, in a throwaway directory.
#
# It never touches a real approval store and never publishes anything: the last
# step shows the guard *escalating* to a human, which is the end of the story.
#
#   ./demo.sh [workdir]
#
# Requires: openready importable by $PYTHON, and this plugin's dependencies-free
# package importable from the repository root.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WORK="${1:-$(mktemp -d)}"
PYTHON="${PYTHON:-python3}"
export OPENREADY_APPROVAL_HOME="$WORK/approvals"
export PYTHONPATH="$HERE${PYTHONPATH:+:$PYTHONPATH}"

say() { printf '\n\033[1m== %s\033[0m\n' "$*"; }

say "1. scaffold a private project"
"$PYTHON" -m openready new --path "$WORK/project" --json >/dev/null
echo "created $WORK/project"

say "2. export an unapproved candidate"
"$PYTHON" -m openready candidate --path "$WORK/project" --json >/dev/null
"$PYTHON" - "$WORK/project/.openready-candidate/CANDIDATE.json" <<'PY'
import json, sys
data = json.load(open(sys.argv[1]))
print("candidate_digest:", data["candidate_digest"])
print("publication_status:", data["publication_status"])
PY

say "3. the agent tries to publish it"
"$PYTHON" - "$WORK/project" <<'PY'
import sys
from openready_plugin import hook
repo = sys.argv[1]
command = "gh repo edit socraticblock/example --visibility public"
result = hook.pre_tool_call(tool_name="terminal", args={"command": command, "workdir": repo})
print("command:", command)
print("hook:", result["action"])
print(result["message"])
PY

say "4. the owner records an approval for this exact digest"
"$PYTHON" -m openready.publisher record \
  --candidate "$WORK/project/.openready-candidate" \
  --destination "https://github.com/socraticblock/example" \
  --licence MIT --actor owner --json >/dev/null
echo "approval recorded in $OPENREADY_APPROVAL_HOME"

say "5. the same command is now escalated, not auto-allowed"
"$PYTHON" - "$WORK/project" <<'PY'
import sys
from openready_plugin import hook
repo = sys.argv[1]
result = hook.pre_tool_call(
    tool_name="terminal",
    args={"command": "gh repo edit socraticblock/example --visibility public", "workdir": repo},
)
print("hook:", result["action"])
print(result["message"])
PY

say "done — nothing was published; publication is still the owner's decision"
