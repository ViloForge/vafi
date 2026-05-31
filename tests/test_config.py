"""Tests for AgentConfig."""

import os

from controller.config import AgentConfig


class TestAgentConfig:
    def test_defaults(self):
        config = AgentConfig()
        assert config.agent_role == "executor"
        assert config.poll_interval == 30
        assert config.max_rework == 3
        assert config.agent_tags == ["executor"]

    def test_from_env(self, monkeypatch):
        monkeypatch.setenv("VF_AGENT_ID", "executor-7")
        monkeypatch.setenv("VF_AGENT_ROLE", "judge")
        monkeypatch.setenv("VF_AGENT_TAGS", "judge,claude,fast")
        monkeypatch.setenv("VF_VTF_API_URL", "http://vtf:8000")
        monkeypatch.setenv("VF_POLL_INTERVAL", "10")
        monkeypatch.setenv("VF_TASK_TIMEOUT", "120")
        monkeypatch.setenv("VF_MAX_REWORK", "5")

        config = AgentConfig.from_env()

        assert config.agent_id == "executor-7"
        assert config.agent_role == "judge"
        assert config.agent_tags == ["judge", "claude", "fast"]
        assert config.vtf_api_url == "http://vtf:8000"
        assert config.poll_interval == 10
        assert config.task_timeout == 120
        assert config.max_rework == 5

    def test_from_env_defaults(self):
        config = AgentConfig.from_env()
        assert config.agent_id == ""
        assert config.agent_role == "executor"
        assert config.poll_interval == 30

    def test_vault_ca_cert_from_env(self, monkeypatch):
        monkeypatch.setenv("VF_VAULT_CA", "/etc/vafi/vault-ca/ca.crt")
        config = AgentConfig.from_env()
        assert config.vault_ca_cert == "/etc/vafi/vault-ca/ca.crt"

    def test_vault_ca_cert_defaults_empty(self):
        config = AgentConfig.from_env()
        assert config.vault_ca_cert == ""

    def test_config_reads_pod_name_from_env(self, monkeypatch):
        monkeypatch.setenv("POD_NAME", "vafi-executor-abc123")
        config = AgentConfig.from_env()
        assert config.pod_name == "vafi-executor-abc123"

    def test_config_pod_name_defaults_to_none(self):
        config = AgentConfig.from_env()
        assert config.pod_name is None

    def test_harness_defaults_to_claude(self):
        config = AgentConfig.from_env()
        assert config.harness == "claude"
        assert config.pi_provider == "anthropic"
        assert config.pi_model == "claude-sonnet-4-20250514"

    def test_harness_from_env(self, monkeypatch):
        monkeypatch.setenv("VF_HARNESS", "pi")
        monkeypatch.setenv("VF_PI_PROVIDER", "anthropic")
        monkeypatch.setenv("VF_PI_MODEL", "claude-haiku-3-20240307")
        config = AgentConfig.from_env()
        assert config.harness == "pi"
        assert config.pi_provider == "anthropic"
        assert config.pi_model == "claude-haiku-3-20240307"

    def test_display(self):
        config = AgentConfig(agent_id="test-1", agent_role="executor")
        output = config.display()
        assert "agent_id:" in output
        assert "test-1" in output
        assert "executor" in output
        assert "harness:" in output
        assert "claude" in output

    def test_display_pi_shows_provider_and_model(self):
        config = AgentConfig(agent_id="test-pi", harness="pi", pi_provider="anthropic", pi_model="sonnet")
        output = config.display()
        assert "pi_provider:" in output
        assert "pi_model:" in output

    def test_display_claude_hides_pi_fields(self):
        config = AgentConfig(agent_id="test-claude", harness="claude")
        output = config.display()
        assert "pi_provider:" not in output
        assert "pi_model:" not in output
