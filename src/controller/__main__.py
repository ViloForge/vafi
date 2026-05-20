"""vafi controller entry point.

Usage: python -m controller
"""

import asyncio
import logging
import os

from controller.config import AgentConfig
from controller.controller import Controller
from vtf_sdk.async_client import AsyncVtfClient
from controller.worksources.vtf import VtfWorkSource
from controller.worksources.fault_injection import maybe_wrap_faults


def setup_logging():
    """Configure logging for the controller."""
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        handlers=[logging.StreamHandler()]
    )


async def main():
    """Main entry point for the vafi controller."""
    setup_logging()
    logger = logging.getLogger(__name__)

    # Load configuration from environment
    config = AgentConfig.from_env()
    logger.info("Starting vafi controller")
    logger.info(f"\n{config.display()}")

    # Create AsyncVtfClient with bootstrap token for registration
    vtf_client = AsyncVtfClient(url=config.vtf_api_url, token=config.vtf_token or "")

    try:
        async with vtf_client:
            # Create VtfWorkSource
            work_source = VtfWorkSource(client=vtf_client, tags=config.agent_tags, pod_name=config.pod_name)

            # Fail-loud verification harness: wraps the work source only when
            # VF_FAULT_INJECT is set (no-op + byte-identical in prod). See
            # docs/fault-injection-DESIGN.md.
            work_source = maybe_wrap_faults(work_source)

            # Create Controller
            controller = Controller(work_source=work_source, config=config)

            # Wire up summarizer if cxdb is configured
            if config.cxdb_url:
                from cxdb.client import CxdbClient
                from cxdb.summarizer import Summarizer, SummarizerConfig

                cxdb_client = CxdbClient(
                    base_url=config.cxdb_url,
                    timeout=10.0,
                )

                class VtfSummaryStore:
                    """Adapter: stores execution summary via SDK task update."""
                    def __init__(self, client: AsyncVtfClient):
                        self._client = client
                    async def store_summary(self, task_id: str, summary: dict) -> None:
                        await self._client.tasks.update(task_id, execution_summary=summary)

                # NL generator (Phase B) — uses Haiku via Anthropic API.
                # Claude-harness pods set ANTHROPIC_AUTH_TOKEN (claude-code CLI
                # convention); pi-harness pods set ANTHROPIC_API_KEY (anthropic
                # SDK convention). Accept either.
                nl_generator = None
                anthropic_key = (
                    os.environ.get("ANTHROPIC_AUTH_TOKEN", "")
                    or os.environ.get("ANTHROPIC_API_KEY", "")
                )
                anthropic_url = os.environ.get("ANTHROPIC_BASE_URL", "")
                if anthropic_key and anthropic_url:
                    import httpx as _httpx
                    from cxdb.nl_summary import HaikuNLGenerator
                    nl_generator = HaikuNLGenerator(
                        http_client=_httpx.AsyncClient(),
                        base_url=anthropic_url,
                        api_key=anthropic_key,
                    )
                    logger.info("NL summary generator enabled (Haiku)")

                summarizer = Summarizer(
                    cxdb=cxdb_client,
                    store=VtfSummaryStore(vtf_client),
                    config=SummarizerConfig(
                        cxdb_public_url=config.cxdb_public_url or config.cxdb_url,
                    ),
                    nl_generator=nl_generator,
                )
                controller.set_summarizer(summarizer)
                logger.info("Summarizer enabled (cxdb configured)")

            # Run the controller
            await controller.run()

    except Exception as e:
        logger.error(f"Failed to start controller: {e}", exc_info=True)
        return 1

    return 0


if __name__ == "__main__":
    exit_code = asyncio.run(main())
