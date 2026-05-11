"""MCP Server for local knowledge base search.

Provides search_articles, get_article, and knowledge_stats tools
via JSON-RPC 2.0 over stdio protocol.
"""

import json
import sys
import os
import glob
from pathlib import Path


ARTICLES_DIR = Path(__file__).resolve().parent / "knowledge" / "articles"


def load_articles():
    """Load all article JSON files from the articles directory."""
    articles = []
    if not ARTICLES_DIR.exists():
        return articles
    for path in glob.glob(str(ARTICLES_DIR / "*.json")):
        try:
            with open(path, "r", encoding="utf-8") as f:
                articles.append(json.load(f))
        except (json.JSONDecodeError, OSError) as e:
            sys.stderr.write(f"Failed to load {path}: {e}\n")
    return articles


def search_articles(keyword, limit=5):
    """Search articles by keyword (matches title and summary)."""
    keyword_lower = keyword.lower()
    articles = load_articles()
    matched = []
    for a in articles:
        if keyword_lower in a.get("title", "").lower() or keyword_lower in a.get("summary", "").lower():
            matched.append({
                "id": a["id"],
                "title": a["title"],
                "source": a.get("source_platform", "unknown"),
                "score": a.get("score", 0),
                "tags": a.get("tags", []),
                "summary": a.get("summary", ""),
            })
    return matched[:limit]


def get_article(article_id):
    """Get full article content by ID."""
    articles = load_articles()
    for a in articles:
        if a["id"] == article_id:
            return a
    return None


def knowledge_stats():
    """Return statistics about the knowledge base."""
    articles = load_articles()
    total = len(articles)

    source_dist = {}
    tag_counter = {}
    for a in articles:
        source = a.get("source_platform", "unknown")
        source_dist[source] = source_dist.get(source, 0) + 1
        for tag in a.get("tags", []):
            tag_counter[tag] = tag_counter.get(tag, 0) + 1

    top_tags = sorted(tag_counter.items(), key=lambda x: -x[1])[:10]

    return {
        "total_articles": total,
        "source_distribution": source_dist,
        "top_tags": [{"tag": t, "count": c} for t, c in top_tags],
    }


def handle_initialize(req):
    """Handle MCP initialize request."""
    return {
        "jsonrpc": "2.0",
        "id": req.get("id"),
        "result": {
            "protocolVersion": "2024-11-05",
            "capabilities": {"tools": {}},
            "serverInfo": {
                "name": "knowledge-server",
                "version": "1.0.0",
            },
        },
    }


def handle_tools_list(req):
    """Handle MCP tools/list request."""
    return {
        "jsonrpc": "2.0",
        "id": req.get("id"),
        "result": {
            "tools": [
                {
                    "name": "search_articles",
                    "description": "Search articles by keyword in title and summary",
                    "inputSchema": {
                        "type": "object",
                        "properties": {
                            "keyword": {
                                "type": "string",
                                "description": "Keyword to search for",
                            },
                            "limit": {
                                "type": "integer",
                                "description": "Maximum number of results (default 5)",
                                "default": 5,
                            },
                        },
                        "required": ["keyword"],
                    },
                },
                {
                    "name": "get_article",
                    "description": "Get full article content by ID",
                    "inputSchema": {
                        "type": "object",
                        "properties": {
                            "article_id": {
                                "type": "string",
                                "description": "Article ID (e.g. gt-20260510-001)",
                            },
                        },
                        "required": ["article_id"],
                    },
                },
                {
                    "name": "knowledge_stats",
                    "description": "Return statistics about the knowledge base",
                    "inputSchema": {
                        "type": "object",
                        "properties": {},
                    },
                },
            ],
        },
    }


def handle_tools_call(req):
    """Handle MCP tools/call request."""
    params = req.get("params", {})
    name = params.get("name", "")
    arguments = params.get("arguments", {})

    if name == "search_articles":
        result = search_articles(
            keyword=arguments.get("keyword", ""),
            limit=arguments.get("limit", 5),
        )
    elif name == "get_article":
        result = get_article(article_id=arguments.get("article_id", ""))
    elif name == "knowledge_stats":
        result = knowledge_stats()
    else:
        return {
            "jsonrpc": "2.0",
            "id": req.get("id"),
            "error": {"code": -32601, "message": f"Unknown tool: {name}"},
        }

    return {
        "jsonrpc": "2.0",
        "id": req.get("id"),
        "result": {"content": [{"type": "text", "text": json.dumps(result, ensure_ascii=False)}]},
    }


METHOD_HANDLERS = {
    "initialize": handle_initialize,
    "tools/list": handle_tools_list,
    "tools/call": handle_tools_call,
}


def main():
    """Main loop: read JSON-RPC requests from stdin and respond on stdout."""
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
            method = req.get("method", "")
            handler = METHOD_HANDLERS.get(method)
            if handler:
                response = handler(req)
            else:
                response = {
                    "jsonrpc": "2.0",
                    "id": req.get("id"),
                    "error": {"code": -32601, "message": f"Method not found: {method}"},
                }
        except json.JSONDecodeError as e:
            response = {
                "jsonrpc": "2.0",
                "id": None,
                "error": {"code": -32700, "message": f"Parse error: {e}"},
            }

        sys.stdout.write(json.dumps(response, ensure_ascii=False) + "\n")
        sys.stdout.flush()


if __name__ == "__main__":
    main()
