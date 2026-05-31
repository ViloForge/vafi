#!/bin/bash
# vf-harness connect.sh — interactive session entry point.
# Dispatches on $VF_HARNESS to launch the appropriate CLI in interactive mode,
# resuming the last session if one exists.

WORKDIR=$(cat /tmp/ready 2>/dev/null || echo /workspace)
cd "$WORKDIR"

HARNESS="${VF_HARNESS:-claude}"

case "$HARNESS" in
  claude)
    DIR_KEY=$(pwd | sed 's|/|-|g')
    if [ -d "$HOME/.claude/projects/$DIR_KEY" ]; then
      exec claude --continue --dangerously-skip-permissions "$@"
    else
      exec claude --dangerously-skip-permissions "$@"
    fi
    ;;
  pi)
    # Pi doesn't have a "skip all permission prompts" flag; it trusts its
    # default tool set (read/bash/edit/write). --continue resumes if a session
    # exists, otherwise pi falls back to a new session.
    exec pi --continue "$@" 2>/dev/null || exec pi "$@"
    ;;
  gemini)
    # -y = yolo (auto-approve all actions). --resume latest if a prior session
    # exists; gemini falls back to new session if none.
    exec gemini -y --resume latest "$@" 2>/dev/null || exec gemini -y "$@"
    ;;
  agy)
    # --dangerously-skip-permissions = auto-approve tool actions. -c/--continue resumes the
    # most recent conversation in this context if one exists; agy falls back to new if none.
    exec agy --continue --dangerously-skip-permissions "$@" 2>/dev/null || exec agy --dangerously-skip-permissions "$@"
    ;;
  *)
    echo >&2 "[connect] Unknown VF_HARNESS=$HARNESS"
    exec bash
    ;;
esac
