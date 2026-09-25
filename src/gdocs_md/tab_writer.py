"""Full-rewrite tab content writer (the --tab path): clears a tab and
rewrites it from markdown, including real Docs tables. Unlike the smart
diff path this destroys any comments/suggestions anchored in that tab, on
purpose -- it's the "just replace it" mode for a single tab."""

from __future__ import annotations

from . import docs_api
from .errors import NotFoundOrPermissionError
from .markdown_parser import build_block_requests, build_text_run_requests, parse_inline, parse_markdown_blocks


def find_table_in_tab(docs_service, doc_id, tab_id, table_index):
    """Find the Nth table element in the tab (0-based). Returns
    (table_element, tab_body_content) or raises NotFoundOrPermissionError."""
    doc = docs_service.documents().get(documentId=doc_id, includeTabsContent=True).execute()

    for tab in doc.get("tabs", []):
        if tab.get("tabProperties", {}).get("tabId") == tab_id:
            body = tab.get("documentTab", {}).get("body", {})
            content = body.get("content", [])
            table_count = 0
            for elem in content:
                if "table" in elem:
                    if table_count == table_index:
                        return elem["table"], content
                    table_count += 1
            raise NotFoundOrPermissionError(
                f"Table index {table_index} not found in tab '{tab_id}' "
                f"(found {table_count} tables)"
            )

    raise NotFoundOrPermissionError(f"Tab '{tab_id}' not found")


def fill_table_cells(docs_service, doc_id, tab_id, table_elem, block):
    """
    Fill an empty table (just inserted) with content from a table block
    descriptor, using the actual cell start indices from the table element.

    All insertText requests are sorted in REVERSE document order (highest
    start index first) and issued in a SINGLE batchUpdate call: within one
    batchUpdate, requests execute sequentially, and inserting at a high
    index shifts indices *above* that point but leaves lower ones alone --
    since we work downward, later requests target indices unaffected by
    earlier ones.

    Each cell in a freshly-inserted table contains exactly one empty
    paragraph.
    """
    headers = block["headers"]
    rows = block["rows"]

    all_rows_api = table_elem.get("tableRows", [])
    all_cell_texts = [headers] + list(rows)

    cells_to_fill = []
    for api_row, text_row in zip(all_rows_api, all_cell_texts):
        api_cells = api_row.get("tableCells", [])
        for api_cell, cell_text in zip(api_cells, text_row):
            if not cell_text:
                continue
            cell_content = api_cell.get("content", [])
            if not cell_content:
                continue
            para = cell_content[0]
            if "paragraph" not in para:
                continue
            cell_start = para.get("startIndex", 0)
            cells_to_fill.append((cell_start, cell_text))

    if not cells_to_fill:
        return

    cells_to_fill.sort(key=lambda x: x[0], reverse=True)

    all_requests = []
    for cell_start, cell_text in cells_to_fill:
        segments = parse_inline(cell_text)
        reqs, _ = build_text_run_requests(segments, cell_start, tab_id)
        all_requests.extend(reqs)

    if all_requests:
        docs_service.documents().batchUpdate(
            documentId=doc_id, body={"requests": all_requests}
        ).execute()


def write_tab_content(docs_service, doc_id, tab_id, markdown_text):
    """
    Replace all content in the specified tab with formatted markdown
    content.

    Strategy:
    1. Clear the tab.
    2. Parse markdown into blocks.
    3. For non-table blocks: accumulate into a batchUpdate, flush before
       each table.
    4. For table blocks: insertTable (empty), batchUpdate, then fill cells
       via a second batchUpdate using actual cell indices read from the API.

    The final newline at endIndex is the document's trailing paragraph --
    preserved by deleting only up to endIndex-1.
    """
    end_index = docs_api.get_tab_end_index(docs_service, doc_id, tab_id)
    if end_index > 2:
        docs_service.documents().batchUpdate(
            documentId=doc_id,
            body={
                "requests": [
                    {
                        "deleteContentRange": {
                            "range": {"startIndex": 1, "endIndex": end_index - 1, "tabId": tab_id}
                        }
                    }
                ]
            },
        ).execute()

    blocks = parse_markdown_blocks(markdown_text)

    pending_requests = []
    pending_idx = 1  # after clear, tab has one empty paragraph at index 1
    table_count = 0

    def flush_pending():
        nonlocal pending_requests
        if not pending_requests:
            return
        docs_service.documents().batchUpdate(
            documentId=doc_id, body={"requests": pending_requests}
        ).execute()
        pending_requests = []

    for block in blocks:
        if block["type"] == "table":
            flush_pending()

            current_end = docs_api.get_tab_end_index(docs_service, doc_id, tab_id)
            insert_at = current_end - 1

            num_rows = 1 + len(block["rows"])
            num_cols = block["cols"]

            docs_service.documents().batchUpdate(
                documentId=doc_id,
                body={
                    "requests": [
                        {
                            "insertTable": {
                                "rows": num_rows,
                                "columns": num_cols,
                                "location": {"index": insert_at, "tabId": tab_id},
                            }
                        }
                    ]
                },
            ).execute()

            table_elem, _ = find_table_in_tab(docs_service, doc_id, tab_id, table_count)
            fill_table_cells(docs_service, doc_id, tab_id, table_elem, block)
            table_count += 1

            pending_idx = docs_api.get_tab_end_index(docs_service, doc_id, tab_id) - 1

        else:
            reqs, pending_idx = build_block_requests(block, tab_id, pending_idx)
            pending_requests.extend(reqs)

    flush_pending()
