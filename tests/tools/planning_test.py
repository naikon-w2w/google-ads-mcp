"""Keyword Planner request, pagination, metric and error contracts."""

import unittest
from unittest.mock import MagicMock, patch

from fastmcp import Client
from fastmcp.exceptions import ToolError
from google.ads.googleads.errors import GoogleAdsException
from google.ads.googleads.v25.errors.types.errors import GoogleAdsFailure
from google.ads.googleads.v25.services.services.keyword_plan_idea_service.pagers import (
    GenerateKeywordIdeasPager,
)
from google.ads.googleads.v25.services.types.keyword_plan_idea_service import (
    GenerateKeywordIdeaResponse,
    GenerateKeywordIdeasRequest,
)
from google.api_core.exceptions import ResourceExhausted
from google.auth.exceptions import DefaultCredentialsError, RefreshError
from pydantic import ValidationError

from ads_mcp.tools.planning import generate_keyword_ideas, planning_mcp


def request_args(**overrides):
    return {
        "customer_id": "123-456-7890",
        "language": "languageConstants/1000",
        "geo_target_constants": ["geoTargetConstants/2840"],
        "keywords": ["water filter"],
        **overrides,
    }


class TestKeywordIdeas(unittest.TestCase):
    def setUp(self):
        patcher = patch("ads_mcp.utils.get_googleads_service")
        self.get_service = patcher.start()
        self.addCleanup(patcher.stop)
        self.service = self.get_service.return_value
        self.service.generate_keyword_ideas.return_value = (
            GenerateKeywordIdeaResponse()
        )

    def test_seed_variants_and_targeting(self):
        seeds = [
            ({"keywords": ["water filter"]}, "keyword_seed"),
            ({"keywords": None, "url": "https://example.com/"}, "url_seed"),
            ({"url": "https://example.com/"}, "keyword_and_url_seed"),
            (
                {"keywords": None, "site_url": "https://example.com/"},
                "site_seed",
            ),
        ]
        for seed, expected in seeds:
            with self.subTest(seed=expected):
                generate_keyword_ideas(
                    **request_args(
                        **seed,
                        login_customer_id="999-888-7777",
                        include_adult_keywords=True,
                        keyword_plan_network="GOOGLE_SEARCH_AND_PARTNERS",
                    )
                )
                kwargs = self.service.generate_keyword_ideas.call_args.kwargs
                request = kwargs["request"]
                self.assertEqual(
                    GenerateKeywordIdeasRequest.pb(request).WhichOneof("seed"),
                    expected,
                )
                self.assertEqual(request.customer_id, "1234567890")
                self.assertEqual(request.language, "languageConstants/1000")
                self.assertEqual(
                    list(request.geo_target_constants),
                    ["geoTargetConstants/2840"],
                )
                self.assertEqual(request.page_size, 50)
                self.assertTrue(request.include_adult_keywords)
                self.assertEqual(
                    request.keyword_plan_network.name,
                    "GOOGLE_SEARCH_AND_PARTNERS",
                )
                self.assertIsNone(kwargs["retry"])
                self.assertEqual(kwargs["timeout"], 30.0)
                self.get_service.assert_called_with(
                    "KeywordPlanIdeaService",
                    login_customer_id="999-888-7777",
                )

    def test_current_page_only_and_missing_metrics_not_zero(self):
        response = GenerateKeywordIdeaResponse(
            results=[
                {
                    "text": "water filter",
                    "keyword_idea_metrics": {
                        "avg_monthly_searches": 0,
                        "competition": "LOW",
                        "high_top_of_page_bid_micros": 1234567,
                    },
                },
                {"text": "unknown volume"},
            ],
            next_page_token="next-page",
            total_size=300,
        )
        fetch_next = MagicMock(side_effect=AssertionError("Fetched next page"))
        pager = GenerateKeywordIdeasPager(
            fetch_next, GenerateKeywordIdeasRequest(), response
        )
        self.service.generate_keyword_ideas.return_value = pager
        result = generate_keyword_ideas(
            **request_args(page_size=2, page_token="previous-page")
        )
        fetch_next.assert_not_called()
        self.assertEqual(result["next_page_token"], "next-page")
        self.assertEqual(result["total_size"], 300)
        self.assertEqual(len(result["results"]), 2)
        metrics = result["results"][0]["keyword_idea_metrics"]
        self.assertEqual(metrics["avg_monthly_searches"], "0")
        self.assertEqual(metrics["competition"], "LOW")
        self.assertEqual(metrics["high_top_of_page_bid_micros"], "1234567")
        self.assertNotIn("competition_index", metrics)
        self.assertNotIn("keyword_idea_metrics", result["results"][1])
        sent = self.service.generate_keyword_ideas.call_args.kwargs["request"]
        self.assertEqual(sent.page_token, "previous-page")

    def test_worldwide_targeting_is_explicit_and_returned(self):
        result = generate_keyword_ideas(**request_args(geo_target_constants=[]))
        self.assertEqual(result["geo_target_constants"], [])
        self.assertEqual(result["keyword_plan_network"], "GOOGLE_SEARCH")
        self.assertEqual(result["next_page_token"], "")
        self.assertEqual(result["results"], [])

    def test_invalid_input_does_not_call_google(self):
        invalid = [
            {"keywords": None},
            {"keywords": []},
            {"keywords": [" "]},
            {"keywords": ["x"] * 21},
            {"keywords": ["x" * 81]},
            {"customer_id": "123"},
            {"customer_id": "abc1234567890"},
            {"language": "fi"},
            {"geo_target_constants": ["Finland"]},
            {"geo_target_constants": ["geoTargetConstants/1"] * 11},
            {"site_url": "https://example.com/"},
            {"url": "file:///etc/passwd"},
            {"url": "https://user:password@example.com/"},
            {"url": "https://example.com:invalid/"},
            {"url": "https://example.com/has space"},
            {"page_size": 0},
            {"page_size": 101},
            {"page_size": True},
            {"keyword_plan_network": "UNKNOWN"},
        ]
        for overrides in invalid:
            with self.subTest(overrides=overrides):
                with self.assertRaises((ValidationError, ToolError)):
                    generate_keyword_ideas(**request_args(**overrides))
        self.get_service.assert_not_called()

    def test_google_error_does_not_echo_submitted_data(self):
        failure = GoogleAdsFailure(
            errors=[
                {
                    "error_code": {
                        "authorization_error": "USER_PERMISSION_DENIED"
                    },
                    "message": "private keyword and URL",
                }
            ]
        )
        self.service.generate_keyword_ideas.side_effect = GoogleAdsException(
            MagicMock(), MagicMock(), failure, "request-123"
        )
        with self.assertRaises(ToolError) as error:
            generate_keyword_ideas(**request_args())
        self.assertIn("USER_PERMISSION_DENIED", str(error.exception))
        self.assertIn("request-123", str(error.exception))
        self.assertNotIn("private keyword", str(error.exception))

    def test_credentials_and_quota_errors_are_sanitized(self):
        for failure in [
            DefaultCredentialsError("private credential path"),
            RefreshError("private refresh token"),
            ResourceExhausted("private request details"),
        ]:
            with self.subTest(failure=type(failure).__name__):
                self.get_service.side_effect = failure
                with self.assertRaises(ToolError) as error:
                    generate_keyword_ideas(**request_args())
                self.assertNotIn("private", str(error.exception))


class TestPlanningProtocol(unittest.IsolatedAsyncioTestCase):
    async def test_mcp_schema_and_tool_call(self):
        with patch("ads_mcp.utils.get_googleads_service") as service:
            service.return_value.generate_keyword_ideas.return_value = (
                GenerateKeywordIdeaResponse(results=[{"text": "water filter"}])
            )
            async with Client(planning_mcp) as client:
                tools = await client.list_tools()
                tool = next(
                    t for t in tools if t.name == "generate_keyword_ideas"
                )
                self.assertTrue(tool.annotations.read_only_hint)
                self.assertEqual(
                    tool.input_schema["properties"]["page_size"]["maximum"], 100
                )
                self.assertIn("language", tool.input_schema["required"])
                result = await client.call_tool(
                    "generate_keyword_ideas", request_args()
                )
                self.assertFalse(result.is_error)
                self.assertEqual(
                    result.data["results"][0]["text"], "water filter"
                )
