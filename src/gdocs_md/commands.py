"""Subcommand implementations. Each cmd_* function takes (args, config) and
does its own stdout formatting -- JSON when args.json is set, otherwise
human-readable text -- and raises a GdocsMdError subclass (see errors.py)
on failure, which the CLI layer turns into an exit code."""

from __future__ import annotations

import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path

from . import auth, content, docs_api, registry
from .config import resolve_account
from .errors import AuthOrConfigError, DestructiveRefusedError, NotFoundOrPermissionError
from .smart_update import smart_update_doc

_NON_PARAGRAPH_LABELS = {
    "table": "table(s)",
    "tableOfContents": "a table of contents",
    "sectionBreak": "section break(s)",
}


def extract_text_and_warnings(content_list):
    """Extract plain text from a body/tab content list, and collect a
    loud-failure warning for any structural element `get` drops silently
    (tables, TOC, section breaks) instead of reading back. See AGENTS.md's
    known-limitations section -- content, not just formatting, is dropped
    here."""
    parts = []
    dropped = set()
    for element in content_list:
        if "paragraph" in element:
            for elem in element["paragraph"].get("elements", []):
                if "textRun" in elem:
                    parts.append(elem["textRun"]["content"])
        else:
            for key, label in _NON_PARAGRAPH_LABELS.items():
                if key in element:
                    dropped.add(label)
    warnings = [
        f"this document contains {label} that 'get' does not read back (dropped, known limitation)"
        for label in sorted(dropped)
    ]
    return "".join(parts), warnings


def _resolve_doc_id(args):
    raw = getattr(args, "url", None) or getattr(args, "doc_id", None)
    if not raw:
        raise NotFoundOrPermissionError("Provide a doc ID or --url <url>")
    url_match = re.search(r"/document/d/([a-zA-Z0-9_-]+)", raw)
    if url_match:
        return url_match.group(1)
    return raw


def _emit_json(obj, file=None):
    # Resolve sys.stdout at call time, not at import time -- a default
    # argument value is bound once, at function definition, which would
    # permanently capture whatever stdout object existed then and silently
    # stop honoring test capture (capsys) or any other runtime stdout swap.
    if file is None:
        file = sys.stdout
    json.dump(obj, file, indent=2)
    file.write("\n")


# ---------------------------------------------------------------------------
# auth
# ---------------------------------------------------------------------------


def cmd_auth_login(args, config):
    account = resolve_account(getattr(args, "account", None), config)
    email = auth.run_login_flow(config, account, headless=getattr(args, "headless", False))
    result = {"account": account, "email": email, "token_path": str(config.token_path(account))}
    if args.json:
        _emit_json(result)
    else:
        print(f"Authenticated '{account}' as {email}")
        print(f"Token saved: {result['token_path']}")
    return result


def cmd_auth_status(args, config):
    account = resolve_account(getattr(args, "account", None), config)
    status = auth.check_status(config, account)
    if args.json:
        _emit_json(status)
    else:
        if status["configured"]:
            print(f"'{account}': OK, signed in as {status['email']}")
        else:
            print(f"'{account}': NOT configured ({status['error']})")
    if not status["configured"]:
        raise AuthOrConfigError(status["error"] or "not configured")
    return status


# ---------------------------------------------------------------------------
# get
# ---------------------------------------------------------------------------


def cmd_get(args, config):
    account = resolve_account(args.account, config)
    doc_id = _resolve_doc_id(args)
    output_path = getattr(args, "output", None)

    if output_path:
        out = open(output_path, "w", encoding="utf-8")
    else:
        out = sys.stdout

    def emit(*print_args, **kwargs):
        kwargs.setdefault("file", out)
        print(*print_args, **kwargs)

    result = {"doc_id": doc_id, "warnings": []}

    try:
        try:
            docs_service = docs_api.get_docs_service(config, account)

            want_tab = getattr(args, "tab", None)
            want_all_tabs = getattr(args, "all_tabs", False)

            if want_tab or want_all_tabs:
                doc, tabs = docs_api.fetch_tabs(docs_service, doc_id)
                title = doc.get("title", "")

                if want_all_tabs:
                    all_tab_props = list(docs_api.enumerate_tabs(tabs))
                    tab_texts = {}
                    for props, _depth in all_tab_props:
                        tab_id = props.get("tabId", "?")
                        tab_obj = docs_api.find_tab_by_id(tabs, tab_id)
                        tab_body = tab_obj.get("documentTab", {}).get("body", {}) if tab_obj else {}
                        text, warns = extract_text_and_warnings(tab_body.get("content", []))
                        result["warnings"].extend(warns)
                        tab_texts[tab_id] = (props.get("title", tab_id), text)

                    if args.json:
                        result["title"] = title
                        result["tabs"] = [
                            {"tab_id": tid, "title": t, "text": txt} for tid, (t, txt) in tab_texts.items()
                        ]
                        _emit_json(result)
                    else:
                        if args.with_title and title:
                            emit(f"# {title}")
                            emit()
                        for tid, (t, txt) in tab_texts.items():
                            emit(f"# {t}")
                            emit()
                            emit(txt, end="")
                            emit()
                        for w in result["warnings"]:
                            print(f"[Warning] {w}", file=sys.stderr)
                    return result

                tab_obj = docs_api.find_tab_by_id(tabs, want_tab)
                if tab_obj is None:
                    all_ids = [p.get("tabId", "?") for p, _ in docs_api.enumerate_tabs(tabs)]
                    raise NotFoundOrPermissionError(
                        f"Tab '{want_tab}' not found. Available: {', '.join(all_ids)}"
                    )
                tab_body = tab_obj.get("documentTab", {}).get("body", {})
                text, warns = extract_text_and_warnings(tab_body.get("content", []))
                result["warnings"].extend(warns)

                if args.json:
                    result["title"] = title
                    result["tab_id"] = want_tab
                    result["text"] = text
                    _emit_json(result)
                else:
                    if args.with_title and title:
                        emit(f"# {title}")
                        emit()
                    emit(text, end="")
                    for w in result["warnings"]:
                        print(f"[Warning] {w}", file=sys.stderr)
                return result

            # Default: fetch with includeTabsContent to count tabs, warn if
            # multi-tab. With includeTabsContent=True the top-level
            # doc['body'] is EMPTY -- all content lives in
            # tabs[0].documentTab.body. Fall back to top-level body only for
            # legacy docs with no tabs at all.
            doc, tabs = docs_api.fetch_tabs(docs_service, doc_id)
            all_tab_props = list(docs_api.enumerate_tabs(tabs))
            if len(all_tab_props) > 1:
                tab_ids = [p.get("tabId", "?") for p, _ in all_tab_props]
                notice = (
                    f"doc has {len(all_tab_props)} tabs ({', '.join(tab_ids)}); "
                    f"reading tab 0. Use --tab <id> or --all-tabs to be explicit."
                )
                if not args.json:
                    print(f"[notice] {notice}", file=sys.stderr)
                result["notice"] = notice

            if tabs:
                content_list = tabs[0].get("documentTab", {}).get("body", {}).get("content", [])
            else:
                content_list = doc.get("body", {}).get("content", [])
            text, warns = extract_text_and_warnings(content_list)
            result["warnings"].extend(warns)
            insertions, deletions = content.extract_suggestions_from_doc(
                {"body": {"content": content_list}}
            )

            title = doc.get("title", "")

            if args.json:
                result["title"] = title
                result["text"] = text
                result["suggestions"] = {"insertions": insertions, "deletions": deletions}
            else:
                if args.with_title and title:
                    emit(f"# {title}")
                    emit()
                emit(text, end="")
                if insertions or deletions:
                    emit()
                    emit(content.format_suggestions(insertions, deletions), end="")

            drive_service = docs_api.get_drive_service(config, account)
            comments = content.fetch_comments(drive_service, doc_id)
            if args.json:
                result["comments"] = comments
                _emit_json(result)
            else:
                if comments:
                    emit()
                    emit(content.format_comments(comments), end="")
                for w in result["warnings"]:
                    print(f"[Warning] {w}", file=sys.stderr)

            return result

        except NotFoundOrPermissionError:
            raise
        except Exception as e:
            if "400" not in str(e) and "not supported" not in str(e).lower():
                raise NotFoundOrPermissionError(f"Failed to fetch document '{doc_id}': {e}") from e

        # Fallback: Drive export (uploaded .docx, Sheets, etc.)
        drive_service = docs_api.get_drive_service(config, account)
        file_meta = drive_service.files().get(fileId=doc_id, fields="name,mimeType").execute()
        mime_type = file_meta.get("mimeType", "")
        title = file_meta.get("name", "")

        if "google-apps" in mime_type:
            raw = drive_service.files().export(fileId=doc_id, mimeType="text/plain").execute()
            text = raw.decode("utf-8") if isinstance(raw, bytes) else raw
        else:
            from googleapiclient.http import MediaIoBaseDownload
            import io
            import tempfile
            import subprocess

            request = drive_service.files().get_media(fileId=doc_id)
            buffer = io.BytesIO()
            downloader = MediaIoBaseDownload(buffer, request)
            done = False
            while not done:
                _, done = downloader.next_chunk()

            suffix = ".docx" if "word" in mime_type.lower() or title.endswith(".docx") else ".bin"
            with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
                tmp.write(buffer.getvalue())
                tmp_path = tmp.name
            try:
                proc = subprocess.run(
                    ["pandoc", "-f", "docx", "-t", "plain", "--wrap=none", tmp_path],
                    capture_output=True, text=True, timeout=30,
                )
                text = proc.stdout if proc.returncode == 0 else buffer.getvalue().decode(
                    "utf-8", errors="replace"
                )
            finally:
                os.unlink(tmp_path)

        if args.json:
            result["title"] = title
            result["text"] = text
            _emit_json(result)
        else:
            if args.with_title and title:
                emit(f"# {title}")
                emit()
            emit(text, end="")
        return result
    finally:
        if output_path and out is not sys.stdout:
            out.close()
            print(f"Written to {output_path}", file=sys.stderr)


# ---------------------------------------------------------------------------
# create
# ---------------------------------------------------------------------------


def cmd_create(args, config):
    account = resolve_account(args.account, config)
    file_path = Path(args.file).resolve()

    if not file_path.exists():
        raise NotFoundOrPermissionError(f"File not found: {file_path}")

    reg = registry.load_registry(config.registry_file)
    if str(file_path) in reg:
        raise AuthOrConfigError(
            "Document already exists for this file. Use 'update' to modify "
            "it, or remove it from the registry first."
        )

    title = args.title if args.title else file_path.stem

    docx_path = docs_api.convert_markdown_to_docx(file_path)
    drive_service = docs_api.get_drive_service(config, account)

    try:
        file_metadata = {"name": title, "mimeType": "application/vnd.google-apps.document"}
        if args.folder:
            file_metadata["parents"] = [args.folder]

        from googleapiclient.http import MediaFileUpload

        media = MediaFileUpload(
            docx_path,
            mimetype="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        )
        file = drive_service.files().create(
            body=file_metadata, media_body=media, fields="id,webViewLink,modifiedTime"
        ).execute()

        doc_id = file["id"]
        reg[str(file_path)] = {
            "doc_id": doc_id,
            "url": file["webViewLink"],
            "title": title,
            "created": datetime.now().isoformat(),
            "updated": file["modifiedTime"],
        }
        registry.save_registry(config.registry_file, reg)

        result = {"doc_id": doc_id, "url": file["webViewLink"], "title": title}
        if args.json:
            _emit_json(result)
        else:
            print(f"Created: {result['url']}")
            print(f"Doc ID:  {result['doc_id']}")
            print(f"Title:   {result['title']}")
        return result
    finally:
        if os.path.exists(docx_path):
            os.unlink(docx_path)


# ---------------------------------------------------------------------------
# update
# ---------------------------------------------------------------------------


def _do_replace_all_update(drive_service, doc_id, file_path):
    docx_path = docs_api.convert_markdown_to_docx(file_path)
    try:
        from googleapiclient.http import MediaFileUpload

        media = MediaFileUpload(
            docx_path,
            mimetype="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        )
        return drive_service.files().update(
            fileId=doc_id, media_body=media, fields="id,webViewLink,modifiedTime"
        ).execute()
    finally:
        if os.path.exists(docx_path):
            os.unlink(docx_path)


def cmd_update(args, config):
    account = resolve_account(args.account, config)
    doc_id = args.doc_id
    dry_run = getattr(args, "dry_run", False)

    if args.tab:
        if not args.input:
            raise AuthOrConfigError("--input <file-path> is required when --tab is specified")
        file_path = Path(args.input).resolve()
        if not file_path.exists():
            raise NotFoundOrPermissionError(f"File not found: {file_path}")

        markdown_text = file_path.read_text(encoding="utf-8")

        if dry_run:
            result = {"dry_run": True, "tab_id": args.tab, "chars": len(markdown_text)}
            if args.json:
                _emit_json(result)
            else:
                print(f"[dry-run] would write to tab '{args.tab}' in document {doc_id}")
                print(f"[dry-run] {len(markdown_text)} chars from {file_path}")
            return result

        docs_service = docs_api.get_docs_service(config, account)
        from .tab_writer import write_tab_content

        write_tab_content(docs_service, doc_id, args.tab, markdown_text)
        result = {"doc_id": doc_id, "tab_id": args.tab, "url": f"https://docs.google.com/document/d/{doc_id}/edit"}
        if args.json:
            _emit_json(result)
        else:
            print(f"Updated tab '{args.tab}' in document {doc_id}")
            print(f"URL: {result['url']}")
        return result

    if not args.file:
        raise AuthOrConfigError("<file-path> is required for whole-document update (or use --tab with --input)")

    file_path = Path(args.file).resolve()
    if not file_path.exists():
        raise NotFoundOrPermissionError(f"File not found: {file_path}")

    reg = registry.load_registry(config.registry_file)
    registry_key, doc_entry = registry.find_by_doc_id(reg, doc_id)

    if getattr(args, "replace_all", False):
        docs_service = docs_api.get_docs_service(config, account)
        _, tabs = docs_api.fetch_tabs(docs_service, doc_id)
        all_tab_props = list(docs_api.enumerate_tabs(tabs))
        if len(all_tab_props) > 1 and not getattr(args, "force", False):
            tab_ids = [p.get("tabId", "?") for p, _ in all_tab_props]
            raise DestructiveRefusedError(
                f"This replaces ALL {len(all_tab_props)} tabs ({', '.join(tab_ids)}) "
                f"and destroys comments/suggestions. Pass --force to confirm."
            )

        if dry_run:
            result = {"dry_run": True, "mode": "replace_all", "tab_count": len(all_tab_props)}
            if args.json:
                _emit_json(result)
            else:
                print(f"[dry-run] would do full Drive DOCX replace on {doc_id}")
                print(f"[dry-run] doc has {result['tab_count']} tab(s) -- ALL would be replaced")
            return result

        drive_service = docs_api.get_drive_service(config, account)
        file = _do_replace_all_update(drive_service, doc_id, file_path)

        if doc_entry and registry_key:
            doc_entry["updated"] = file["modifiedTime"]
            reg[registry_key] = doc_entry
            registry.save_registry(config.registry_file, reg)

        result = {
            "mode": "replace_all",
            "doc_id": doc_id,
            "url": file["webViewLink"],
            "modified": file["modifiedTime"],
        }
        if args.json:
            _emit_json(result)
        else:
            print(f"Updated (full replace): {result['url']}")
            print(f"Doc ID:  {doc_id}")
            print(f"Modified: {result['modified']}")
        return result

    # Default: smart diff-based update
    markdown_text = file_path.read_text(encoding="utf-8")
    docs_service = docs_api.get_docs_service(config, account)

    update_result = smart_update_doc(docs_service, doc_id, markdown_text, dry_run=dry_run)

    if dry_run:
        result = {"mode": "smart_diff", "doc_id": doc_id, "dry_run": True, **update_result}
        if args.json:
            _emit_json(result)
        else:
            url = f"https://docs.google.com/document/d/{doc_id}/edit"
            print(f"[dry-run] doc: {url}")
            print(f"[dry-run] target tab: {update_result.get('tab_id') or '(body)'}")
            print(
                f"[dry-run] {update_result['changed']} paragraph(s) would change, "
                f"{update_result['unchanged']} unchanged, {update_result['ops']} API ops"
            )
        return result

    if doc_entry and registry_key:
        doc_entry["updated"] = datetime.now().isoformat()
        reg[registry_key] = doc_entry
        registry.save_registry(config.registry_file, reg)

    url = f"https://docs.google.com/document/d/{doc_id}/edit"
    result = {"mode": "smart_diff", "doc_id": doc_id, "url": url, **update_result}
    if args.json:
        _emit_json(result)
    else:
        if update_result["changed"] == 0:
            print("No changes detected. Document unchanged.")
        else:
            print(f"Updated (smart diff): {url}")
            print(f"Doc ID:  {doc_id}")
            print(
                f"Changed: {update_result['changed']} paragraph(s), "
                f"unchanged: {update_result['unchanged']}, ops: {update_result['ops']}"
            )
    return result


# ---------------------------------------------------------------------------
# tabs
# ---------------------------------------------------------------------------


def cmd_tabs(args, config):
    account = resolve_account(args.account, config)
    doc_id = args.doc_id
    docs_service = docs_api.get_docs_service(config, account)

    if getattr(args, "create", False):
        tab_properties = {"title": args.title}
        if args.index is not None:
            tab_properties["index"] = args.index
        warn = None
        if getattr(args, "parent", None):
            # parentTabId MUST be inside tabProperties (not at the request
            # body's top level) -- placing it at the top level causes a 400
            # from the Docs API.
            tab_properties["parentTabId"] = args.parent
            warn = (
                f"Creating a child tab under '{args.parent}'. Content writes to "
                "nested tabs require --tab <child-id> explicitly; the parent "
                "tab is not automatically targeted."
            )
            if not args.json:
                print(f"[Warning] {warn}", file=sys.stderr)

        request_body = {"tabProperties": tab_properties}
        # The Docs v1 API uses 'addDocumentTab' (NOT 'createDocumentTab');
        # verify live before relying on API-reference text alone --
        # 'createDocumentTab' has previously returned 400 "Cannot find field".
        response = docs_service.documents().batchUpdate(
            documentId=doc_id, body={"requests": [{"addDocumentTab": request_body}]}
        ).execute()

        replies = response.get("replies", [])
        new_tab_id = None
        if replies:
            create_reply = replies[0].get("addDocumentTab", {})
            new_tab_id = create_reply.get("tabProperties", {}).get("tabId")

        result = {
            "doc_id": doc_id,
            "tab_id": new_tab_id,
            "title": args.title,
            "url": f"https://docs.google.com/document/d/{doc_id}/edit",
            "warning": warn,
        }
        if args.json:
            _emit_json(result)
        elif new_tab_id:
            print(f"Created tab: {new_tab_id}")
            print(f"Title: {args.title}")
            print(f"URL: {result['url']}")
        else:
            print("Tab created (no tabId in response). Full response:")
            print(json.dumps(response, indent=2))
        return result

    _, tabs = docs_api.fetch_tabs(docs_service, doc_id)
    all_tab_props = list(docs_api.enumerate_tabs(tabs))

    tab_list = []
    for props, depth in all_tab_props:
        tab_id = props.get("tabId", "?")
        tab_obj = docs_api.find_tab_by_id(tabs, tab_id)
        child_count = len(tab_obj.get("childTabs", [])) if tab_obj else 0
        tab_list.append(
            {
                "tab_id": tab_id,
                "title": props.get("title", "(untitled)"),
                "index": props.get("index", "?"),
                "depth": depth,
                "child_count": child_count,
            }
        )

    if args.json:
        _emit_json({"doc_id": doc_id, "tabs": tab_list})
    elif not tab_list:
        print("No tabs found (this may be a single-tab doc with no tab metadata).")
    else:
        print(f"Tabs in {doc_id}:")
        for t in tab_list:
            indent = "  " * t["depth"]
            child_info = f" [{t['child_count']} child(ren)]" if t["child_count"] > 0 else ""
            print(f"{indent}{t['tab_id']}  \"{t['title']}\"  (index={t['index']}){child_info}")
    return {"doc_id": doc_id, "tabs": tab_list}


# ---------------------------------------------------------------------------
# sheets
# ---------------------------------------------------------------------------


def _format_rows_as_table(rows):
    if not rows:
        return "(empty)"
    col_count = max(len(row) for row in rows)
    normalized = [row + [""] * (col_count - len(row)) for row in rows]
    normalized = [[str(cell) for cell in row] for row in normalized]
    widths = [max(len(normalized[r][c]) for r in range(len(normalized))) for c in range(col_count)]

    lines = []
    header = normalized[0]
    lines.append("| " + " | ".join(cell.ljust(widths[c]) for c, cell in enumerate(header)) + " |")
    lines.append("| " + " | ".join("-" * widths[c] for c in range(col_count)) + " |")
    for row in normalized[1:]:
        lines.append("| " + " | ".join(cell.ljust(widths[c]) for c, cell in enumerate(row)) + " |")
    return "\n".join(lines)


def cmd_sheets(args, config):
    account = resolve_account(args.account, config)
    spreadsheet_id = args.spreadsheet_id
    sheets_service = docs_api.get_sheets_service(config, account)

    if getattr(args, "list_sheets", False):
        meta = sheets_service.spreadsheets().get(spreadsheetId=spreadsheet_id).execute()
        sheet_names = [s["properties"]["title"] for s in meta["sheets"]]
        if args.json:
            _emit_json({"spreadsheet_id": spreadsheet_id, "sheets": sheet_names})
        else:
            for name in sheet_names:
                print(name)
        return {"sheets": sheet_names}

    range_param = getattr(args, "range_param", None)
    sheet_name = getattr(args, "sheet", None)

    if range_param:
        target_range = range_param
    elif sheet_name:
        target_range = sheet_name
    else:
        meta = sheets_service.spreadsheets().get(spreadsheetId=spreadsheet_id).execute()
        target_range = meta["sheets"][0]["properties"]["title"]

    result = sheets_service.spreadsheets().values().get(
        spreadsheetId=spreadsheet_id, range=target_range
    ).execute()
    values = result.get("values", [])

    if args.json:
        _emit_json({"spreadsheet_id": spreadsheet_id, "range": target_range, "values": values})
    elif not values:
        print(f"(no data in range '{target_range}')")
    else:
        print(_format_rows_as_table(values))
    return {"range": target_range, "values": values}


# ---------------------------------------------------------------------------
# list
# ---------------------------------------------------------------------------


def cmd_list(args, config):
    reg = registry.load_registry(config.registry_file)
    if args.json:
        _emit_json({"registry": reg})
        return {"registry": reg}
    if not reg:
        print("Registry is empty.")
        return {"registry": reg}
    for file_path, entry in reg.items():
        print(entry["title"])
        print(f"  Doc ID:  {entry['doc_id']}")
        print(f"  URL:     {entry['url']}")
        print(f"  File:    {file_path}")
        print(f"  Created: {entry['created']}")
        print(f"  Updated: {entry['updated']}")
        print()
    return {"registry": reg}
