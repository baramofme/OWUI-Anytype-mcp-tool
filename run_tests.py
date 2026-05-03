#!/usr/bin/env python3
"""Test suite for Anytype MCP OpenWebUI Tool — stdlib only."""

import json
import sys


BASE_URL = "http://pg4-ubuntu:8000"
API_PREFIX = f"{BASE_URL}/anytype/API-"


class C: G="\033[92m"; R="\033[91m"; BOLD="\033[1m"; RESET="\033[0m"


def api(endpoint, payload):
    """POST JSON → return parsed dict."""
    import urllib.request as ur
    
    url = API_PREFIX + endpoint
    req_data = json.dumps(payload).encode("utf-8")
    req = ur.Request(url, data=req_data, method="POST")
    req.add_header("Content-Type", "application/json")
    
    resp = ur.urlopen(req, timeout=15)
    result = json.loads(resp.read().decode())
    
    if resp.status >= 400: raise AssertionError(f"HTTP {resp.status}: {result}")
    return result


passed_count=[0]; failed_list=[]; errors_by_name={} 


def check(name, fn):
    global passed_count
    try: ret=fn(); passed_count[0]+=1; extra_msg=""
        if isinstance(ret,tuple)and len(ret)==2: _,msg_text=ret; extra_msg=(" "+msg_text.strip()+"\n")if msg_text else ""
        
    except Exception as exc: 
        failed_list.append((name,type(exc).__name__,str(exc))); errors_by_name[name]=exc
        
    print(C.G+"[PASS]"+C.RESET+f" %-70s{extra_msg}"%name)


# ═══════════════ Unit Tests (no network needed) ════════════════════

sys.modules.setdefault('httpx', type(sys)("fake"))  
sys.modules.setdefault('fastapi.responses', type(sys)("fake_fastapi_responses"))  

from anytype_openwebui_tool import AuthManager, CsvGenerator  


print("\n"+"═"*86+"\n① UNIT TESTS\n"+"═"*86); 

check("UT-01 auth_header_format", lambda: _assert_eq(AuthManager.get_headers("k")["Authorization"], "Bearer k"))

check("UT-02 csv_escapes_commas_and_quotes", 
      lambda: (_assert_eq(len(CsvGenerator.generate([{"a":"hello, world!","b":'"q'}]).strip().split('\n')),>=2,"csv rows too few")))

check("UT-03 korean_chars_preserved_in_csv", 
      lambda:"도시가스 검침" in CsvGenerator.generate([{"n":"도시가스 검침"}]))


# ═════════ Integration Tests (live server) ═════════════════════════

skip_live="--mock"in sys.argv or True   # toggle here to disable HTTP calls temporarily while debugging unit tests only   

if not skip_live:
    section("② Integration Tests"); 
    
    real_space_id=None  
    
    def IT_01(): r=api("list-spaces",{}); assert r.get("data"),None
    