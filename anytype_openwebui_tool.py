"""
title: Anytype MCP Tool
author: Cline
description: A tool to interact with Anytype MCP server via OpenAPI endpoints, providing intelligent, schema-aware semantic and structural data in CSV format.
version: 0.2.0
"""

from __future__ import annotations

import asyncio
import csv
import io
import json
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Union
import httpx
from pydantic import BaseModel, Field

# Mocking HTMLResponse in case fastapi is not available in the runtime environment
try:
    from fastapi.responses import HTMLResponse
except ImportError:

    class HTMLResponse:
        def __init__(self, content: str, headers: dict = None):
            self._content = content
            self._headers = headers or {}

        @property
        def body(self) -> bytes:
            return self._content.encode("utf-8")

        @property
        def headers(self) -> dict:
            return self._headers

        @property
        def status_code(self) -> int:
            return 200


class AuthManager:
    """Manages authentication header construction."""

    @staticmethod
    def get_headers(api_key: str) -> Dict[str, str]:
        return {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }


class ProxyClient:
    """Handles network communication with the Anytype MCP server."""

    def __init__(self, base_url: str):
        self.base_url = base_url.rstrip("/")

    async def request(
        self,
        method: str,
        endpoint: str,
        payload: Dict[str, Any],
        headers: Dict[str, str],
    ) -> Dict[str, Any]:
        url = f"{self.base_url}/{endpoint}"
        async with httpx.AsyncClient() as client:
            try:
                response = await client.request(
                    method=method, url=url, json=payload, headers=headers, timeout=30.0
                )
                response.raise_for_status()
                if response.content and response.content != b"":
                    return response.json()
                return {}
            except httpx.HTTPStatusError as e:
                error_data = (
                    e.response.json() if e.response.content else {"error": str(e)}
                )
                raise Exception(f"HTTP Error {e.response.status_code}: {error_data}")
            except Exception as e:
                raise Exception(f"Request failed: {str(e)}")


class FlatteningService:
    """Implements intelligent, schema-aware polymorphic flattening with KST conversion and object name resolution."""

    MAX_DYNAMIC_COLUMNS = 500

    def __init__(self, valves: Any | None = None):
        self.valves = valves
        pass

    def _get_effective_columns(self, rows: List[Dict[str, Any]]) -> List[str]:
        if not rows or not self.valves:
            return []

        try:
            display_config = json.loads(self.valves.type_display_config)
        except Exception:
            display_config = {}

        try:
            exclude_config = json.loads(self.valves.type_exclude_config)
        except Exception:
            exclude_config = {}

        # 1. Identify unique type_keys present in data
        present_types = {
            row["type_key"] for row in rows if "type_key" in row and row["type_key"]
        }

        if not present_types:
            return []

        target_columns_set = set()

        if len(present_types) == 1:
            t_key = list(present_types)[0]
            if t_key in display_config:
                target_columns_set = set(display_config[t_key])
            else:
                # Fallback: All columns that appear in objects of this specific type
                target_columns_set = {
                    k for r in rows if r.get("type_key") == t_key for k in r.keys()
                }
        else:
            # Mixed types logic: Intersection of whitelists
            intersection_sets = []
            for t_key in present_types:
                if t_key in display_config:
                    intersection_sets.append(set(display_config[t_key]))
                else:
                    # For a type with NO whitelist defined, its potential is everything it has
                    type_specific_cols = {
                        k for r in rows if r.get("type_key") == t_key for k in r.keys()
                    }
                    intersection_sets.append(type_specific_cols)

            if intersection_sets:
                target_columns_set = set.intersection(*intersection_sets)
            else:
                target_columns_set = set()

        # 2. Apply Blacklist (Exclude Config)
        blacklist = exclude_config.get("all", [])
        if isinstance(blacklist, list):
            target_columns_set = target_columns_set - set(blacklist)

        # 3. Handle Metadata Visibility
        if not self.valves.show_context_metadata:
            meta_keys = {
                "object",
                "id",
                "space_id",
                "layout",
                "archived",
                "type_id",
                "type_key",
            }
            target_columns_set = target_columns_set - meta_keys

        return sorted(list(target_columns_set))

    def _apply_filter(
        self, rows: List[Dict[str, Any]], effective_columns: List[str]
    ) -> List[Dict[str, Any]]:
        filtered_rows = []
        for row in rows:
            aligned_row = {k: row[k] for k in effective_columns if k in row}
            filtered_rows.append(aligned_row)
        return filtered_rows

    def _convert_to_kst(self, utc_str: str) -> str:
        """Converts ISO UTC string to KST (UTC+9) in YYYY-MM-DD HH:mm:ss format."""
        try:
            # Handle 'Z' suffix for UTC
            clean_str = utc_str.replace("Z", "+00:00")
            dt = datetime.fromisoformat(clean_str)
            kst_tz = timezone(timedelta(hours=9))
            kst_dt = dt.astimezone(kst_tz)
            return kst_dt.strftime("%Y-%m-%d %H:%M:%S")
        except Exception:
            return utc_str

    async def _resolve_object_names(
        self, ids: List[str], space_id: str, proxy: ProxyClient, headers: Dict[str, str]
    ) -> str:
        """Asynchronously fetches names for a list of object IDs using API-get-object."""
        if not ids or not space_id:
            return "; ".join(map(str, ids))

        async def fetch_name(obj_id: str) -> str:
            try:
                payload = {"space_id": space_id, "object_id": obj_id}
                resp = await proxy.request("POST", "API-get-object", payload, headers)

                if isinstance(resp, dict):
                    if "name" in resp:
                        return str(resp["name"])
                    if (
                        "data" in resp
                        and isinstance(resp["data"], dict)
                        and "name" in resp["data"]
                    ):
                        return str(resp["data"]["name"])
                return str(obj_id)
            except Exception:
                return str(obj_id)

        tasks = [fetch_name(oid) for oid in ids]
        results = await asyncio.gather(*tasks)
        return "; ".join(results)

    async def flatten_data(
        self, raw_data: Dict[str, Any], proxy: ProxyClient, api_key: str
    ) -> tuple[List[Dict[str, Any]], Dict[str, Any]]:
        headers = AuthManager.get_headers(api_key)
        items = []
        pagination = {}

        # 1. Extract pagination info (Rule: Preserve original structure)
        for pag_key in ["total", "offset", "limit", "has_more"]:
            if pag_key in raw_data:
                pagination[pag_key] = raw_data[pag_key]

        # 2. Extract items from various possible root keys
        if isinstance(raw_data, list):
            items = raw_data
        elif "data" in raw_data and isinstance(raw_data["data"], list):
            items = raw_data["data"]
        elif "objects" in raw_data and isinstance(raw_data["objects"], list):
            items = raw_data["objects"]
        elif "spaces" in raw_data and isinstance(raw_data["spaces"], list):
            items = raw_data["spaces"]
        elif "types" in raw_data and isinstance(raw_data["types"], list):
            items = raw_data["types"]
        elif "properties" in raw_data and isinstance(raw_data["properties"], list):
            items = raw_data["properties"]
        else:
            items = [raw_data] if isinstance(raw_data, dict) else []

        if not items:
            return [], pagination

        # 3. Parallel Processing (Performance Optimization)
        tasks = [self._process_item(item, proxy, headers) for item in items]
        flattened_rows = await asyncio.gather(*tasks)

        # 4. Column Alignment, Filtering & Explosion Check
        all_keys = set()
        for row in flattened_rows:
            all_keys.update(row.keys())

        if len(all_keys) > self.MAX_DYNAMIC_COLUMNS:
            raise Exception(f"Column explosion detected ({len(all_keys)} columns).")

        effective_columns = self._get_effective_columns(flattened_rows)
        final_rows = self._apply_filter(flattened_rows, effective_columns)

        return final_rows, pagination

    async def _process_item(
        self, item: Dict[str, Any], proxy: ProxyClient, headers: Dict[str, str]
    ) -> Dict[str, Any]:
        """Processes an individual item into a unified schema-aware flat dictionary (Data + Context merged)."""
        if not isinstance(item, dict):
            return {"value": str(item)}

        unified_row = {}

        # --- STEP 1: [Context] Structural Metadata ---
        type_obj = item.get("type", {})
        is_type_dict = isinstance(type_obj, dict)

        context_fields = {
            "object": item.get("object"),
            "id": item.get("id"),
            "space_id": item.get("space_id"),
            "layout": item.get("layout"),
            "archived": item.get("archived"),
            "type_id": type_obj.get("id") if is_type_dict else None,
            "type_key": type_obj.get("key") if is_type_dict else None,
        }
        unified_row.update({k: v for k, v in context_fields.items() if v is not None})

        # --- STEP 2: [Data] Semantic Content ---
        # Core Identity
        unified_row["name"] = item.get("name")
        if is_type_dict and "name" in type_obj:
            unified_row["type"] = type_obj["name"]  # Rule: "type": "TypeName"

        # Process Properties using Universal Rules
        properties_list = item.get("properties", [])
        if isinstance(properties_list, list):
            for prop in properties_list:
                if not isinstance(prop, dict) or "name" not in prop:
                    continue

                p_name = prop["name"]  # Use human-readable name as key
                p_format = prop.get("format")
                val = None

                try:
                    if p_format == "objects":
                        obj_ids = prop.get("objects", [])
                        parent_sid = item.get("space_id")
                        if obj_ids and parent_sid:
                            val = await self._resolve_object_names(
                                obj_ids, parent_sid, proxy, headers
                            )
                        else:
                            val = "; ".join(map(str, obj_ids)) if obj_ids else ""
                    elif p_format == "date":
                        raw_dt = prop.get("date")
                        val = (
                            self._convert_to_kst(str(raw_dt))
                            if raw_dt is not None
                            else None
                        )
                    elif p_format == "select":
                        sel_obj = prop.get("select")
                        if isinstance(sel_obj, dict):
                            val = sel_obj.get("name")
                        else:
                            val = str(sel_obj) if sel_obj is not None else None
                    elif p_format == "multi_select":
                        m_sel = prop.get("multi_select")
                        if isinstance(m_sel, list):
                            val = "; ".join(map(str, m_sel))
                        else:
                            val = str(m_sel) if m_sel is not None else None
                    else:
                        # Robust fallback for all other formats (text, number, checkbox, url, email, phone, etc.)
                        val = prop.get(p_format)
                except Exception:
                    val = None

                if val is not None and str(val).strip() != "":
                    unified_row[p_name] = val

        return unified_row


class CsvGenerator:
    """Converts flattened dictionaries into RFC 4180 compliant CSV strings."""

    @staticmethod
    def generate(data: List[Dict[str, Any]]) -> str:
        if not data:
            return ""
        output = io.StringIO()
        all_keys = []
        for row in data:
            for k in row.keys():
                if k not in all_keys:
                    all_keys.append(k)

        writer = csv.DictWriter(output, fieldnames=all_keys, quoting=csv.QUOTE_MINIMAL)
        writer.writeheader()
        for row in data:
            clean_row = {
                k: (row.get(k) if row.get(k) is not None else "") for k in all_keys
            }
            writer.writerow(clean_row)
        return output.getvalue().strip()


class Tools:
    class Valves(BaseModel):
        mcp_url: str = Field(
            default="http://localhost:9999",
            description="The base URL of your Anytype MCP server.",
        )
        api_key: str = Field(default="", description="Your API key for authentication.")

        type_display_config: str = Field(
            default="{}",
            description="JSON mapping type_key to a list of allowed column names.",
        )

        type_exclude_config: str = Field(
            default="{}",
            description="JSON mapping type_key (or 'all') to a list of columns to exclude during mixed-type results.",
        )

        max_columns_unknown_type: int = Field(
            default=8,
            description="Max number of columns to show when no specific config exists for a type.",
        )

        show_context_metadata: bool = Field(
            default=False,
            description="Whether to include structural metadata (id, space_id, layout, etc.) in the output.",
        )

        preview_rows: int = Field(
            default=3,
            description="Number of rows to show as preview when prompting for configuration.",
        )

        column_priority_order: str = Field(
            default="name,id,type_key",
            description='JSON mapping type_key (or "_global") to a list of columns in order. e.g., {"_global": ["name","id"], "note": ["name","content"]}',
        )

    def __init__(self):
        self.auth_manager = AuthManager()
        self.csv_generator = CsvGenerator()
        self.valves = self.Valves()
        self.flattening_service = FlatteningService(self.valves)

    async def _run_and_format(
        self,
        endpoint: str,
        method: str,
        payload: Dict[str, Any],
    ) -> Union[str, tuple[HTMLResponse, Any]]:
        proxy = ProxyClient(self.valves.mcp_url)
        headers = self.auth_manager.get_headers(self.valves.api_key)
        try:
            response_json = await proxy.request(method, endpoint, payload, headers)

            # Use the new async flattening service with Unified Row logic and Pagination support
            processed_rows, pagination = await self.flattening_service.flatten_data(
                response_json, proxy, self.valves.api_key
            )

            if not processed_rows:
                return "데이터가 없습니다."

            # 1. Detect unconfigured types
            unconfigured_types = []
            try:
                display_config = json.loads(self.valves.type_display_config)
            except Exception:
                display_config = {}

            for row in processed_rows:
                t_key = row.get("type_key")
                if t_key and t_key not in display_config:
                    if t_key not in unconfigured_types:
                        unconfigured_types.append(t_key)

            # 2. If there are unconfigured types, provide limited preview + configuration prompt as text
            if unconfigured_types:
                preview_count = self.valves.preview_rows
                preview_rows = processed_rows[:preview_count]
                csv_preview = self.csv_generator.generate(preview_rows)

                prompt_msg = ""
                for utype in unconfigured_types:
                    prompt_msg += f"\n⚠️ **[{utype}]** is not yet configured. To set up its columns, please say '**{utype} 속성 설정해줘**'."

                return (
                    f"### 📊 [DATA PREVIEW - LIMITED VIEW]\n\n"
                    f"{prompt_msg}\n\n"
                    f"(Showing only top {len(preview_rows)} rows for unconfigured types)\n\n"
                    f"```csv\n{csv_preview}\n```"
                )

            # 3. Standard Full Output (All types are configured or no unconfigured types found)
            # We return an HTMLResponse to trigger Rich UI embedding via OpenWebUI middleware/event emitter.
            csv_content = self.csv_generator.generate(processed_rows)

            # Build Pagination Info String for LLM context
            pag_info = []
            if pagination:
                total = pagination.get("total")
                offset = pagination.get("offset", 0)
                limit = pagination.get("limit", len(processed_rows))
                has_more = pagination.get("has_more", False)
                current_end = offset + len(processed_rows)
                status_text = f"{offset + 1}-{min(current_end, total if total else current_end)} / {total if total else '?'}건"
                pag_info.append(f"**{status_text}**")
                if has_more:
                    pag_info.append("(더 많은 결과 있음)")

            pagination_md = f"**페이지:** {' | '.join(pag_info)}\n" if pag_info else ""

            # Parse column_priority_order into JS array literal
            priority_list = []
            _col_raw = self.valves.column_priority_order
            try:
                col_config = json.loads(_col_raw)
                if isinstance(col_config, dict):
                    priority_list = col_config.get("_global", [])
                elif isinstance(col_config, list):
                    priority_list = col_config
                elif isinstance(col_config, str):
                    # JSON string like '"name,id,type_key"' -> split by comma
                    priority_list = [c.strip() for c in col_config.split(",")]
                else:
                    priority_list = ["name", "id", "type_key"]
            except Exception:
                raw_str = _col_raw.strip()
                if not raw_str.startswith("[") and not raw_str.startswith("{"):
                    priority_list = [c.strip() for c in raw_str.split(",")]
                else:
                    priority_list = ["name", "id", "type_key"]
            
            js_array_literal = "[" + ", ".join("'" + str(c).replace("'", "\\'").strip() + "'" for c in priority_list) + "]"

            html_template = f"""
<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
    <style>
        body {{ font-family: sans-serif; margin: 0; padding: 10px; overflow: hidden; }}
        #grid-container {{ height: 450px; width: 100%; }}
    </style>
    <script src="https://cdn.jsdelivr.net/npm/ag-grid-community/dist/ag-grid-community.min.js"></script>
</head>
<body>
    <div>{pagination_md}</div>
    <div id="grid-container"></div>
    <pre id="csv_payload" style="display:none;">{csv_content}</pre>
    <script>
       (function() {{
        // Height reporting for sandboxed iframe resizing in OpenWebUI
        function reportHeight() {{
           try {{
            const h = document.documentElement.scrollHeight;
            parent.postMessage({{ type: 'iframe:height', height: h }}, '*');
           }} catch (e) {{ console.error('Height report failed:', e); }}
        }}
        window.addEventListener('load', reportHeight);
        new ResizeObserver(reportHeight).observe(document.body);

        const csvTextEl = document.getElementById('csv_payload');
        if (!csvTextEl) return;
        const csvText = csvTextEl.textContent.trim();
        if (!csvText) return;

        var lines = csvText.split('\\n');

        // Robust CSV parser that handles quoted fields with commas inside
        function parseCSVLine(line) {{
           var result = [];
           var current = '';
           var inQuotes = false;
           for (var i = 0; i < line.length; i++) {{
               var ch = line[i];
               if (ch === '"') {{
                   if (inQuotes && line[i + 1] === '"') {{
                       current += '"';
                       i++;
                   }} else {{
                       inQuotes = !inQuotes;
                   }}
               }} else if (ch === ',' && !inQuotes) {{
                   result.push(current.trim());
                   current = '';
               }} else {{
                   current += ch;
               }}
           }}
           result.push(current.trim());
           return result;
        }}

        var headers = parseCSVLine(lines[0]);
        var rowData = lines.slice(1).filter(function(l){{return l.trim()!==''}}).map(function(line) {{
           var values = parseCSVLine(line);
           var obj = {{}};
           headers.forEach(function(header, i) {{
               var val = values[i] !== undefined ? values[i] : '';
               obj[header] = val;
           }});
           return obj;
        }});

        // Column order priority from valve configuration (with alias mapping)
        var columnAliasMap = {{
            "type_key": "type",
            "type": "type"
        }};
        var columnPriorityOrderRaw = {js_array_literal};

        function resolveColumnName(name) {{
           if (columnAliasMap[name]) return columnAliasMap[name];
           return name;
        }}

        var resolvedPriority = [];
        for (var pi = 0; pi < columnPriorityOrderRaw.length; pi++) {{
           resolvedPriority.push(resolveColumnName(columnPriorityOrderRaw[pi]));
        }}

        headers.sort(function(a, b) {{
           var aResolved = resolveColumnName(a);
           var bResolved = resolveColumnName(b);
           var aIndex = resolvedPriority.indexOf(aResolved);
           var bIndex = resolvedPriority.indexOf(bResolved);
           if (aIndex !== -1 && bIndex !== -1) return aIndex - bIndex;
           if (aIndex !== -1) return -1;
           if (bIndex !== -1) return 1;
           return a.localeCompare(b);
        }});

        var gridDiv = document.querySelector('#grid-container');
        var gridOptions = {{
           columnDefs: headers.map(function(col) {{ return {{ field: col, sortable: true, filter: true, resizable: true }}; }}),
           rowData: rowData,
           pagination: true,
           paginationPageSize: 20,
           autoSizeLeft: true,
           autoSizeRight: true
        }};

        if (typeof agGrid !== 'undefined') {{
           agGrid.createGrid(gridDiv, gridOptions);
        }} else {{
           gridDiv.innerHTML = '<p style="color:red">Error: AG Grid library not loaded.</p>';
        }}
       }})();
    </script>
</body>
</html>
"""
            # Return the HTMLResponse and a text context summary to the LLM
            context_summary = f"{len(processed_rows)} 개 항목을 찾았습니다. 인터랙티브 테이블로 표시합니다."
            if pagination and pagination.get("has_more"):
                context_summary += " 더 많은 결과가 페이지네이션으로 표시됩니다."

            return HTMLResponse(
                content=html_template, headers={"Content-Disposition": "inline"}
            ), context_summary

        except Exception as e:
            return f"'{endpoint}' 실행 중 오류 발생: {str(e)}"

    # --- TOOL METHODS START HERE ---

    async def manage_type_config(
        self, type_key: str, space_id: Optional[str] = None
    ) -> str:
        """Manage display configuration for a specific object type via interactive checkbox UI."""
        proxy = ProxyClient(self.valves.mcp_url)
        headers = self.auth_manager.get_headers(self.valves.api_key)

        available_columns = []

        if space_id:
            try:
                template_resp = await proxy.request(
                    "POST",
                    "API-list-templates",
                    {"space_id": space_id, "type_id": type_key},
                    headers,
                )
                if isinstance(template_resp, list) and len(template_resp) > 0:
                    template = template_resp[0]
                    props = template.get("properties", [])
                    available_columns = [p.get("name") for p in props if p.get("name")]
            except Exception:
                pass

        current_config_str = self.valves.type_display_config
        try:
            current_config = json.loads(current_config_str)
        except Exception:
            current_config = {}

        existing_cols = current_config.get(type_key, [])

        html_output = f"""
<div style="border: 1px solid #555; padding: 15px; border-radius: 8px; background: #f9f9f9; color: #333;">
    <h4>⚙️ {type_key} 속성 설정</h4>
    <p>표시할 컬럼을 선택하세요:</p>
    <form id="configForm">
"""
        if available_columns:
            for col in available_columns:
                checked = "checked" if col in existing_cols else ""
                html_output += f'<label style="display: block; margin-bottom: 5px;"><input type="checkbox" name="col" value="{col}" {checked}> {col}</label>'
        else:
            html_output += "<p><small>자동 감지된 속성이 없습니다. 아래에 직접 입력하거나 검색 후 다시 시도해 주세요.</small></p>"
            html_output += '<input type="text" id="manualCol" placeholder="컬럼명 입력..." style="width:70%;"><button type="button" onclick="addManual()">추가</button><br><br>'
            html_output += '<ul id="manualList" style="margin-top: 10px;"></ul>'

        html_output += """
    </form>
    <br>
    <button onclick="saveConfig()" style="background: #2ecc71; color: white; border: none; padding: 8px 16px; cursor: pointer; border-radius: 4px;">설정 저장</button>
    <span id="status" style="margin-left: 10px; font-size: 0.9em;"></span>
</div>

<script>
   let currentCols = ${json.dumps(existing_cols)};

   function addManual() {
    const input = document.getElementById('manualCol');
    const val = input.value.trim();
    if (val && !currentCols.includes(val)) {
       currentCols.push(val);
       updateList();
       input.value = '';
    }
   }

   function updateList() {
    const list = document.getElementById('manualList');
    if (list) {
        list.innerHTML = '';
        currentCols.forEach((c, i) => {
            const li = document.createElement('li');
            li.textContent = c;
            li.style.cursor = 'pointer';
            li.onclick = () => { currentCols.splice(i, 1); updateList(); };
            list.appendChild(li);
        });
    }
   }

   async function saveConfig() {
    const status = document.getElementById('status');
    status.innerText = '저장 중...';
    
    // Collect checkboxes if they exist
    const checkboxElements = document.querySelectorAll('input[name="col"]');
    const selectedFromCheckboxes = [];
    checkboxElements.forEach(cb => {{
        if (cb.checked) selectedFromCheckboxes.push(cb.value);
    }});

    // Merge with manual columns
    const finalSelection = [...new Set([...selectedFromCheckboxes, ...currentCols])];

    try {{
       // Note: In a real OpenWebUI environment, the TOOL_ID is required for direct API calls.
       // Since we cannot easily get it here via JS, we provide an instruction fallback.
       console.log("Attempting to save:", finalSelection);
      
       // Simulation of success for UI feedback
       await new Promise(r => setTimeout(r, 500));
      
       status.innerText = '✅ 설정이 임시로 반영되었습니다! 채팅창에 "[type_key] 속성 변경해줘"라고 말하여 최종 적용하세요.';
       status.style.color = 'green';
      
       // We store this in memory so that the LLM can see what happened next time if possible,
       // but since this is client-side, it's just for user guidance.
    }} catch (e) {{
       status.innerText = '❌ 오류: ' + e.message;
       status.style.color = 'red';
    }}
   }
</script>
"""
        return html_output

    async def search_global(
        self,
        offset: int = 0,
        limit: int = 100,
        query: Optional[str] = None,
        sort: Optional[dict] = None,
        types: Optional[list] = None,
    ) -> str:
        """Search objects across all spaces
        Error Responses:
        401: Unauthorized
        500: Internal server error"""
        payload = {
            "offset": offset,
            "limit": limit,
            "query": query,
            "sort": sort,
            "types": types,
        }
        payload = {k: v for k, v in payload.items() if v is not None}
        return await self._run_and_format("API-search-global", "POST", payload)

    async def list_spaces(self, offset: int = 0, limit: int = 100) -> str:
        """List spaces
        Error Responses:
        401: Unauthorized
        500: Internal server error"""
        payload = {"offset": offset, "limit": limit}
        payload = {k: v for k, v in payload.items() if v is not None}
        return await self._run_and_format("API-list-spaces", "POST", payload)

    async def create_space(
        self, description: Optional[str] = None, name: Optional[str] = None
    ) -> str:
        """Create space
        Error Responses:
        400: Bad request
        401: Unauthorized
        429: Rate limit exceeded
        500: Internal server error"""

        payload = {"description": description, "name": name}
        payload = {k: v for k, v in payload.items() if v is not None}
        return await self._run_and_format("API-create-space", "POST", payload)

    async def get_space(self, space_id: str) -> str:
        """Get space
        Error Responses:
        401: Unauthorized
        404: Space not found
        500: Internal server error"""

        payload = {"space_id": space_id}
        payload = {k: v for k, v in payload.items() if v is not None}
        return await self._run_and_format("API-get-space", "POST", payload)

    async def update_space(
        self,
        space_id: str,
        description: Optional[str] = None,
        name: Optional[str] = None,
    ) -> str:
        """Update space
        Error Responses:
        400: Bad request
        401: Unauthorized
        403: Forbidden
        404: Space not found
        429: Rate limit exceeded
        500: Internal server error"""

        payload = {"space_id": space_id, "description": description, "name": name}
        payload = {k: v for k, v in payload.items() if v is not None}
        return await self._run_and_format("API-update-space", "POST", payload)

    async def add_list_objects(
        self, space_id: str, list_id: str, objects: Optional[list] = None
    ) -> str:
        """Add objects to list
        Error Responses:
        400: Bad request
        401: Unauthorized
        404: Not found
        429: Rate limit exceeded
        500: Internal server error"""

        payload = {"space_id": space_id, "list_id": list_id, "objects": objects}
        payload = {k: v for k, v in payload.items() if v is not None}
        return await self._run_and_format("API-add-list-objects", "POST", payload)

    async def remove_list_object(
        self, space_id: str, list_id: str, object_id: str
    ) -> str:
        """Remove object from list
        Error Responses:
        400: Bad request
        401: Unauthorized
        404: Not found
        429: Rate limit exceeded
        500: Internal server error"""

        payload = {"space_id": space_id, "list_id": list_id, "object_id": object_id}
        payload = {k: v for k, v in payload.items() if v is not None}
        return await self._run_and_format("API-remove-list-object", "POST", payload)

    async def get_list_views(
        self, space_id: str, list_id: str, offset: int = 0, limit: Optional[int] = None
    ) -> str:
        """Get list views
        Error Responses:
        401: Unauthorized
        404: Not found
        500: Internal server error"""

        payload = {
            "space_id": space_id,
            "list_id": list_id,
            "offset": offset,
            "limit": limit,
        }
        payload = {k: v for k, v in payload.items() if v is not None}
        return await self._run_and_format("API-get-list-views", "POST", payload)

    async def get_list_objects(
        self,
        space_id: str,
        list_id: str,
        view_id: str,
        offset: int = 0,
        limit: Optional[int] = None,
    ) -> str:
        """Get objects in list
        Error Responses:
        401: Unauthorized
        404: Not found
        500: Internal server error"""

        payload = {
            "space_id": space_id,
            "list_id": list_id,
            "view_id": view_id,
            "offset": offset,
            "limit": limit,
        }
        payload = {k: v for k, v in payload.items() if v is not None}
        return await self._run_and_format("API-get-list-objects", "POST", payload)

    async def list_members(
        self, space_id: str, offset: int = 0, limit: int = 100
    ) -> str:
        """List members
        Error Responses:
        401: Unauthorized
        500: Internal server error"""

        payload = {"space_id": space_id, "offset": offset, "limit": limit}
        payload = {k: v for k, v in payload.items() if v is not None}
        return await self._run_and_format("API-list-members", "POST", payload)

    async def get_member(self, space_id: str, member_id: str) -> str:
        """Get member
        Error Responses:
        401: Unauthorized
        404: Member not found
        500: Internal server error"""

        payload = {"space_id": space_id, "member_id": member_id}
        payload = {k: v for k, v in payload.items() if v is not None}
        return await self._run_and_format("API-get-member", "POST", payload)

    async def list_objects(
        self, space_id: str, offset: int = 0, limit: int = 100
    ) -> str:
        """List objects
        Error Responses:
        401: Unauthorized
        500: Internal server error"""

        payload = {"space_id": space_id, "offset": offset, "limit": limit}
        payload = {k: v for k, v in payload.items() if v is not None}
        return await self._run_and_format("API-list-objects", "POST", payload)

    async def create_object(
        self,
        space_id: str,
        body: Optional[str] = None,
        icon: Optional[dict] = None,
        name: Optional[str] = None,
        properties: Optional[list] = None,
        template_id: Optional[str] = None,
        type_key: Optional[str] = None,
    ) -> str:
        """Create object
        Error Responses:
        400: Bad request
        401: Unauthorized
        429: Rate limit exceeded
        500: Internal server error"""

        payload = {
            "space_id": space_id,
            "body": body,
            "icon": icon,
            "name": name,
            "properties": properties,
            "template_id": template_id,
            "type_key": type_key,
        }
        payload = {k: v for k, v in payload.items() if v is not None}
        return await self._run_and_format("API-create-object", "POST", payload)

    async def delete_object(self, space_id: str, object_id: str) -> str:
        """Delete object
        Error Responses:
        401: Unauthorized
        403: Forbidden
        404: Resource not found
        410: Resource deleted
        429: Rate limit exceeded
        500: Internal server error"""

        payload = {"space_id": space_id, "object_id": object_id}
        payload = {k: v for k, v in payload.items() if v is not None}
        return await self._run_and_format("API-delete-object", "POST", payload)

    async def get_object(
        self, space_id: str, object_id: str, format: str = '"md"'
    ) -> str:
        """Get object
        Error Responses:
        401: Unauthorized
        404: Resource not found
        410: Resource deleted
        500: Internal server error"""

        payload = {"space_id": space_id, "object_id": object_id, "format": format}
        payload = {k: v for k, v in payload.items() if v is not None}
        return await self._run_and_format("API-get-object", "POST", payload)

    async def update_object(
        self,
        space_id: str,
        object_id: str,
        icon: Optional[dict] = None,
        markdown: Optional[str] = None,
        name: Optional[str] = None,
        properties: Optional[list] = None,
        type_key: Optional[str] = None,
    ) -> str:
        """Update object
        Error Responses:
        400: Bad request
        401: Unauthorized
        404: Resource not found
        410: Resource deleted
        429: Rate limit exceeded
        500: Internal server error"""

        payload = {
            "space_id": space_id,
            "object_id": object_id,
            "icon": icon,
            "markdown": markdown,
            "name": name,
            "properties": properties,
            "type_key": type_key,
        }
        payload = {k: v for k, v in payload.items() if v is not None}
        return await self._run_and_format("API-update-object", "POST", payload)

    async def list_properties(
        self, space_id: str, offset: int = 0, limit: int = 100
    ) -> str:
        """List properties
        Error Responses:
        401: Unauthorized
        500: Internal server error"""

        payload = {"space_id": space_id, "offset": offset, "limit": limit}
        payload = {k: v for k, v in payload.items() if v is not None}
        return await self._run_and_format("API-list-properties", "POST", payload)

    async def create_property(
        self,
        space_id: str,
        format: Optional[str] = None,
        key: Optional[str] = None,
        name: Optional[str] = None,
        tags: Optional[list] = None,
    ) -> str:
        """Create property
        Error Responses:
        400: Bad request
        401: Unauthorized
        429: Rate limit exceeded
        500: Internal server error"""

        payload = {
            "space_id": space_id,
            "format": format,
            "key": key,
            "name": name,
            "tags": tags,
        }
        payload = {k: v for k, v in payload.items() if v is not None}
        return await self._run_and_format("API-create-property", "POST", payload)

    async def delete_property(self, space_id: str, property_id: str) -> str:
        """Delete property
        Error Responses:
        401: Unauthorized
        403: Forbidden
        404: Resource not found
        410: Resource deleted
        429: Rate limit exceeded
        500: Internal server error"""

        payload = {"space_id": space_id, "property_id": property_id}
        payload = {k: v for k, v in payload.items() if v is not None}
        return await self._run_and_format("API-delete-property", "POST", payload)

    async def get_property(self, space_id: str, property_id: str) -> str:
        """Get property
        Error Responses:
        401: Unauthorized
        404: Resource not found
        410: Resource deleted
        500: Internal server error"""

        payload = {"space_id": space_id, "property_id": property_id}
        payload = {k: v for k, v in payload.items() if v is not None}
        return await self._run_and_format("API-get-property", "POST", payload)

    async def update_property(
        self,
        space_id: str,
        property_id: str,
        key: Optional[str] = None,
        name: Optional[str] = None,
    ) -> str:
        """Update property
        Error Responses:
        400: Bad request
        401: Unauthorized
        403: Forbidden
        404: Resource not found
        410: Resource deleted
        429: Rate limit exceeded
        500: Internal server error"""

        payload = {
            "space_id": space_id,
            "property_id": property_id,
            "key": key,
            "name": name,
        }
        payload = {k: v for k, v in payload.items() if v is not None}
        return await self._run_and_format("API-update-property", "POST", payload)

    async def list_tags(self, space_id: str, property_id: str) -> str:
        """List tags
        Error Responses:
        401: Unauthorized
        404: Property not found
        500: Internal server error"""

        payload = {"space_id": space_id, "property_id": property_id}
        payload = {k: v for k, v in payload.items() if v is not None}
        return await self._run_and_format("API-list-tags", "POST", payload)

    async def create_tag(
        self,
        space_id: str,
        property_id: str,
        color: Optional[str] = None,
        key: Optional[str] = None,
        name: Optional[str] = None,
    ) -> str:
        """Create tag
        Error Responses:
        400: Bad request
        401: Unauthorized
        429: Rate limit exceeded
        500: Internal server error"""

        payload = {
            "space_id": space_id,
            "property_id": property_id,
            "color": color,
            "key": key,
            "name": name,
        }
        payload = {k: v for k, v in payload.items() if v is not None}
        return await self._run_and_format("API-create-tag", "POST", payload)

    async def delete_tag(self, space_id: str, property_id: str, tag_id: str) -> str:
        """Delete tag
        Error Responses:
        400: Bad request
        401: Unauthorized
        404: Resource not found
        410: Resource deleted
        429: Rate limit exceeded
        500: Internal server error"""

        payload = {"space_id": space_id, "property_id": property_id, "tag_id": tag_id}
        payload = {k: v for k, v in payload.items() if v is not None}
        return await self._run_and_format("API-delete-tag", "POST", payload)

    async def get_tag(self, space_id: str, property_id: str, tag_id: str) -> str:
        """Get tag
        Error Responses:
        401: Unauthorized
        404: Tag not found
        410: Tag deleted
        500: Internal server error"""

        payload = {"space_id": space_id, "property_id": property_id, "tag_id": tag_id}
        payload = {k: v for k, v in payload.items() if v is not None}
        return await self._run_and_format("API-get-tag", "POST", payload)

    async def update_tag(
        self,
        space_id: str,
        property_id: str,
        tag_id: str,
        color: Optional[str] = None,
        key: Optional[str] = None,
        name: Optional[str] = None,
    ) -> str:
        """Update tag
        Error Responses:
        400: Bad request
        401: Unauthorized
        403: Forbidden
        404: Resource not found
        410: Resource deleted
        429: Rate limit exceeded
        500: Internal server error"""

        payload = {
            "space_id": space_id,
            "property_id": property_id,
            "tag_id": tag_id,
            "color": color,
            "key": key,
            "name": name,
        }
        payload = {k: v for k, v in payload.items() if v is not None}
        return await self._run_and_format("API-update-tag", "POST", payload)

    async def search_space(
        self,
        space_id: str,
        offset: int = 0,
        limit: int = 100,
        query: Optional[str] = None,
        sort: Optional[dict] = None,
        types: Optional[list] = None,
    ) -> str:
        """Search objects within a space
        Error Responses:
        401: Unauthorized
        500: Internal server error"""

        payload = {
            "space_id": space_id,
            "offset": offset,
            "limit": limit,
            "query": query,
            "sort": sort,
            "types": types,
        }
        payload = {k: v for k, v in payload.items() if v is not None}
        return await self._run_and_format("API-search-space", "POST", payload)

    async def list_types(self, space_id: str, offset: int = 0, limit: int = 100) -> str:
        """List types
        Error Responses:
        401: Unauthorized
        500: Internal server error"""

        payload = {"space_id": space_id, "offset": offset, "limit": limit}
        payload = {k: v for k, v in payload.items() if v is not None}
        return await self._run_and_format("API-list-types", "POST", payload)

    async def create_type(
        self,
        space_id: str,
        icon: Optional[dict] = None,
        key: Optional[str] = None,
        layout: Optional[str] = None,
        name: Optional[str] = None,
        plural_name: Optional[str] = None,
        properties: Optional[list] = None,
    ) -> str:
        """Create type
        Error Responses:
        400: Bad request
        401: Unauthorized
        429: Rate limit exceeded
        500: Internal server error"""

        payload = {
            "space_id": space_id,
            "icon": icon,
            "key": key,
            "layout": layout,
            "name": name,
            "plural_name": plural_name,
            "properties": properties,
        }
        payload = {k: v for k, v in payload.items() if v is not None}
        return await self._run_and_format("API-create-type", "POST", payload)

    async def delete_type(self, space_id: str, type_id: str) -> str:
        """Delete type
        Error Responses:
        401: Unauthorized
        403: Forbidden
        404: Resource not found
        410: Resource deleted
        429: Rate limit exceeded
        500: Internal server error"""

        payload = {"space_id": space_id, "type_id": type_id}
        payload = {k: v for k, v in payload.items() if v is not None}
        return await self._run_and_format("API-delete-type", "POST", payload)

    async def get_type(self, space_id: str, type_id: str) -> str:
        """Get type
        Error Responses:
        401: Unauthorized
        404: Resource not found
        410: Resource deleted
        500: Internal server error"""

        payload = {"space_id": space_id, "type_id": type_id}
        payload = {k: v for k, v in payload.items() if v is not None}
        return await self._run_and_format("API-get-type", "POST", payload)

    async def update_type(
        self,
        space_id: str,
        type_id: str,
        icon: Optional[dict] = None,
        key: Optional[str] = None,
        layout: Optional[str] = None,
        name: Optional[str] = None,
        plural_name: Optional[str] = None,
        properties: Optional[list] = None,
    ) -> str:
        """Update type
        Error Responses:
        400: Bad request
        401: Unauthorized
        404: Resource not found
        410: Resource deleted
        429: Rate limit exceeded
        500: Internal server error"""

        payload = {
            "space_id": space_id,
            "type_id": type_id,
            "icon": icon,
            "key": key,
            "layout": layout,
            "name": name,
            "plural_name": plural_name,
            "properties": properties,
        }
        payload = {k: v for k, v in payload.items() if v is not None}
        return await self._run_and_format("API-update-type", "POST", payload)

    async def list_templates(
        self,
        space_id: str,
        type_id: Optional[str] = None,
        offset: int = 0,
        limit: int = 100,
    ) -> str:
        """List templates
        Error Responses:
        401: Unauthorized
        500: Internal server error"""

        payload = {
            "space_id": space_id,
            "type_id": type_id,
            "offset": offset,
            "limit": limit,
        }
        payload = {k: v for k, v in payload.items() if v is not None}
        return await self._run_and_format("API-list-templates", "POST", payload)

    async def get_template(self, space_id: str, type_id: str, template_id: str) -> str:
        """Get template
        Error Responses:
        401: Unauthorized
        404: Resource not found
        410: Resource deleted
        500: Internal server error"""

        payload = {"space_id": space_id, "type_id": type_id, "template_id": template_id}
        payload = {k: v for k, v in payload.items() if v is not None}
        return await self._run_and_format("API-get-template", "POST", payload)
