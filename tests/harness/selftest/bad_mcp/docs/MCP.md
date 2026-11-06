# bad-mcp – MCP-Schnittstelle

bad-mcp ist ein absichtlich fehlerhafter Artikeldienst für den Gate-Selbsttest.

```mcp-meta
{"command": "bad-mcp", "cli": "bad", "base_url_env": "BAD_BASE_URL", "secret_env": ["BAD_TOKEN"], "env": {}}
```

## Routen

```mcp-routes
[{"method": "GET", "path": "/items", "query_contains": "q=", "response": "tests/fixtures/mcp/items.json", "status": 200, "content_type": "application/json; charset=utf-8"},
 {"method": "GET", "path": "/item", "query_contains": "id=", "response": "tests/fixtures/mcp/item.json", "status": 200, "content_type": "application/json; charset=utf-8"},
 {"method": "POST", "path": "/book", "response": "tests/fixtures/mcp/book.json", "status": 200, "content_type": "application/json; charset=utf-8"}]
```

## Aufrufe

```mcp-calls
[{"tool": "search_items", "arguments": {"query": "Linsen"}, "expect": ["Linsen"]},
 {"tool": "get_item", "arguments": {"id": "1"}, "expect": ["Linsen"]},
 {"tool": "book_item", "arguments": {"item": "1", "confirm": false}, "expect_no_write": true}]
```
