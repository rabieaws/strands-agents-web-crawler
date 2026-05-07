"""Custom tools for the Strands web crawler agent."""

import hashlib
import json
import os
import re
import time
from datetime import datetime, timezone
from typing import Optional
from urllib.parse import urljoin, urlparse
from urllib.robotparser import RobotFileParser

import requests
from bs4 import BeautifulSoup
from strands import tool

from config import CrawlerConfig

# Module-level state for tracking crawl progress
_visited_urls: set = set()
_crawl_results: list = []
_page_cache: dict = {}  # url -> full extracted content
_config: CrawlerConfig = CrawlerConfig()
_last_request_time: float = 0.0


def init_crawl_state(config: CrawlerConfig):
    """Initialize/reset the crawl state with given config."""
    global _visited_urls, _crawl_results, _page_cache, _config
    _visited_urls = set()
    _crawl_results = []
    _page_cache = {}
    _config = config


def get_crawl_results() -> list:
    """Return all crawled results."""
    return _crawl_results


def _respect_rate_limit():
    """Enforce delay between requests."""
    global _last_request_time
    elapsed = time.time() - _last_request_time
    if elapsed < _config.request_delay:
        time.sleep(_config.request_delay - elapsed)
    _last_request_time = time.time()


def _is_allowed_by_robots(url: str) -> bool:
    """Check if URL is allowed by robots.txt."""
    if not _config.respect_robots_txt:
        return True

    parsed = urlparse(url)
    robots_url = f"{parsed.scheme}://{parsed.netloc}/robots.txt"

    try:
        rp = RobotFileParser()
        rp.set_url(robots_url)
        rp.read()
        return rp.can_fetch(_config.user_agent, url)
    except Exception:
        # If we can't read robots.txt, allow by default
        return True


def _is_excluded_domain(url: str) -> bool:
    """Check if URL belongs to an excluded domain."""
    parsed = urlparse(url)
    domain = parsed.netloc.lower()
    return any(excluded in domain for excluded in _config.excluded_domains)


def _is_allowed_domain(url: str) -> bool:
    """Check if URL belongs to an allowed domain. If allowlist is empty, all domains are allowed."""
    if not _config.allowed_domains:
        return True
    parsed = urlparse(url)
    domain = parsed.netloc.lower()
    return any(domain.endswith(allowed) for allowed in _config.allowed_domains)


def _has_excluded_extension(url: str) -> bool:
    """Check if URL points to an excluded file type (PDF, image, etc.)."""
    if not _config.excluded_extensions:
        return False
    parsed = urlparse(url)
    path = parsed.path.lower()
    return any(path.endswith(ext) for ext in _config.excluded_extensions)


def _extract_text_from_html(html: str, url: str) -> dict:
    """Extract meaningful text content from HTML."""
    soup = BeautifulSoup(html, "html.parser")

    # Remove script, style, nav, footer, header elements
    for element in soup(["script", "style", "nav", "footer", "header", "aside", "noscript"]):
        element.decompose()

    # Extract title
    title = ""
    if soup.title and soup.title.string:
        title = soup.title.string.strip()

    # Extract meta description
    meta_desc = ""
    meta_tag = soup.find("meta", attrs={"name": "description"})
    if meta_tag and meta_tag.get("content"):
        meta_desc = meta_tag["content"].strip()

    # Extract main content (prefer article/main tags)
    main_content = soup.find("main") or soup.find("article") or soup.find("body")
    if main_content:
        text = main_content.get_text(separator="\n", strip=True)
    else:
        text = soup.get_text(separator="\n", strip=True)

    # Clean up whitespace
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    text = "\n".join(lines)

    # Truncate if too long
    if len(text) > _config.max_content_length:
        text = text[: _config.max_content_length] + "\n\n[Content truncated]"

    # Extract links for potential follow-up crawling
    links = []
    for a_tag in soup.find_all("a", href=True):
        href = a_tag["href"]
        absolute_url = urljoin(url, href)
        # Only include http/https links
        if absolute_url.startswith(("http://", "https://")):
            link_text = a_tag.get_text(strip=True)
            links.append({"url": absolute_url, "text": link_text[:100]})

    return {
        "title": title,
        "meta_description": meta_desc,
        "content": text,
        "links": links[:50],  # Limit to 50 links
    }


@tool
def fetch_page(url: str) -> str:
    """Fetch a web page and extract its text content.

    Downloads the page, strips HTML, and returns clean text content
    along with metadata and outbound links for further crawling.

    Args:
        url: The full URL of the page to fetch.

    Returns:
        JSON string with extracted title, content, metadata, and links found on the page.
    """
    # Check limits
    if len(_visited_urls) >= _config.max_pages:
        return json.dumps({
            "error": f"Maximum page limit reached ({_config.max_pages}). Stop crawling.",
            "pages_crawled": len(_visited_urls),
        })

    if url in _visited_urls:
        return json.dumps({"error": "URL already visited", "url": url})

    if _is_excluded_domain(url):
        return json.dumps({"error": "Domain is in exclusion list", "url": url})

    if not _is_allowed_domain(url):
        parsed = urlparse(url)
        return json.dumps({
            "error": f"Domain '{parsed.netloc}' is not in the allowed domains list. Only Government of Canada domains (.gc.ca, .canada.ca) are permitted.",
            "url": url,
            "allowed_domains": _config.allowed_domains,
        })

    if _has_excluded_extension(url):
        return json.dumps({
            "error": f"URL points to an excluded file type. PDFs, images, and binary assets are not supported.",
            "url": url,
        })

    # Normalize http:// to https://
    if url.startswith("http://"):
        url = "https://" + url[7:]

    # Check robots.txt
    if not _is_allowed_by_robots(url):
        return json.dumps({"error": "Blocked by robots.txt", "url": url})

    # Rate limiting
    _respect_rate_limit()

    try:
        headers = {"User-Agent": _config.user_agent}
        response = requests.get(url, headers=headers, timeout=15, allow_redirects=True)
        response.raise_for_status()

        # Only process HTML content
        content_type = response.headers.get("content-type", "")
        if "text/html" not in content_type:
            return json.dumps({
                "error": f"Not an HTML page (content-type: {content_type})",
                "url": url,
            })

        # Mark as visited
        _visited_urls.add(url)

        # Extract content
        extracted = _extract_text_from_html(response.text, url)

        # Cache full content for later storage
        _page_cache[url] = extracted

        # Check minimum content length
        if len(extracted["content"]) < _config.min_content_length:
            # If the page is thin but has same-domain links, it's likely a TOC page
            # Return the links so the agent can follow them
            same_domain_links = [
                link for link in extracted["links"]
                if _is_allowed_domain(link["url"])
                and not _has_excluded_extension(link["url"])
                and link["url"] not in _visited_urls
            ]

            if same_domain_links:
                return json.dumps({
                    "warning": "Page has thin content but contains links to sub-pages (likely a table of contents). Follow these links to get the actual content.",
                    "url": url,
                    "title": extracted["title"],
                    "content_length": len(extracted["content"]),
                    "follow_these_links": [link["url"] for link in same_domain_links[:20]],
                    "link_count": len(same_domain_links),
                })
            else:
                return json.dumps({
                    "warning": "Page has very little content and no followable links",
                    "url": url,
                    "content_length": len(extracted["content"]),
                    "title": extracted["title"],
                    "content": extracted["content"],
                })

        return json.dumps({
            "url": url,
            "title": extracted["title"],
            "meta_description": extracted["meta_description"],
            "content": extracted["content"][:2000],  # First 2000 chars in response
            "full_content_length": len(extracted["content"]),
            "links_found": len(extracted["links"]),
            "sample_links": extracted["links"][:5],
            "pages_crawled_so_far": len(_visited_urls),
        }, indent=2)

    except requests.exceptions.Timeout:
        return json.dumps({"error": "Request timed out", "url": url})
    except requests.exceptions.HTTPError as e:
        return json.dumps({"error": f"HTTP error: {e.response.status_code}", "url": url})
    except Exception as e:
        return json.dumps({"error": str(e), "url": url})


@tool
def store_to_knowledge_base(url: str, title: str, content: str = "", metadata: str = "") -> str:
    """Store a crawled page's content into the knowledge base.

    Saves the extracted content as a document file. Uses the full cached
    content from fetch_page if available. If S3 is configured, also uploads to S3.

    Args:
        url: The source URL of the content.
        title: The page title.
        content: Optional text content override. If empty, uses cached full content from fetch_page.
        metadata: Optional additional metadata string.

    Returns:
        Confirmation of storage with file path.
    """
    # Use cached full content if available and content arg is short/empty
    if url in _page_cache and (not content or len(content) < 500):
        content = _page_cache[url]["content"]
    elif not content:
        content = "(No content available)"
    # Generate a filename from the URL
    url_hash = hashlib.md5(url.encode()).hexdigest()[:12]
    safe_title = re.sub(r"[^\w\s-]", "", title)[:50].strip().replace(" ", "_")
    filename = f"{safe_title}_{url_hash}"

    # Parse optional metadata
    meta_dict = {}
    if metadata:
        try:
            meta_dict = json.loads(metadata)
        except json.JSONDecodeError:
            meta_dict = {"raw_metadata": metadata}

    # Build document
    doc = {
        "source_url": url,
        "title": title,
        "content": content,
        "crawled_at": datetime.now(timezone.utc).isoformat(),
        "metadata": meta_dict,
    }

    # Store crawl result in memory
    _crawl_results.append(doc)

    # Write to local filesystem
    os.makedirs(_config.output_dir, exist_ok=True)

    if _config.output_format == "markdown":
        filepath = os.path.join(_config.output_dir, f"{filename}.md")
        md_content = f"""# {title}

**Source:** {url}
**Crawled:** {doc['crawled_at']}

---

{content}
"""
        with open(filepath, "w", encoding="utf-8") as f:
            f.write(md_content)
    else:
        filepath = os.path.join(_config.output_dir, f"{filename}.json")
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(doc, f, indent=2, ensure_ascii=False)

    result = {
        "stored": True,
        "local_path": filepath,
        "total_documents_stored": len(_crawl_results),
    }

    # Upload to S3 if configured
    if _config.s3_bucket:
        try:
            import boto3

            s3 = boto3.client("s3", region_name=_config.region)
            s3_key = f"{_config.s3_prefix}{filename}.md" if _config.output_format == "markdown" else f"{_config.s3_prefix}{filename}.json"

            with open(filepath, "rb") as f:
                s3.put_object(Bucket=_config.s3_bucket, Key=s3_key, Body=f.read())

            # Upload metadata file so Bedrock KB shows the source URL in citations
            # Format must match AWS docs: value is an object with type + stringValue
            metadata_key = f"{s3_key}.metadata.json"
            metadata_content = json.dumps({
                "metadataAttributes": {
                    "source_url": {
                        "value": {
                            "type": "STRING",
                            "stringValue": url,
                        },
                        "includeForEmbedding": False,
                    },
                    "title": {
                        "value": {
                            "type": "STRING",
                            "stringValue": title,
                        },
                        "includeForEmbedding": False,
                    },
                }
            })
            s3.put_object(Bucket=_config.s3_bucket, Key=metadata_key, Body=metadata_content.encode("utf-8"))

            result["s3_path"] = f"s3://{_config.s3_bucket}/{s3_key}"
        except Exception as e:
            result["s3_error"] = str(e)

    return json.dumps(result, indent=2)


@tool
def get_crawl_status() -> str:
    """Get the current status of the crawling session.

    Returns statistics about pages visited, content stored, and remaining capacity.

    Returns:
        JSON string with crawl session statistics.
    """
    return json.dumps({
        "pages_visited": len(_visited_urls),
        "max_pages": _config.max_pages,
        "remaining_capacity": _config.max_pages - len(_visited_urls),
        "documents_stored": len(_crawl_results),
        "visited_urls": list(_visited_urls),
    }, indent=2)
