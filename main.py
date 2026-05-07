"""AgentCore-compatible entrypoint for the Strands web crawler agent."""

import json
import os

os.environ["BYPASS_TOOL_CONSENT"] = "true"

from strands import Agent
from strands.models.bedrock import BedrockModel

from tools import (
    fetch_page,
    get_crawl_status,
    init_crawl_state,
    store_to_knowledge_base,
    get_crawl_results,
)
from config import CrawlerConfig

# Configure the agent
MODEL_ID = os.environ.get("MODEL_ID", "global.anthropic.claude-sonnet-4-6")
REGION = os.environ.get("AWS_REGION", "ca-central-1")

SYSTEM_PROMPT = """You are a web page crawler agent. Your job is to fetch a list of URLs \
provided by the user, extract useful content from each page, and store it in a knowledge base.

## Your Workflow

1. **Fetch** — Use `fetch_page` for each URL in the list provided.
   Process them one by one or in small batches.

2. **Follow TOC links** — If a page returns thin content with a `follow_these_links` list,
   it means the page is a table of contents. Fetch those linked pages to get the actual content.

3. **Evaluate** — After fetching, decide if the content is valuable enough to store.
   Skip pages with thin content or that failed to load.

4. **Store** — Use `store_to_knowledge_base` to save content from pages that have
   substantive text. Include the page title and full extracted content.

5. **Status** — Use `get_crawl_status` to report progress after processing all URLs.

## Guidelines

- Process ALL URLs provided — do not skip any unless they fail to load
- When a page is flagged as a table of contents with `follow_these_links`, fetch those links
- If a fetch fails (timeout, 404, robots.txt block), report it and move on
- Always store content with the page's actual title
- Do not search for additional URLs — only process the ones given and TOC sub-pages
- Report a summary at the end: how many succeeded, failed, and were stored
"""


def create_crawler_agent(config: CrawlerConfig) -> Agent:
    """Create and return a configured crawler agent."""
    init_crawl_state(config)

    model = BedrockModel(
        model_id=config.model_id,
        region_name=config.region,
    )

    agent = Agent(
        model=model,
        tools=[fetch_page, store_to_knowledge_base, get_crawl_status],
        system_prompt=SYSTEM_PROMPT,
    )

    return agent


# --- AgentCore Runtime Integration ---

from bedrock_agentcore.runtime import BedrockAgentCoreApp

app = BedrockAgentCoreApp()


@app.entrypoint
def invoke(payload, context):
    """Handler for agent invocation via AgentCore Runtime.

    Expected payload:
    {
        "urls": ["https://example.com/page1", "https://example.com/page2", ...]
    }
    """
    urls = payload.get("urls", [])

    # Support legacy "prompt" field with comma/newline-separated URLs
    if not urls:
        prompt = payload.get("prompt", "")
        if prompt:
            urls = [u.strip() for u in prompt.replace(",", "\n").splitlines() if u.strip().startswith("http")]

    if not urls:
        return {"error": "Missing 'urls' list in payload. Provide a JSON array of URLs to crawl."}

    config = CrawlerConfig(
        model_id=MODEL_ID,
        region=REGION,
        max_pages=len(urls) * 3,  # Allow headroom for TOC sub-pages
        output_dir="/tmp/kb_output",
        request_delay=0.5,
    )

    agent = create_crawler_agent(config)

    url_list = "\n".join(f"- {url}" for url in urls)
    prompt = f"""Fetch and store content from the following {len(urls)} URLs:

{url_list}

Process each URL: fetch the page, evaluate the content, and store it if it has substantive text.
Report which URLs succeeded and which failed at the end."""

    try:
        result = agent(prompt)
        response_text = str(result.message) if hasattr(result, 'message') else str(result)
    except Exception as e:
        error_msg = str(e)
        if "modelStreamErrorException" in error_msg or "ToolUse" in error_msg:
            response_text = "Crawl completed with partial results (model tool-use limit reached)"
        else:
            response_text = f"Agent error: {error_msg}"

    results = get_crawl_results()

    # Trigger Knowledge Base ingestion if documents were stored
    ingestion_job_id = None
    if results and config.s3_bucket and config.knowledge_base_id:
        ingestion_job_id = _trigger_kb_ingestion(config)

    return {
        "response": response_text,
        "urls_requested": len(urls),
        "documents_stored": len(results),
        "documents": [
            {"title": r["title"], "url": r["source_url"]}
            for r in results
        ],
        "ingestion_job_id": ingestion_job_id,
    }


def _trigger_kb_ingestion(config):
    """Trigger a Bedrock Knowledge Base ingestion job after crawling."""
    try:
        import boto3
        client = boto3.client("bedrock-agent", region_name=config.region)
        response = client.start_ingestion_job(
            knowledgeBaseId=config.knowledge_base_id,
            dataSourceId=config.kb_data_source_id,
        )
        job_id = response["ingestionJob"]["ingestionJobId"]
        print(f"KB ingestion job started: {job_id}")
        return job_id
    except Exception as e:
        print(f"Warning: Could not start KB ingestion: {e}")
        return None


if __name__ == "__main__":
    app.run()
