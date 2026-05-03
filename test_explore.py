import json, urllib.request

BASE = "http://pg4-ubuntu:8000/anytype/API-"

# Test 1: valid get-space structure  
print("=== GET SPACE RESPONSE ===")
req1 = urllib.request.Request(BASE + 'get-space', data=json.dumps({'space_id': '_invalid_space_xyz'}).encode(), method='POST')
req1.add_header('Content-Type','application/json')
try:
    resp1 = urllib.request.urlopen(req1, timeout=5)
    print(f"Status: {resp1.status}")
    print(json.loads(resp1.read()))
except Exception as e:
    print(f"Error type: {type(e).__name__}: {e}")

