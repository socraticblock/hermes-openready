# The 30-second demo

Screen content: `./demo-30s.sh` (21 s of runtime, paced to change with the voice).
Narration: ~75 words, about 28 seconds at a measured pace.

Record the terminal window while the script runs, and speak over it. No editing
needed: the script clears the screen at the start, so begin recording one second
before you press Enter.

## Narration, timed

| Time | Say | On screen |
|---|---|---|
| 0:00 | "An agent with a terminal can make a repository public in one command." | title, then the `gh repo edit` command |
| 0:05 | "This plugin refuses." | **blocked** — no owner approval covers that repository |
| 0:08 | "It looks for an owner approval bound to one exact digest. There isn't one, so the agent prepares a candidate instead." | `openready candidate` |
| 0:14 | "The candidate is not approved. The owner records an approval for that digest." | digest, `NOT APPROVED`, then `record` |
| 0:19 | "Now the same command escalates — scoped to that digest, and still the owner's decision." | **escalated** + the rule key |
| 0:25 | "No push, no visibility change, nothing was published." | closing lines |

If you would rather not speak, the same text works as captions burned over the
recording.

## Recording on this machine

1. Windows Terminal, dark theme, font size 20 or larger, window roughly 16:9.
2. Start Game Bar with `Win+G`, or OBS with a 1920×1080 canvas at 30 fps.
3. `cd ~/projects/hermes-openready && ./demo-30s.sh`
4. Record, then post with a tag for `@NousResearch`.

Set `PAUSE=0.4 ./demo-30s.sh` if you want the whole screen sequence in about six
seconds and intend to slow it down in an editor instead.
