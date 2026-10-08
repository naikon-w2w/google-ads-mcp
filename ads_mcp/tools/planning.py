# Copyright 2026 Naikon Oy.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Read-only Keyword Planner tools with explicit targeting and pagination."""

import re
from typing import Annotated, Any, Literal
from urllib.parse import urlsplit

from fastmcp import FastMCP
from fastmcp.exceptions import ToolError
from google.ads.googleads.errors import GoogleAdsException
from google.ads.googleads.v25.services.types.keyword_plan_idea_service import (
    GenerateKeywordIdeasRequest,
)
from google.api_core.exceptions import GoogleAPICallError
from google.auth.exceptions import DefaultCredentialsError, RefreshError
from google.protobuf.json_format import MessageToDict
from mcp.types import ToolAnnotations
from pydantic import Field, validate_call

from ads_mcp import utils

planning_mcp = FastMCP("planning")

CustomerId = Annotated[
    str, Field(pattern=r"^(?:[0-9]{10}|[0-9]{3}-[0-9]{3}-[0-9]{4})$")
]
Keyword = Annotated[str, Field(min_length=1, max_length=80)]
Language = Annotated[str, Field(pattern=r"^languageConstants/[0-9]+$")]
GeoTarget = Annotated[str, Field(pattern=r"^geoTargetConstants/[0-9]+$")]
Network = Literal["GOOGLE_SEARCH", "GOOGLE_SEARCH_AND_PARTNERS"]


def _validate_seed_url(value: str) -> str:
    """Validate a URL sent to Google; the server does not fetch it locally."""
    try:
        parsed = urlsplit(value)
        valid = (
            parsed.scheme in ("http", "https")
            and parsed.hostname
            and not parsed.username
            and not parsed.password
            and not re.search(r"\s", value)
        )
        parsed.port  # Reject malformed ports before making an API call.
    except ValueError:
        valid = False
    if not valid:
        raise ToolError("Use an absolute HTTP(S) seed URL without credentials.")
    return value


@planning_mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
@validate_call
def generate_keyword_ideas(
    customer_id: CustomerId,
    language: Language,
    geo_target_constants: Annotated[list[GeoTarget], Field(max_length=10)],
    keywords: (
        Annotated[list[Keyword], Field(min_length=1, max_length=20)] | None
    ) = None,
    url: Annotated[str, Field(min_length=1, max_length=2048)] | None = None,
    site_url: (
        Annotated[str, Field(min_length=1, max_length=2048)] | None
    ) = None,
    keyword_plan_network: Network = "GOOGLE_SEARCH",
    include_adult_keywords: bool = False,
    page_size: Annotated[int, Field(strict=True, ge=1, le=100)] = 50,
    page_token: Annotated[str, Field(max_length=8192)] = "",
    login_customer_id: CustomerId | None = None,
) -> dict[str, Any]:
    """Discover keyword ideas and historical search metrics, one page at a time.

    Requires Google Ads Basic or Standard access and an accessible customer
    account. This tool does not create plans, campaigns or ads.

    Supply keywords, a page URL, keywords plus a page URL, OR a site URL.
    site_url cannot be combined with keywords or url. Supply language and
    geographic resource names explicitly; an empty geo list means worldwide.
    Resolve IDs using the language_constant and geo_target_constant resources
    with the search tool rather than guessing them.

    At most page_size ideas (1-100, default 50) are requested. Pass the returned
    next_page_token with otherwise identical arguments for another page.
    This tool never automatically fetches subsequent pages or retries calls.

    Results use Google Ads protobuf JSON field names. Optional missing metrics
    are omitted, not converted to zero. Int64 metrics are decimal strings;
    enum values are names. Bid fields ending in _micros are millionths of the
    customer account's currency (look up customer.currency_code separately).
    Competition measures advertising competition, not organic SEO difficulty.
    Historical metrics use Google's default period, generally 12 months, and
    are estimates. Idea results may merge close variants of seed keywords.

    login_customer_id optionally selects the manager account used for access;
    otherwise the server's GOOGLE_ADS_LOGIN_CUSTOMER_ID setting is used.
    """
    if site_url is not None and (keywords is not None or url is not None):
        raise ToolError("site_url cannot be combined with keywords or url.")
    if keywords is None and url is None and site_url is None:
        raise ToolError("Supply keywords, url, or site_url.")
    if keywords is not None:
        keywords = [keyword.strip() for keyword in keywords]
        if any(not keyword for keyword in keywords):
            raise ToolError("Keywords must not be blank.")
    if url is not None:
        url = _validate_seed_url(url)
    if site_url is not None:
        site_url = _validate_seed_url(site_url)

    request = GenerateKeywordIdeasRequest(
        customer_id=utils.clean_customer_id(customer_id),
        language=language,
        geo_target_constants=geo_target_constants,
        keyword_plan_network=keyword_plan_network,
        include_adult_keywords=include_adult_keywords,
        page_size=page_size,
        page_token=page_token,
    )
    if site_url is not None:
        request.site_seed.site = site_url
    elif keywords is not None and url is not None:
        request.keyword_and_url_seed.keywords = keywords
        request.keyword_and_url_seed.url = url
    elif keywords is not None:
        request.keyword_seed.keywords = keywords
    else:
        request.url_seed.url = url

    try:
        service = utils.get_googleads_service(
            "KeywordPlanIdeaService", login_customer_id=login_customer_id
        )
        response = service.generate_keyword_ideas(
            request=request, retry=None, timeout=30.0
        )
        # Iterating the pager itself silently fetches every remaining page.
        # Its results attribute contains only the current response's page.
        results = [
            MessageToDict(
                type(idea).pb(idea),
                preserving_proto_field_name=True,
                always_print_fields_with_no_presence=False,
            )
            for idea in response.results
        ]
        return {
            "customer_id": request.customer_id,
            "language": language,
            "geo_target_constants": list(geo_target_constants),
            "keyword_plan_network": keyword_plan_network,
            "results": results,
            "next_page_token": response.next_page_token,
            "total_size": int(response.total_size),
        }
    except GoogleAdsException as error:
        # Google error messages may echo submitted keywords or URLs. Return
        # only structured error codes and the request ID for diagnostics.
        codes = [
            MessageToDict(
                type(item.error_code).pb(item.error_code),
                preserving_proto_field_name=True,
            )
            for item in error.failure.errors
        ]
        raise ToolError(
            f"Google Ads request failed ({error.request_id}): {codes}"
        ) from None
    except (DefaultCredentialsError, RefreshError):
        raise ToolError(
            "Google Ads credentials are missing or require reauthorization."
        ) from None
    except GoogleAPICallError as error:
        raise ToolError(
            f"Google Ads request failed: {type(error).__name__}. "
            "Check access and quota before retrying."
        ) from None
