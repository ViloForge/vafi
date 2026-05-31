# vf-launchers.sh — shared helper + per-context launchers for vafi-developer images.
#
# Source from ~/.bashrc:
#     [ -f ~/.claude/vf-launchers.sh ] && . ~/.claude/vf-launchers.sh
#
# Then invoke any context launcher with an optional harness argument:
#     vfdev                  # default harness (claude) in ~/VF
#     vfdev --resume         # resume a prior session
#     vfdev --continue       # auto-resume last session
#     vfdev pi               # swap to pi in same context
#     vfdev claude:2.1.90    # pinned version
#     ogcli pi               # ~/OG work, pi harness
#     ogdr claude            # ~/DR work, claude harness
#     pidev                  # ~/PI, default pi
#
# Per-context env + mounts come from arrays named <CTX>_EXTRA_ENV and
# <CTX>_EXTRA_MOUNTS, defined either by the launcher itself (for mounts) or
# by ~/.claude/vf-launchers-context.sh (for secrets). See example file.
#
# Images: vafi-developer:<harness>[-<version>] — see docs/developer-images-DESIGN.md

: "${VF_REGISTRY:=vafi}"

# --- Shared runner ----------------------------------------------------------
_vafi_dev_run() {
  local ctx="$1"; shift
  local root="$1"; shift
  local default_harness="$1"; shift
  local arg1="${1:-}"

  # Parse optional leading <harness>[:version]
  local harness="$default_harness"
  local version=""
  if [ -n "$arg1" ]; then
    case "$arg1" in
      claude|pi|gemini|agy|codex|copilot)
        harness="$arg1"; shift ;;
      claude:*|pi:*|gemini:*|agy:*|codex:*|copilot:*)
        harness="${arg1%%:*}"; version="${arg1#*:}"; shift ;;
    esac
  fi

  local image_tag="${harness}"
  [ -n "$version" ] && image_tag="${harness}-${version}"
  local image="${VF_REGISTRY}/vafi-developer:${image_tag}"

  local agent_home="${root}/home/agent"
  local workspace="${root}/workspace"

  # First-run scaffold
  if [ ! -d "$agent_home" ] || [ ! -d "$workspace" ]; then
    echo "[${ctx}] First run — creating ${root}/{home/agent,workspace}" >&2
    mkdir -p "$agent_home" "$workspace"
    sudo chown 1001:1001 "$agent_home" "$workspace"
  fi

  local env_extra=()

  # Mempalace auto-init on first run
  [ ! -d "${agent_home}/.mempalace/palace" ] && env_extra+=(-e "MEMPALACE_AUTO_INIT=true")

  # Do NOT seed Claude OAuth credentials from the host. Claude Code's OAuth
  # refresh tokens are single-use — sharing one refresh token across host +
  # containers causes whichever client rotates second to 401. Each context
  # gets its own /login on first use (persisted via the bind-mounted
  # ~/<ctx>/home/agent/.claude/.credentials.json). See anthropics/claude-code#24317.

  # --- Universal forwards from host env ---
  # These are safe to leak to every context: they're either harness-neutral
  # (GEMINI_API_KEY for any harness that supports google) or already host-global
  # (GITLAB_TOKEN).
  local var
  for var in \
      ANTHROPIC_API_KEY \
      GEMINI_API_KEY \
      OPENAI_API_KEY \
      GROQ_API_KEY \
      GITLAB_TOKEN GITLAB_HOST; do
    [ -n "${!var:-}" ] && env_extra+=(-e "${var}=${!var}")
  done

  # agy (Antigravity CLI) auth: forward the host's Google AI Pro OAuth token so the `agy`
  # leaf is pre-authenticated (init-agy.sh materializes it). Google refresh tokens are
  # reusable, so copying does NOT break the host (unlike Claude's single-use refresh tokens).
  # Ignored by non-agy leaves; the persisted token in <ctx>/home/agent also survives across runs.
  if [ -f "$HOME/.gemini/antigravity-cli/antigravity-oauth-token" ]; then
    env_extra+=(-e "AGY_OAUTH_TOKEN=$(cat "$HOME/.gemini/antigravity-cli/antigravity-oauth-token")")
  fi

  # --- Per-context EXTRA_ENV array (defined in vf-launchers-context.sh) ---
  # Use nameref to read the context's array cleanly.
  local ee_name="${ctx^^}_EXTRA_ENV"
  if declare -p "$ee_name" >/dev/null 2>&1; then
    local -n _ee_ref="$ee_name"
    env_extra+=("${_ee_ref[@]}")
    unset -n _ee_ref
  fi

  # --- Per-context EXTRA_MOUNTS array (vfdev uses this for docker.sock) ---
  local extra_mounts=()
  local em_name="${ctx^^}_EXTRA_MOUNTS"
  if declare -p "$em_name" >/dev/null 2>&1; then
    local -n _em_ref="$em_name"
    extra_mounts=("${_em_ref[@]}")
    unset -n _em_ref
  fi

  # Shared MCP network if it exists
  local net_args=()
  docker network inspect vafi-mcp >/dev/null 2>&1 && net_args=(--network vafi-mcp)

  echo "[${ctx}] launching ${image} in ${root}" >&2

  # Auto-detect TTY: -it for interactive user use, -i only for piped/tested use.
  local io_flags=(-i)
  [ -t 0 ] && [ -t 1 ] && io_flags=(-i -t)

  # Default command: drop straight into the harness CLI via connect.sh
  # (dispatches per $VF_HARNESS — pi --continue, gemini --yolo).
  # User can still get a shell with `ogcli claude bash` or pass any other command.
  if [ $# -eq 0 ]; then
    set -- /opt/vf-harness/connect.sh
  fi

  docker run "${io_flags[@]}" --rm \
    --name "${ctx}dev-$$-$(date +%s)" \
    "${net_args[@]}" \
    "${env_extra[@]}" \
    "${extra_mounts[@]}" \
    -v "${agent_home}:/home/agent" \
    -v "${workspace}:/workspace" \
    "$image" \
    "$@"
}

# --- Context launchers ------------------------------------------------------

# ViloForge — ~/VF, default claude, nested docker
# Flags like --resume / --continue are forwarded to claude: vfdev --resume
# Harness selection still works: vfdev pi, vfdev gemini
vfdev() {
  local VF_EXTRA_MOUNTS=(-v /var/run/docker.sock:/var/run/docker.sock)
  case "${1:-}" in
    ""|-*)
      # _vafi_dev_run consumes the first "claude" as the harness token; the second
      # "claude" becomes the actual CMD so init.sh execs "claude --dangerously-skip-permissions [flags]".
      _vafi_dev_run vf "$HOME/VF" claude claude claude --dangerously-skip-permissions "$@" ;;
    *)
      _vafi_dev_run vf "$HOME/VF" claude "$@" ;;
  esac
}

# OptiscanGroup — ~/OG, default claude
ogcli() {
  _vafi_dev_run og "$HOME/OG" claude "$@"
}

# OptiscanGroup DR — ~/DR, default claude
ogdr() {
  _vafi_dev_run dr "$HOME/DR" claude "$@"
}

# Pi-first context — ~/PI, default pi (historical z.ai setup)
pidev() {
  _vafi_dev_run pi "$HOME/PI" pi "$@"
}

# --- Context-specific secrets (not in repo) ---
[ -f "$HOME/.claude/vf-launchers-context.sh" ] && . "$HOME/.claude/vf-launchers-context.sh"
