# GC Web Crawler Agent & Knowledge Base

An AI agent that crawls Government of Canada web pages, extracts content, and builds a searchable knowledge base you can query with natural language.

**Give it URLs → it fetches, filters, and stores content → you ask questions and get grounded answers with source citations.**

## Architecture

```
URL List → Filter Rules → AI Agent (fetch & extract) → S3 → Bedrock Knowledge Base → RAG Queries
```

| Component | Technology |
|-----------|-----------|
| Agent Framework | Strands Agents SDK |
| Agent Runtime | Bedrock AgentCore |
| LLM | Claude Sonnet 4.6 (global inference profile) |
| Embeddings | Amazon Titan Embed Text V2 (1024d) |
| Vector Store | OpenSearch Serverless |
| Region | ca-central-1 |

Open `architecture.drawio` in [draw.io](https://app.diagrams.net) for the visual diagram.

## How It Works

1. You provide a list of URLs (via `demo.py` or programmatic invocation)
2. The agent applies filter rules (GC domains only, no PDFs/images)
3. For each allowed URL, it fetches the page and extracts clean text
4. If a page is a table of contents, it follows sub-page links automatically
5. Content is stored as markdown in S3, each file paired with a `.metadata.json` sidecar containing the original source URL and title
6. A Bedrock Knowledge Base ingestion job vectorizes the content and indexes the metadata attributes
7. You query the KB and get answers grounded in the crawled documents — citations show the actual source URLs (not S3 paths) thanks to the metadata sidecars

## Setup

### Prerequisites

- Python 3.11+
- AWS CLI configured with access to Bedrock, S3, and OpenSearch Serverless
- Docker (for AgentCore container deployment)
- [Bedrock AgentCore SDK](https://docs.aws.amazon.com/bedrock/latest/userguide/agentcore.html)

### 1. Create infrastructure (one-time)

```bash
pip install -r requirements.txt
python infra/setup_kb.py
```

This creates: S3 bucket, IAM role, OpenSearch Serverless collection + vector index, Bedrock Knowledge Base, and S3 data source.

Update `config.py` with the output values (KB ID, data source ID, etc.) if they differ from defaults.

### 2. Deploy the agent

```bash
agentcore deploy
```

This builds the Docker container and deploys it to Bedrock AgentCore Runtime.

### 3. Run the crawler

```bash
python demo.py
```

Sends a predefined list of GC URLs to the deployed agent. The agent crawls, stores content in S3, and triggers KB ingestion.

### 4. Query the knowledge base

Wait a few minutes for ingestion to complete, then:

```bash
python query_kb.py "What are the employer obligations under the Canada Labour Code?"
python query_kb.py "What does the Policy on People Management cover?"
```

## Project Structure

```
├── main.py              # AgentCore entrypoint (system prompt, agent config, invocation handler)
├── tools.py             # Agent tools: fetch_page, store_to_knowledge_base, get_crawl_status
├── config.py            # All settings (model, region, filters, KB IDs, allowed domains)
├── demo.py              # Demo script — invokes the deployed agent with sample URLs
├── query_kb.py          # Query the KB with natural language (RetrieveAndGenerate)
├── infra/setup_kb.py    # One-time infrastructure provisioning
├── requirements.txt     # Python dependencies
├── architecture.drawio  # Solution architecture diagram
├── .bedrock_agentcore.yaml                    # AgentCore deployment config
└── .bedrock_agentcore/web_crawler_agent/
    └── Dockerfile                             # Container definition
```

## Metadata Sidecars

For each document uploaded to S3, the agent also uploads a `.metadata.json` sidecar file (e.g., `Policy_on_People_Management_abc123.md.metadata.json`). This file tells Bedrock KB to attach custom attributes to each chunk:

```json
{
  "metadataAttributes": {
    "source_url": {
      "value": { "type": "STRING", "stringValue": "https://www.tbs-sct.canada.ca/pol/doc-eng.aspx?id=32691" },
      "includeForEmbedding": false
    },
    "title": {
      "value": { "type": "STRING", "stringValue": "Policy on People Management" },
      "includeForEmbedding": false
    }
  }
}
```

This ensures that when you query the KB, citations reference the original web URL rather than the S3 object path. The format follows the [AWS Bedrock metadata file specification](https://docs.aws.amazon.com/bedrock/latest/userguide/s3-data-source-connector.html).

## Filter Rules

| Rule | Behavior |
|------|----------|
| Domain allowlist | Only `.gc.ca` and `.canada.ca` domains |
| Extension blocklist | PDFs, images, binary files rejected |
| HTTPS enforcement | `http://` auto-upgraded to `https://` |
| TOC follow-through | Thin pages with links trigger sub-page crawling |
| Robots.txt | Respected by default |
| Rate limiting | Configurable delay between requests |

## Configuration

All settings live in `config.py`. Key options:

| Setting | Default | Purpose |
|---------|---------|---------|
| `allowed_domains` | `.gc.ca`, `.canada.ca` | Only these domains are crawled |
| `excluded_extensions` | `.pdf`, `.png`, etc. | Blocked file types |
| `max_pages` | 20 | Max pages per crawl session |
| `model_id` | `global.anthropic.claude-sonnet-4-6` | LLM for the agent |
| `s3_bucket` | `web-crawler-kb-docs-...` | Where docs are stored |
| `knowledge_base_id` | `OMI9U6VVI8` | Bedrock KB to ingest into |

## Redeploying After Changes

```bash
agentcore deploy --auto-update-on-conflict
```

If you change crawl logic (`tools.py`, `main.py`, `config.py`), you must redeploy for changes to take effect on the agent runtime.
