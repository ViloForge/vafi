"""Minimal stdlib HTTP server exposing the onboarding probe (C.3 Slice 5 #5).

`POST /admin/probe?project=<id>` with `Authorization: Token <vtf-token>` →
JSON probe report (see `controller.probe.run_probe`). Runs in a daemon thread
alongside the controller poll loop. Stdlib `http.server` only — the executor
image carries no web framework, and the probe is a low-traffic operator tool.

The Vault read uses the executor SA (the pod's identity); the caller's token only
authorizes them for the project (validated against the vtf API). The report
carries result + byte length per variable, **never values**.
"""
from __future__ import annotations

import json
import logging
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

import httpx

from controller.probe import ProbeError, run_probe

logger = logging.getLogger("controller.probe")


def build_materializer(config):
    """Construct a materializer whose registry holds the executor VaultBackend —
    the same wiring `VariablesStage.from_config` uses at spawn."""
    from controller.variables_stage import resolve_vault_verify
    from variables.literal import LiteralBackend
    from variables.materializer import VariableMaterializer
    from variables.registry import BackendRegistry
    from variables.vault import VaultBackend
    from variables.vault_reader import KubernetesVaultReader

    reader = KubernetesVaultReader(config.vault_addr, verify=resolve_vault_verify(config))
    registry = BackendRegistry()
    registry.register("vault", VaultBackend(reader))
    registry.register("literal", LiteralBackend())
    return VariableMaterializer(registry)


def _make_handler(config, materializer_factory, now):
    controller_id = config.pod_name or config.agent_id or "vafi-controller"

    class _Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt, *args):  # route through logging, not stderr
            logger.info("probe %s", fmt % args)

        def _send(self, status, payload):
            body = json.dumps(payload).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):  # noqa: N802 - stdlib naming
            if urlparse(self.path).path == "/admin/health":
                return self._send(200, {"status": "ok"})
            return self._send(404, {"error": "not found"})

        def do_POST(self):  # noqa: N802 - stdlib naming
            parsed = urlparse(self.path)
            if parsed.path != "/admin/probe":
                return self._send(404, {"error": "not found"})
            project = (parse_qs(parsed.query).get("project") or [""])[0]
            if not project:
                return self._send(400, {"error": "project query param required"})
            auth = self.headers.get("Authorization", "")
            token = auth[6:].strip() if auth.lower().startswith("token ") else ""
            if not token:
                return self._send(401, {"error": "Token authorization required"})
            try:
                with httpx.Client(timeout=15.0) as client:
                    report = run_probe(
                        project,
                        token,
                        vtf_url=config.vtf_api_url,
                        materializer=materializer_factory(config),
                        controller_env=config.controller_env,
                        controller_id=controller_id,
                        now=now,
                        http=client,
                    )
                return self._send(200, report)
            except ProbeError as e:
                return self._send(e.status, {"error": e.message})
            except Exception as e:  # noqa: BLE001 - report, don't crash the thread
                logger.warning("probe failed for project %s: %s", project, e)
                return self._send(500, {"error": "probe failed", "detail": str(e)[:200]})

    return _Handler


def start_probe_server(config, port, *, now, materializer_factory=build_materializer):
    """Start the probe HTTP server in a daemon thread; return the server."""
    handler = _make_handler(config, materializer_factory, now)
    server = ThreadingHTTPServer(("0.0.0.0", port), handler)
    thread = threading.Thread(target=server.serve_forever, name="probe-server", daemon=True)
    thread.start()
    logger.info("probe server listening on :%d (POST /admin/probe)", port)
    return server
