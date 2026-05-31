#!/bin/bash
# vf-harness init.sh — generic entrypoint for vafi-developer images.
#
# Dispatches harness-specific setup based on $VF_HARNESS (set by the leaf's ENV),
# then runs common post-init (glab, mempalace auto-init), then execs the CMD.
#
# Runs as agent user (NO ROOT).
set -e

mkdir -p "$HOME/.config"

HARNESS="${VF_HARNESS:-claude}"

# --- Harness-specific setup ---

case "$HARNESS" in
  claude)
    source /opt/vf-harness/init-claude.sh
    ;;
  pi)
    source /opt/vf-harness/init-pi.sh
    ;;
  gemini)
    source /opt/vf-harness/init-gemini.sh
    ;;
  agy)
    source /opt/vf-harness/init-agy.sh
    ;;
  *)
    echo >&2 "[vf-harness] Unknown VF_HARNESS=$HARNESS — skipping harness init"
    ;;
esac

# --- Common: glab (GitLab CLI) ---

if [ -n "${GITLAB_TOKEN:-}" ]; then
  mkdir -p "$HOME/.config/glab-cli"
  cat > "$HOME/.config/glab-cli/config.yml" <<GLEOF
hosts:
  ${GITLAB_HOST:-gitlab.optiscangroup.com}:
    token: ${GITLAB_TOKEN}
    api_host: ${GITLAB_HOST:-gitlab.optiscangroup.com}
    api_protocol: https
    git_protocol: ssh
GLEOF
  chmod 600 "$HOME/.config/glab-cli/config.yml"
  echo >&2 "[vf-harness] glab: configured for ${GITLAB_HOST:-gitlab.optiscangroup.com}"
fi

# --- Common: mempalace auto-init ---
# mempalace init prints a multi-line banner to stdout. That would mangle the
# JSON output of /opt/vf-harness/run.sh in non-interactive use. Suppress all
# of it — we log our own short notes to stderr.
#
# Two-step: (1) write config.json via `mempalace init`, then (2) force-create
# the ChromaDB collection at palace_path. Without step 2, MCP search on a
# never-written palace returns "No palace found" instead of an empty result,
# which looks like a broken install.

if [ "${MEMPALACE_AUTO_INIT:-false}" = "true" ]; then
  if [ ! -f "$HOME/.mempalace/config.json" ]; then
    echo >&2 "[vf-harness] Auto-initializing mempalace config"
    mempalace init /workspace --yes >/dev/null 2>&1 || true
  fi
  if [ ! -d "$HOME/.mempalace/palace" ]; then
    echo >&2 "[vf-harness] Seeding empty mempalace collection"
    python3 -c "from mempalace.palace import get_collection; get_collection('$HOME/.mempalace/palace')" >/dev/null 2>&1 || true
  fi
fi

# --- Launch ---

cd /workspace
exec "$@"
