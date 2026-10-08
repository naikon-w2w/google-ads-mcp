"""Exercise discovery and a planning call over a real loopback HTTP socket."""

import asyncio
import socket
import unittest
from unittest.mock import patch

import uvicorn
from fastmcp import Client
from google.ads.googleads.v25.services.types.keyword_plan_idea_service import (
    GenerateKeywordIdeaResponse,
)

from ads_mcp.server import mcp


class TestStreamableHttp(unittest.IsolatedAsyncioTestCase):
    async def test_discovery_and_planning_call(self):
        sock = socket.socket()
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
        server = uvicorn.Server(
            uvicorn.Config(
                mcp.http_app(path="/mcp", transport="streamable-http"),
                log_level="critical",
                access_log=False,
                lifespan="on",
            )
        )
        task = asyncio.create_task(server.serve(sockets=[sock]))

        async def wait_for_startup():
            while not server.started:
                if task.done():
                    await task
                    self.fail("HTTP server exited before startup")
                await asyncio.sleep(0.01)

        try:
            await asyncio.wait_for(wait_for_startup(), timeout=10)
            with patch("ads_mcp.utils.get_googleads_service") as service:
                service.return_value.generate_keyword_ideas.return_value = (
                    GenerateKeywordIdeaResponse(
                        results=[{"text": "water filter"}]
                    )
                )
                async with Client(
                    f"http://127.0.0.1:{port}/mcp", timeout=10
                ) as client:
                    names = {t.name for t in await client.list_tools()}
                    self.assertIn("planning_generate_keyword_ideas", names)
                    self.assertIn("search_search", names)
                    result = await client.call_tool(
                        "planning_generate_keyword_ideas",
                        {
                            "customer_id": "1234567890",
                            "language": "languageConstants/1000",
                            "geo_target_constants": [],
                            "keywords": ["water filter"],
                        },
                    )
                    self.assertFalse(result.is_error)
                    self.assertEqual(
                        result.data["results"], [{"text": "water filter"}]
                    )
                    service.return_value.generate_keyword_ideas.assert_called_once()
        finally:
            server.should_exit = True
            try:
                await asyncio.wait_for(task, timeout=10)
            finally:
                sock.close()
