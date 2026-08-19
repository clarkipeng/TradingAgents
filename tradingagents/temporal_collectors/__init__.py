"""Optional public-data collectors that write into the temporal evidence store."""

from .gdelt import GdeltImportResult, GdeltResponseError, import_gdelt_articles
from .hn_algolia import (
    HackerNewsArchiveResponseError,
    HackerNewsImportResult,
    import_hacker_news_stories,
)
from .media_store import MediaStoreImportResult, import_media_store_posts
from .reddit_archive import (
    RedditArchiveImportResult,
    RedditArchiveResponseError,
    import_reddit_archive,
)
from .sec_edgar import SecEdgarImportResult, import_sec_edgar_filings
from .wayback import WaybackImportResult, import_wayback_captures

__all__ = [
    "GdeltImportResult",
    "GdeltResponseError",
    "HackerNewsArchiveResponseError",
    "HackerNewsImportResult",
    "MediaStoreImportResult",
    "RedditArchiveImportResult",
    "RedditArchiveResponseError",
    "SecEdgarImportResult",
    "WaybackImportResult",
    "import_gdelt_articles",
    "import_hacker_news_stories",
    "import_media_store_posts",
    "import_reddit_archive",
    "import_sec_edgar_filings",
    "import_wayback_captures",
]
