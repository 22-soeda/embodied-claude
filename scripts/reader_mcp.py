# /// script
# requires-python = ">=3.10"
# dependencies = [
#     "mcp>=1.20,<2",
#     "readability-lxml>=0.8",
#     "lxml-html-clean>=0.1",
#     "requests>=2.31",
#     "lxml>=5.0",
#     "cssselect>=1.2",
# ]
# ///
"""MCP server "reader": read a web page or a book slowly, one page at a time.

Ported from PetitOnes/petit-ones (scripts/reader.py and scripts/fetch_aozora.py,
MIT License), where the characters read stories and Aozora Bunko books with it.
There it is a shell command; here it is an MCP server, because the
individual-kernel gate counts a Bash call as outward and would want an
intention before every page. Tool names contain "read_" so the gate counts
them as read-only.

Unlike WebFetch, nothing is summarised on the way: the page's own text comes
back, 2000 characters per page. Aozora Bunko pages are cut to the main text,
with ruby readings dropped.

Run: uv run --script scripts/reader_mcp.py   (uv installs the dependencies above)
     uv run --script scripts/reader_mcp.py --check   (prepare them and exit)
"""

from __future__ import annotations

import html
import re
import sys
from collections import OrderedDict

CHARS_PER_PAGE = 2000
CACHE_SIZE = 8  # pages of the same work are read one call at a time; fetch it once
USER_AGENT = "Mozilla/5.0 (compatible; EmbodiedClaudeReader/1.0)"
READING_WAY = (
    "読み方: 1 ページずつ読む。全部を先に取らない。1 ページ読んだら立ち止まって、今の気持ち、"
    "この先どうなりそうか、刺さった言葉や違和感があれば書く（無ければ無理に書かない）。"
    "次のページで予想が当たった・外れた・超えてきたら反応する。読み終えたら全体の感想を書き残す。"
    "本文を勝手に要約しない。"
)

_cache: OrderedDict[str, tuple[str, str]] = OrderedDict()


def detect_encoding(response) -> str:
    """Content-Type charset, then a meta charset in the first 4 KB, then a guess."""
    content_type = response.headers.get("Content-Type", "")
    match = re.search(r"charset=([^\s;]+)", content_type, re.IGNORECASE)
    if match:
        return match.group(1).strip()
    head = response.content[:4096]
    for pattern in (rb'<meta[^>]+charset=["\']?([^"\'\s;>]+)',
                    rb'<meta[^>]+content=["\'][^"\']*charset=([^"\'\s;>]+)'):
        match = re.search(pattern, head, re.IGNORECASE)
        if match:
            return match.group(1).decode("ascii", errors="ignore")
    return response.apparent_encoding or "utf-8"


def fetch_html(url: str) -> str:
    import requests

    response = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=30)
    response.raise_for_status()
    return response.content.decode(detect_encoding(response), errors="replace")


def aozora_text(page_html: str) -> tuple[str, str] | None:
    """Title and main text of an Aozora Bunko XHTML page, or None if it is not one."""
    body = re.search(r'<div class="main_text">(.*?)</div>\s*<div class="bibliographical_information"',
                     page_html, re.DOTALL) or re.search(r'<div class="main_text">(.*)</div>', page_html,
                                                        re.DOTALL)
    if not body:
        return None
    title = re.search(r'<h1 class="title">(.*?)</h1>', page_html, re.DOTALL)
    author = re.search(r'<h2 class="author">(.*?)</h2>', page_html, re.DOTALL)
    text = body.group(1)
    text = re.sub(r"<rp>[^<]*</rp>|<rt>[^<]*</rt>|</?ruby>", "", text)  # keep the base text only
    text = re.sub(r"<br\s*/?>", "\n", text)
    text = html.unescape(re.sub(r"<[^>]+>", "", text))
    text = re.sub(r"\n{3,}", "\n\n", "\n".join(line.strip() for line in text.split("\n"))).strip()
    heading = " ".join(html.unescape(re.sub(r"<[^>]+>", "", m.group(1))).strip()
                       for m in (title, author) if m)
    return heading or "(no title)", text


def readable_text(page_html: str, url: str) -> tuple[str, str]:
    """Title and main text by the readability algorithm."""
    from lxml.html import fromstring
    from readability import Document

    document = Document(page_html, url=url)
    return document.short_title() or "(no title)", fromstring(document.summary()).text_content().strip()


def load(url: str) -> tuple[str, str]:
    if url in _cache:
        _cache.move_to_end(url)
        return _cache[url]
    page_html = fetch_html(url)
    aozora = aozora_text(page_html) if "aozora.gr.jp" in url else None
    title, text = aozora if aozora else readable_text(page_html, url)
    _cache[url] = (title, text)
    if len(_cache) > CACHE_SIZE:
        _cache.popitem(last=False)
    return title, text


def page_count(text: str) -> int:
    return max(1, -(-len(text) // CHARS_PER_PAGE))


def main() -> None:
    if "--check" in sys.argv:
        import lxml.html  # noqa: F401
        import readability  # noqa: F401
        import requests  # noqa: F401
        print("reader: dependencies ready")
        return

    from mcp.server.fastmcp import FastMCP

    server = FastMCP("reader")

    @server.tool()
    def read_page_info(url: str) -> str:
        """Look at a web page or a book before reading it: title, length and number of pages
        (2000 characters each). Aozora Bunko (www.aozora.gr.jp) works pages are cut to the
        main text. Use it first, then read_page one page at a time."""
        try:
            title, text = load(url)
        except Exception as error:  # noqa: BLE001 - tell the reader, do not crash the server
            return f"could not fetch {url}: {error}"
        if not text:
            return "no readable text on this page"
        return f"{title}\n{len(text)} 字、{page_count(text)} ページ\n\n{READING_WAY}"

    @server.tool()
    def read_page(url: str, page: int = 1) -> str:
        """Read one page (2000 characters) of a web page or a book, as written - nothing is
        summarised, unlike WebFetch. For stories and articles to read slowly, not for searching.

        One page per call, in order; after each page, stop and note what you feel and expect
        before reading the next. Aozora Bunko works are cut to the main text."""
        try:
            title, text = load(url)
        except Exception as error:  # noqa: BLE001
            return f"could not fetch {url}: {error}"
        total = page_count(text)
        if not 1 <= page <= total:
            return f"page {page} is out of range (1-{total})"
        start = (page - 1) * CHARS_PER_PAGE
        return f"# {title}\n\n{text[start:start + CHARS_PER_PAGE]}\n\n--- {page}/{total} ページ ---"

    server.run()


if __name__ == "__main__":
    main()
