"""
=============================================================
  Query the Knowledge Base — Test Script
=============================================================

Usage:
    python query_kb.py "What are the employer obligations under the Canada Labour Code?"
    python query_kb.py "What does the Policy on People Management cover?"
    python query_kb.py "What are the requirements for real property management?"

This uses Bedrock's RetrieveAndGenerate API to:
1. Search the vectorized knowledge base for relevant chunks
2. Generate an answer grounded in the retrieved content
3. Show source citations
"""

import sys
import json
import boto3

REGION = "ca-central-1"
KB_ID = "OMI9U6VVI8"
MODEL_ARN = f"arn:aws:bedrock:{REGION}::foundation-model/anthropic.claude-sonnet-4-6-v1:0"

# Use the global inference profile for the generation model
MODEL_ID = f"arn:aws:bedrock:{REGION}:612673515314:inference-profile/global.anthropic.claude-sonnet-4-6"


def query(question: str):
    client = boto3.client("bedrock-agent-runtime", region_name=REGION)

    print(f"\n{'='*70}")
    print(f"  QUESTION: {question}")
    print(f"{'='*70}\n")

    # Option 1: Retrieve and Generate (full RAG — retrieves + answers)
    try:
        response = client.retrieve_and_generate(
            input={"text": question},
            retrieveAndGenerateConfiguration={
                "type": "KNOWLEDGE_BASE",
                "knowledgeBaseConfiguration": {
                    "knowledgeBaseId": KB_ID,
                    "modelArn": MODEL_ID,
                },
            },
        )

        # Print the generated answer
        answer = response["output"]["text"]
        print(f"  ANSWER:\n")
        for line in answer.split("\n"):
            print(f"    {line}")
        print()

        # Print citations
        citations = response.get("citations", [])
        if citations:
            print(f"  SOURCES ({len(citations)} citations):")
            print(f"  {'-'*66}")
            seen_urls = set()
            for citation in citations:
                refs = citation.get("retrievedReferences", [])
                for ref in refs:
                    location = ref.get("location", {})
                    s3_uri = location.get("s3Location", {}).get("uri", "")
                    # Get source URL from metadata (uploaded alongside the doc)
                    metadata = ref.get("metadata", {})
                    source_url = metadata.get("source_url", "")
                    title = metadata.get("title", "")
                    # Handle case where value might be a dict (e.g. {"value": "..."})
                    if isinstance(source_url, dict):
                        source_url = source_url.get("stringValue", source_url.get("value", ""))
                    if isinstance(title, dict):
                        title = title.get("stringValue", title.get("value", ""))
                    display = source_url if source_url else s3_uri
                    if display not in seen_urls:
                        seen_urls.add(display)
                        if title:
                            print(f"    • {title}")
                            print(f"      {display}")
                        else:
                            print(f"    • {display}")
            print()

    except Exception as e:
        print(f"  RetrieveAndGenerate failed: {e}")
        print(f"\n  Falling back to Retrieve-only...\n")
        _retrieve_only(client, question)


def _retrieve_only(client, question: str):
    """Fallback: just retrieve relevant chunks without generating an answer."""
    try:
        response = client.retrieve(
            knowledgeBaseId=KB_ID,
            retrievalQuery={"text": question},
            retrievalConfiguration={
                "vectorSearchConfiguration": {
                    "numberOfResults": 5,
                }
            },
        )

        results = response.get("retrievalResults", [])
        print(f"  RETRIEVED CHUNKS ({len(results)} results):\n")

        for i, result in enumerate(results, 1):
            score = result.get("score", 0)
            text = result.get("content", {}).get("text", "")[:300]
            location = result.get("location", {}).get("s3Location", {}).get("uri", "")

            print(f"  {i}. [Score: {score:.3f}]")
            print(f"     Source: {location}")
            print(f"     Content: {text}...")
            print()

    except Exception as e:
        print(f"  Retrieve failed: {e}")


def main():
    if len(sys.argv) > 1:
        question = " ".join(sys.argv[1:])
    else:
        # Default demo questions
        questions = [
            "What are the employer obligations under the Canada Labour Code?",
            "What does the Directive on Management of Real Property require?",
            "What are the key principles of the Policy on People Management?",
        ]
        print("No question provided. Pick one:\n")
        for i, q in enumerate(questions, 1):
            print(f"  {i}. {q}")
        print()
        choice = input("Enter number (or type your own question): ").strip()

        if choice.isdigit() and 1 <= int(choice) <= len(questions):
            question = questions[int(choice) - 1]
        elif choice:
            question = choice
        else:
            question = questions[0]

    query(question)


if __name__ == "__main__":
    main()
