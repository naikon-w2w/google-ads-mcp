# Keyword Planner

The `planning` namespace exposes read-only keyword research tools. The first
tool, `planning_generate_keyword_ideas`, returns ideas and historical metrics
from `KeywordPlanIdeaService.GenerateKeywordIdeas`. It does not create plans,
campaigns, ads or budgets. Standalone historical-metric and forecast tools
are not implemented yet.

## Access and credentials

Use a Google Cloud project with the Google Ads API enabled and Basic or
Standard access. Explorer access does not include Keyword Planning services.
The Google identity must have access to the target Ads customer account.
For manager-account access, configure `GOOGLE_ADS_LOGIN_CUSTOMER_ID` or supply
`login_customer_id` to the tool. The target `customer_id` is the advertising
account whose planning data is requested, not automatically the manager ID.

For a local user-authorized test:

1. In the selected project's Google Auth Platform, create a **Desktop app**
   OAuth client. Select **Use this client for an AI-powered agent** for the
   MCP workflow. Keep traditional application integrations on separate clients.
2. Download the client JSON outside this repository. Add the Google Ads
   `https://www.googleapis.com/auth/adwords` scope to the consent configuration.
3. Obtain Application Default Credentials (ADC) using that client. Using an
   isolated gcloud configuration avoids replacing other local ADC:

   ```shell
   mkdir -p "$HOME/.config/google-ads-mcp/gcloud"
   chmod 700 "$HOME/.config/google-ads-mcp"
   CLOUDSDK_CONFIG="$HOME/.config/google-ads-mcp/gcloud" \
     gcloud auth application-default login \
       --client-id-file=/absolute/path/to/oauth-client.json \
       --scopes=https://www.googleapis.com/auth/adwords,https://www.googleapis.com/auth/cloud-platform
   ```

4. Complete consent as the Google user with Ads account access. Point
   `GOOGLE_APPLICATION_CREDENTIALS` at the **ADC file printed by gcloud**, not
   the downloaded OAuth client JSON. Store both files outside version control.
5. Run the server with `GOOGLE_ADS_MCP_TRANSPORT=streamable-http` and connect
   your MCP client to the loopback URL documented in the README.

This local HTTP mode uses server-side Google credentials and has no caller
authentication. Do not publish the endpoint directly. A shared deployment
must authenticate callers and enforce their permitted accounts at its
gateway or use the existing OAuth proxy mode.

## Tool inputs

Targeting is explicit: supply `language` as a `languageConstants/ID` resource
and `geo_target_constants` as up to ten `geoTargetConstants/ID` resources.
An empty geo list deliberately requests worldwide data. Look up constants
through the existing search tool rather than guessing IDs.

Exactly one seed mode is selected:

| Inputs | Seed |
| --- | --- |
| `keywords` | One to twenty seed keywords |
| `url` | A single page |
| `keywords` and `url` | Keywords together with a page |
| `site_url` | A whole site; cannot be combined with the other seeds |

URLs must be absolute HTTP(S) URLs without embedded credentials. They are
passed to Google; this server does not fetch seed URLs itself.

Example arguments (replace the synthetic customer ID with an accessible
account; the language and geography below target English in the US):

```json
{
  "customer_id": "1234567890",
  "language": "languageConstants/1000",
  "geo_target_constants": ["geoTargetConstants/2840"],
  "keywords": ["water filter", "water filtration"],
  "keyword_plan_network": "GOOGLE_SEARCH",
  "page_size": 20
}
```

The default network is Google Search without search partners. Adult keywords
are excluded by default. `page_size` defaults to 50 and is limited to 1–100.
The response contains `results`, `next_page_token`, `total_size` and targeting
metadata. An empty next-page token means there is no further page. Request
another page explicitly with otherwise identical arguments; the tool does
not automatically consume every API page.

## Interpreting metrics and handling limits

Results retain Google's protobuf JSON representation: int64 metrics are
decimal strings, enum values are names, and unavailable optional metrics are
omitted. An omitted search volume is not zero. Bids ending in `_micros` are
millionths of the customer's currency; retrieve `customer.currency_code`
separately when displaying monetary values.

Competition measures advertising competition, not organic SEO difficulty.
Search volumes are estimates for Google's default historical period,
generally the past twelve months. Google may combine close variants.

Each tool call makes one planning RPC with a 30-second timeout and automatic
retries disabled. Planning quotas are stricter than reporting quotas. Callers
should pace requests and reuse results when appropriate. This first version
does not provide a shared rate limiter or persistent results cache. API error
responses expose error codes and a request ID, without echoing raw Google
error messages that could contain submitted data.

The namespace can be renamed or disabled using `tools_config.yaml`, following
the same rules as `customers`, `search` and `metadata`.

## Verification

The unit tests use actual Google Ads protobuf messages and a mocked API
service. They check all seed variants, targeting, one-page pagination,
unavailable metrics, validation and sanitized errors. The HTTP test uses a
real loopback server and MCP client with a mocked Google service. Neither
test requires Google credentials or proves a Cloud project's live API access.

For a live acceptance test, resolve the target language and geography,
request a small page of ideas for an authorized customer and check the
returned metrics. A successful tools/list call alone does not verify Google
authorization or Keyword Planner access.

## References

- [Google Ads access levels](https://developers.google.com/google-ads/api/docs/api-policy/access-levels)
- [Keyword Planning](https://developers.google.com/google-ads/api/docs/keyword-planning/overview)
- [Single-user OAuth setup](https://developers.google.com/google-ads/api/docs/oauth/single-user-authentication)
- [OAuth clients for AI agents](https://support.google.com/cloud/answer/15549257)
