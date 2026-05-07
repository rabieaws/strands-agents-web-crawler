"""Configuration for the Strands web crawler agent."""

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class CrawlerConfig:
    """Configuration for the web crawler agent."""

    # Model settings
    model_id: str = "global.anthropic.claude-sonnet-4-6"
    region: str = "ca-central-1"

    # Crawling limits
    max_pages: int = 20
    max_search_iterations: int = 5
    max_link_depth: int = 2
    request_delay: float = 1.0  # seconds between requests

    # Content extraction
    min_content_length: int = 200  # minimum chars to consider a page useful
    max_content_length: int = 50000  # truncate very long pages

    # Politeness
    respect_robots_txt: bool = True
    user_agent: str = "StrandsWebCrawler/1.0 (Knowledge Base Builder)"

    # Output
    output_dir: str = "./kb_output"
    output_format: str = "markdown"  # markdown or json

    # Optional S3/Bedrock KB integration
    s3_bucket: Optional[str] = "web-crawler-kb-docs-612673515314-ca-central-1"
    s3_prefix: str = "crawled-docs/"
    knowledge_base_id: Optional[str] = "OMI9U6VVI8"
    kb_data_source_id: Optional[str] = "YUSXLRU7QW"

    # Domains to exclude from crawling
    excluded_domains: list = field(default_factory=lambda: [
        "facebook.com",
        "twitter.com",
        "instagram.com",
        "linkedin.com",
        "youtube.com",
    ])

    # Domain allowlist — if non-empty, ONLY these domains are permitted
    # URLs must end with one of these suffixes to be crawled
    allowed_domains: list = field(default_factory=lambda: [
        ".gc.ca",
        ".canada.ca",
    ])

    # File extensions to reject (binary/non-HTML assets)
    excluded_extensions: list = field(default_factory=lambda: [
        ".pdf", ".png", ".jpg", ".jpeg", ".gif", ".svg",
        ".webp", ".ico", ".bmp", ".tiff", ".tif",
        ".mp4", ".mp3", ".wav", ".avi", ".mov",
        ".zip", ".tar", ".gz", ".rar",
        ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx",
    ])
