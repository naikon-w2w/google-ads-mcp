"""Historical volumes preserve targeting, variants and missing metric values."""

import unittest
from unittest.mock import MagicMock, patch

from fastmcp import Client
from fastmcp.exceptions import ToolError
from google.ads.googleads.errors import GoogleAdsException
from google.ads.googleads.v25.errors.types.errors import GoogleAdsFailure
from google.ads.googleads.v25.services.types.keyword_plan_idea_service import (
    GenerateKeywordHistoricalMetricsResponse,
)
from google.api_core.exceptions import ResourceExhausted
from google.auth.exceptions import RefreshError
from pydantic import ValidationError

from ads_mcp.tools.planning import (
    generate_keyword_historical_metrics,
    planning_mcp,
)


def args(**overrides):
    return {
        "customer_id": "123-456-7890",
        "language": "languageConstants/1000",
        "geo_target_constants": ["geoTargetConstants/2840"],
        "keywords": ["water filter", "water filters", "unknown"],
        **overrides,
    }


class TestHistoricalMetrics(unittest.TestCase):
    def setUp(self):
        patcher = patch("ads_mcp.utils.get_googleads_service")
        self.service = patcher.start().return_value
        self.addCleanup(patcher.stop)
        self.call = self.service.generate_keyword_historical_metrics
        self.call.return_value = GenerateKeywordHistoricalMetricsResponse()

    def test_targeting_trim_and_single_bounded_call(self):
        result = generate_keyword_historical_metrics(
            **args(
                keywords=[" water filter "] * 100,
                geo_target_constants=[],
                keyword_plan_network="GOOGLE_SEARCH_AND_PARTNERS",
                login_customer_id="999-888-7777",
            )
        )
        self.call.assert_called_once()
        sent = self.call.call_args.kwargs
        self.assertEqual(sent["request"].customer_id, "1234567890")
        self.assertEqual(sent["request"].language, "languageConstants/1000")
        self.assertEqual(list(sent["request"].geo_target_constants), [])
        self.assertEqual(list(sent["request"].keywords), ["water filter"] * 100)
        self.assertEqual(
            sent["request"].keyword_plan_network.name,
            "GOOGLE_SEARCH_AND_PARTNERS",
        )
        self.assertIsNone(sent["retry"])
        self.assertEqual(sent["timeout"], 30.0)
        self.assertEqual(result["geo_target_constants"], [])
        self.assertEqual(result["requested_keyword_count"], 100)
        self.assertEqual(result["returned_result_count"], 0)
        self.assertEqual(result["results"], [])

    def test_merged_variants_and_monthly_missing_vs_zero_metrics(self):
        self.call.return_value = GenerateKeywordHistoricalMetricsResponse(
            results=[
                {
                    "text": "water filter",
                    "close_variants": ["water filters"],
                    "keyword_metrics": {
                        "avg_monthly_searches": 0,
                        "competition": "LOW",
                        "low_top_of_page_bid_micros": 1234567,
                        "monthly_search_volumes": [
                            {
                                "year": 2026,
                                "month": "JANUARY",
                                "monthly_searches": 0,
                            },
                            {"year": 2026, "month": "FEBRUARY"},
                        ],
                    },
                },
                {"text": "unknown"},
            ]
        )
        result = generate_keyword_historical_metrics(**args())
        self.assertEqual(result["requested_keyword_count"], 3)
        self.assertEqual(result["returned_result_count"], 2)
        first, unknown = result["results"]
        self.assertEqual(first["close_variants"], ["water filters"])
        metrics = first["keyword_metrics"]
        self.assertEqual(metrics["avg_monthly_searches"], "0")
        self.assertEqual(metrics["low_top_of_page_bid_micros"], "1234567")
        self.assertNotIn("competition_index", metrics)
        self.assertEqual(
            metrics["monthly_search_volumes"][0]["monthly_searches"], "0"
        )
        self.assertNotIn(
            "monthly_searches", metrics["monthly_search_volumes"][1]
        )
        self.assertNotIn("keyword_metrics", unknown)

    def test_invalid_input_never_calls_api(self):
        for override in [
            {"keywords": []},
            {"keywords": None},
            {"keywords": ["\t "]},
            {"keywords": ["x"] * 101},
            {"keywords": ["x" * 81]},
            {"customer_id": "123"},
            {"language": "fi"},
            {"geo_target_constants": ["Finland"]},
            {"geo_target_constants": ["geoTargetConstants/1"] * 11},
            {"keyword_plan_network": "UNKNOWN"},
            {"login_customer_id": "bad"},
        ]:
            with self.subTest(override=override):
                with self.assertRaises((ValidationError, ToolError)):
                    generate_keyword_historical_metrics(**args(**override))
        self.call.assert_not_called()

    def test_errors_are_sanitized_and_not_retried(self):
        failure = GoogleAdsFailure(
            errors=[
                {
                    "error_code": {
                        "authorization_error": "USER_PERMISSION_DENIED"
                    },
                    "message": "private keyword data",
                }
            ]
        )
        for error in [
            GoogleAdsException(
                MagicMock(), MagicMock(), failure, "request-123"
            ),
            RefreshError("private refresh token"),
            ResourceExhausted("private request data"),
        ]:
            with self.subTest(error=type(error).__name__):
                self.call.reset_mock()
                self.call.side_effect = error
                with self.assertRaises(ToolError) as caught:
                    generate_keyword_historical_metrics(**args())
                self.assertNotIn("private", str(caught.exception))
                self.call.assert_called_once()


class TestHistoricalProtocol(unittest.IsolatedAsyncioTestCase):
    async def test_mcp_schema_and_structured_output(self):
        with patch("ads_mcp.utils.get_googleads_service") as service:
            service.return_value.generate_keyword_historical_metrics.return_value = GenerateKeywordHistoricalMetricsResponse(
                results=[{"text": "water filter"}]
            )
            async with Client(planning_mcp) as client:
                tool = next(
                    t
                    for t in await client.list_tools()
                    if t.name == "generate_keyword_historical_metrics"
                )
                self.assertTrue(tool.annotations.read_only_hint)
                self.assertEqual(
                    tool.input_schema["properties"]["keywords"]["maxItems"], 100
                )
                self.assertIn("keywords", tool.input_schema["required"])
                result = await client.call_tool(tool.name, args())
                self.assertFalse(result.is_error)
                self.assertEqual(
                    result.data["results"], [{"text": "water filter"}]
                )
