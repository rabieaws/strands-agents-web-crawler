"""
=============================================================
  Web Crawler Agent — Demo Script
  Crawls Government of Canada URLs → S3 → Knowledge Base
=============================================================

Usage:
    python demo.py

What it does:
    1. Sends a list of GC URLs to the deployed AgentCore agent
    2. Agent fetches each page, extracts content, stores to S3
    3. Triggers Bedrock Knowledge Base ingestion (auto-vectorization)
    4. Prints a clear report showing what was stored vs rejected

Filter rules enforced:
    ✅ Only .gc.ca and .canada.ca domains allowed
    ❌ PDFs, images, and binary files are rejected
"""

import json
import uuid
import time
import boto3

# --- Configuration ---
AGENT_ARN = "arn:aws:bedrock-agentcore:ca-central-1:612673515314:runtime/web_crawler_agent-5RgrV350Vx"
REGION = "ca-central-1"
KB_ID = "OMI9U6VVI8"

# --- URLs to crawl ---
URLS = [
    "https://www.csagroup.org/wp-content/uploads/2430328.pdf",
    "https://laws-lois.justice.gc.ca/eng/ACTS/L-2/index.html",
    "https://laws-lois.justice.gc.ca/eng/regulations/sor-86-304/index.html",
    "https://laws-lois.justice.gc.ca/eng/acts/h-6/",
    "https://laws-lois.justice.gc.ca/eng/acts/p-38.2/",
    "https://laws-lois.justice.gc.ca/eng/acts/P-38.2/page-1.html",
    "https://www.tbs-sct.canada.ca/pol/doc-eng.aspx?id=32691",
    "https://laws-lois.justice.gc.ca/eng/acts/G-6/",
    "https://laws-lois.justice.gc.ca/eng/regulations/C.R.C.,_c._887/",
    "https://www.tbs-sct.canada.ca/pol/doc-eng.aspx?id=32593",
    "https://www.tbs-sct.canada.ca/pol/doc-eng.aspx?id=32621",
    "https://www.tbs-sct.gc.ca/pol/doc-eng.aspx?id=12614&section=HTML",
    "https://publications.gc.ca/collections/collection_2022/cnrc-nrc/NR24-28-2020-eng.pdf",
    "https://www.tbs-sct.canada.ca/pol/doc-eng.aspx?id=32692",
]


def main():
    print("=" * 70)
    print("  WEB CRAWLER AGENT — DEMO")
    print("  Crawl GC URLs → S3 → Bedrock Knowledge Base")
    print("=" * 70)
    print()
    print(f"  Agent:  {AGENT_ARN.split('/')[-1]}")
    print(f"  Region: {REGION}")
    print(f"  KB:     {KB_ID}")
    print()
    print(f"  URLs to process: {len(URLS)}")
    print("-" * 70)
    for i, url in enumerate(URLS, 1):
        # Predict what will be filtered
        marker = "❌ PDF" if url.lower().endswith(".pdf") else ""
        if not marker and not any(d in url for d in [".gc.ca", ".canada.ca"]):
            marker = "❌ Non-GC domain"
        print(f"  {i:2}. {marker:15} {url}")
    print("-" * 70)
    print()

    input("  Press ENTER to start crawling...")
    print()

    # Invoke the agent
    session_id = f"demo-{uuid.uuid4().hex[:28]}-pad"
    client = boto3.client("bedrock-agentcore", region_name=REGION)

    payload = {"urls": URLS}

    print("  ⏳ Invoking agent (this takes 30-60 seconds)...")
    start = time.time()

    try:
        response = client.invoke_agent_runtime(
            agentRuntimeArn=AGENT_ARN,
            runtimeSessionId=session_id,
            payload=json.dumps(payload).encode("utf-8"),
        )

        streaming_body = response.get("response")
        result = streaming_body.read().decode("utf-8") if hasattr(streaming_body, "read") else str(streaming_body)
        elapsed = time.time() - start

        parsed = json.loads(result)

        print(f"  ✅ Done in {elapsed:.1f}s")
        print()
        print("=" * 70)
        print("  RESULTS")
        print("=" * 70)
        print()
        print(f"  URLs submitted:    {parsed.get('urls_requested', len(URLS))}")
        print(f"  Documents stored:  {parsed.get('documents_stored', 0)}")
        print(f"  Ingestion job:     {parsed.get('ingestion_job_id', 'N/A')}")
        print()

        docs = parsed.get("documents", [])
        if docs:
            print("  📄 Stored documents:")
            print("  " + "-" * 66)
            for i, doc in enumerate(docs, 1):
                print(f"  {i}. {doc['title']}")
                print(f"     {doc['url']}")
            print()

        # Print the agent's narrative response (trimmed)
        agent_response = parsed.get("response", "")
        # Extract just the text content from the model response
        if "'text':" in agent_response:
            try:
                import ast
                resp_dict = ast.literal_eval(agent_response)
                text = resp_dict.get("content", [{}])[0].get("text", "")
                if text:
                    print("  📋 Agent Report:")
                    print("  " + "-" * 66)
                    for line in text.split("\\n"):
                        if line.strip():
                            print(f"  {line}")
                    print()
            except Exception:
                pass

        # Show KB ingestion status
        ingestion_id = parsed.get("ingestion_job_id")
        if ingestion_id:
            print("  🔄 Knowledge Base Ingestion:")
            print("  " + "-" * 66)
            print(f"  Job ID: {ingestion_id}")
            print(f"  Check status:")
            print(f"    aws bedrock-agent get-ingestion-job \\")
            print(f"      --knowledge-base-id {KB_ID} \\")
            print(f"      --data-source-id YUSXLRU7QW \\")
            print(f"      --ingestion-job-id {ingestion_id} \\")
            print(f"      --region {REGION}")
            print()

    except Exception as e:
        print(f"  ❌ Error: {e}")
        import traceback
        traceback.print_exc()

    finally:
        # Stop session
        try:
            client.stop_runtime_session(
                agentRuntimeArn=AGENT_ARN,
                runtimeSessionId=session_id,
            )
        except Exception:
            pass

    print("=" * 70)
    print("  DEMO COMPLETE")
    print("=" * 70)


if __name__ == "__main__":
    main()
