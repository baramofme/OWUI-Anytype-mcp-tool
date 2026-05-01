#!/usr/bin/env python3
"""Apply all remaining improvements to anytype_openwebui_tool_clean.py."""
import sys

def main():
    with open("anytype_openwebui_tool_clean.py", "r") as f:
        content = f.read()

    # ────────────────── Localization ──────────────────
    repl_count = {"before": len(content)}

    # No data found
    old = 'return "No data found."'
    new = 'return "데이터가 없습니다."'
    if old not in content: return print(f"MISMATCH at '{old[:20]}'"); sys.exit(1)
    content = content.replace(old, new); repl_count["no_data"] = 1

    # Pagination status_text (line ~428)
    old = 'status_text = f"Showing items {offset + 1} to {min(current_end, total if total else current_end)} of {total if total else \'N/A\'}"'
    new = r'''status_text = f"{offset + 1}-{min(current_end, total if total else current_end)} / {total if total else '?'}건"'''
    assert old in content, f"Not found: {old[:60]}"
    content = content.replace(old, new)

    # More results available parenthetical
    old = '(More results available)'
    new = '(더 많은 결과 있음)'
    assert old in content
    content = content.replace(old, new)

    # **Pagination:** label
    old = '"**Pagination:** "'
    new = '"**페이지:** "'
    assert old in content
    content = content.replace(old, new)

    # context summary line 503
    old = 'context_summary += " More results available via pagination."'
    new = 'context_summary += "\n⚠️ 구성되지 않은 타입이 있습니다. 컬럼 설정 패널에서 커스터마이징하세요."'
    assert old in content
    content = content.replace(old, new)

    # Error executing message line 508
    old = '''return f"Error executing \'{endpoint}\': {str(e)}"'''
    new = '''return f"\'{endpoint}\' 실행 중 오류 발생: {str(e)}"'''
    assert old in content
    content = content.replace(old, new)

    # AG Grid error HTML → Korean + add fallback table rendering
    old_error_html = '''gridDiv.innerHTML = '<p style="color:red">Error: AG Grid library not loaded.</p>';'''
    new_fallback_js = _build_fallback_table_code()
    assert old_error_html in content
    content = content.replace(old_error_html, new_fallback_js)

    # ──────────── Alert text localization ──────────────
    old_alert = """alert("Column settings saved.");"""
    new_alert = """alert("컬럼 설정이 저장되었습니다.");"""
    assert old_alert in content, f"MISMATCH alert: {[l for l in content.split(chr(10)) if 'alert(' in l][:3]}"
    content = content.replace(old_alert, new_alert)

    # ───────── Valves persistence guidance ─────────────
    vals_note = (
        "<p style=\"margin-top:8px;font-size:.7rem;color:#666;line-height:1.4\">"
        "💡 설정은 브라우저 세션(LocalStorage)에 저장됩니다.<br/>"
        "영구저장을 위해 OpenWebUI Valves → <code>type_display_config</code> 에 JSON 형식으로 입력하세요."
        "</p>"
    )
    close_panel_div = '</div>'
    idx = content.rfind(close_panel_div) - len(content)
    while True:
        nxt = content.find('</div>', idx + len(close_panel_div))
        if nxt == -1 or 'cfgPanel' not in content[idx:nxt]:
            break
        idx = nxt
    insert_pos = idx + len(close_panel_div)
    content = content[:insert_pos] + '\n' + vals_note + content[insert_pos:]

    with open("anytype_openwebui_tool.py", "w") as out:
        out.write(content)

def _build_fallback_table_code():
    return r'''#region FallbackTableRendering
          var rawDataEl = document.getElementById('__row_data');
          if (!rawDataEl) { gridDiv.innerHTML = '<p style=color:red;padding:2rem>데이터를 불러올 수 없습니다.</p>'; }
          else {
              try {
                  var data = JSON.parse(rawDataEl.textContent.trim());
                  if (!data || !Array.isArray(data) || data.length === 0) throw new Error();
                  var keys = Object.keys(data[0]);
                  var table = document.createElement('table');
                  table.style.cssText = 'width:100%;border-collapse:collapse;font-size:13px;background:#fff;';
                  var thead = document.createElement('thead');
                  var htr = document.createElement('tr');
                  keys.forEach(function(k){var th=document.createElement('th');th.textContent=k;th.style.cssText='background:#f3f4f6;border-bottom:2px solid #ddd;padding:8px;text-align:left;font-weight:600;color:#374151;white-space:nowrap;';htr.appendChild(th);});
                  thead.appendChild(htr);
                  table.appendChild(thead);
                  var tbody = document.createElement('tbody');
                  for(var i=0;i<data.length && i<500;i++){
                      var tr=document.createElement('tr');
                      keys.forEach(function(k,ci){var td=document.createElement('td');td.textContent=data[i][k]!=null?data[i][k]:"";td.style.cssText='padding:4px 8px;'+((ci%2==0)?'background:#fafafa;':'')+'white-space:nowrap;overflow:hidden;text-overflow:ellipsis;max-width:250px;';if(i%2===1&&ci%2!==0)td.style.background='#fafafa';tr.appendChild(td);}
                      tbody.appendChild(tr);
                  }}
                  table.appendChild(tbody);gridDiv.appendChild(table);
              } catch(ee) { gridDiv.innerHTML='<p style="color:red;padding:2rem">테이블 렌더링 실패</p>'; }
          }
      }'''


if __name__ == "__main__":
    main()
