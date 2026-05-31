"""Agent configuration from environment variables."""

import os
from dataclasses import dataclass, field


@dataclass
class AgentConfig:
    """Configuration for a vafi controller instance.

    All values read from VF_* environment variables with sensible defaults.
    """

    agent_id: str = ""
    agent_role: str = "executor"
    agent_tags: list[str] = field(default_factory=lambda: ["executor"])

    vtf_api_url: str = "http://vtf-api.vafi-system.svc.cluster.local:8000"
    vtf_token: str = ""
    poll_interval: int = 30

    # C.3 variables substrate (Vault-backed secret injection at spawn time).
    vault_addr: str = "https://vault.vault.svc:8200"
    vault_skip_verify: bool = False
    # Path to a CA bundle that signs the Vault serving cert. When set (and
    # skip_verify is false) the reader verifies TLS against this CA instead of
    # the system trust store — lets pods trust the internal vault-ca without
    # disabling verification. Empty => fall back to the system trust store.
    vault_ca_cert: str = ""
    controller_env: str = "dev"

    task_timeout: int = 600
    max_rework: int = 3
    max_turns: int = 50
    heartbeat_interval: int = 300

    sessions_dir: str = "/sessions"

    harness: str = "claude"
    pi_provider: str = "anthropic"
    pi_model: str = "claude-sonnet-4-20250514"

    cxdb_url: str = ""
    cxdb_public_url: str = ""

    pod_name: str | None = None

    # vfobs observability emission (WG5-min T1). Default OFF — the
    # controller behaves exactly as today unless explicitly enabled
    # AND configured. Turning it on is a config flip, no code change.
    vfobs_emit_enabled: bool = False
    vfobs_emit_url: str = ""
    vfobs_emit_token: str = ""

    @classmethod
    def from_env(cls) -> "AgentConfig":
        tags_str = os.environ.get("VF_AGENT_TAGS", "executor")
        tags = [t.strip() for t in tags_str.split(",") if t.strip()]

        return cls(
            agent_id=os.environ.get("VF_AGENT_ID", ""),
            agent_role=os.environ.get("VF_AGENT_ROLE", "executor"),
            agent_tags=tags,
            vtf_api_url=os.environ.get("VF_VTF_API_URL", cls.vtf_api_url),
            vtf_token=os.environ.get("VF_VTF_TOKEN", ""),
            vault_addr=os.environ.get("VF_VAULT_ADDR", cls.vault_addr),
            vault_skip_verify=os.environ.get("VF_VAULT_SKIP_VERIFY", "").lower() in ("1", "true"),
            vault_ca_cert=os.environ.get("VF_VAULT_CA", ""),
            controller_env=os.environ.get("VF_CONTROLLER_ENV", cls.controller_env),
            poll_interval=int(os.environ.get("VF_POLL_INTERVAL", "30")),
            task_timeout=int(os.environ.get("VF_TASK_TIMEOUT", "600")),
            max_rework=int(os.environ.get("VF_MAX_REWORK", "3")),
            max_turns=int(os.environ.get("VF_MAX_TURNS", "50")),
            heartbeat_interval=int(os.environ.get("VF_HEARTBEAT_INTERVAL", "300")),
            sessions_dir=os.environ.get("VF_SESSIONS_DIR", "/sessions"),
            harness=os.environ.get("VF_HARNESS", "claude"),
            pi_provider=os.environ.get("VF_PI_PROVIDER", "anthropic"),
            pi_model=os.environ.get("VF_PI_MODEL", "claude-sonnet-4-20250514"),
            cxdb_url=os.environ.get("VF_CXDB_URL", ""),
            cxdb_public_url=os.environ.get("VF_CXDB_PUBLIC_URL", ""),
            pod_name=os.environ.get("POD_NAME"),
            vfobs_emit_enabled=os.environ.get(
                "VFOBS_EMIT_ENABLED", ""
            ).lower() in ("1", "true", "yes"),
            vfobs_emit_url=os.environ.get("VFOBS_EMIT_URL", ""),
            vfobs_emit_token=os.environ.get("VFOBS_EMIT_TOKEN", ""),
        )

    def display(self) -> str:
        lines = [
            "vafi controller configuration:",
            f"  agent_id:           {self.agent_id or '(auto)'}",
            f"  agent_role:         {self.agent_role}",
            f"  agent_tags:         {', '.join(self.agent_tags)}",
            f"  vtf_api_url:        {self.vtf_api_url}",
            f"  vtf_token:          {'***' if self.vtf_token else '(none)'}",
            f"  poll_interval:      {self.poll_interval}s",
            f"  task_timeout:       {self.task_timeout}s",
            f"  max_rework:         {self.max_rework}",
            f"  max_turns:          {self.max_turns}",
            f"  heartbeat_interval: {self.heartbeat_interval}s",
            f"  sessions_dir:       {self.sessions_dir}",
            f"  harness:            {self.harness}",
            f"  cxdb_url:          {self.cxdb_url or '(disabled)'}",
            f"  cxdb_public_url:   {self.cxdb_public_url or '(same as cxdb_url)'}",
        ]
        if self.harness == "pi":
            lines.insert(-2, f"  pi_provider:        {self.pi_provider}")
            lines.insert(-2, f"  pi_model:           {self.pi_model}")
        return "\n".join(lines)
