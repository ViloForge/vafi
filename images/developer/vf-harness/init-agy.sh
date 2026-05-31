# vf-harness init-agy.sh — Antigravity CLI (agy) setup.
# Sourced by /opt/vf-harness/init.sh. Runs as agent user.
#
# agy is Google's Gemini-CLI successor. It authenticates with the operator's GOOGLE account
# (Google AI Pro/Ultra) via an OAuth token — NOT an API key. So agy follows the claude-style
# "OAuth-persist-in-home" model, NOT the gemini API-key model:
#
# Auth sources (first match wins):
#   AGY_OAUTH_TOKEN  — the full token JSON, materialized to
#                      ~/.gemini/antigravity-cli/antigravity-oauth-token (forwarded by the
#                      launcher from the host's token; Google refresh tokens are reusable, so
#                      copying is safe — unlike Claude's single-use refresh tokens).
#   (persisted)      — a token already present in the bind-mounted home from a prior `agy`
#                      login in this context.
# If neither is present, agy will prompt for an interactive login on first use.

AGY_TOKEN_DIR="$HOME/.gemini/antigravity-cli"
mkdir -p "$AGY_TOKEN_DIR"

if [ -n "${AGY_OAUTH_TOKEN:-}" ]; then
  printf '%s' "$AGY_OAUTH_TOKEN" > "$AGY_TOKEN_DIR/antigravity-oauth-token"
  chmod 600 "$AGY_TOKEN_DIR/antigravity-oauth-token"
  unset AGY_OAUTH_TOKEN
  echo >&2 "[agy] Auth: OAuth token materialized from AGY_OAUTH_TOKEN (Google AI Pro)"
elif [ -f "$AGY_TOKEN_DIR/antigravity-oauth-token" ]; then
  echo >&2 "[agy] Auth: persisted OAuth token in \$HOME/.gemini/antigravity-cli"
else
  echo >&2 "[agy] WARNING: no agy auth — run 'agy' once to log in (Google AI Pro), or set AGY_OAUTH_TOKEN"
fi

export VF_HARNESS=agy
