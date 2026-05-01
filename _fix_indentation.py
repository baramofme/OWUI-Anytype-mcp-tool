#!/usr/bin/env python3
"""Fix inconsistent indentation by normalizing all leading whitespace."""
import re

with open("anytype_openwebui_tool.py", "r") as f:
    raw = f.read()

lines = raw.split('\n')
fixed = []
for line in lines:
    # Convert tabs to 4 spaces per PEP 8
    expanded = line.expandtabs(4)
    fixed.append(expanded)

# Now rewrite with normalized spacing
output_lines = list(fixed)

with open("anytype_openwebui_tool_fixed.py", "w") as out:
    out.write('\n'.join(output_lines))

print("Whitespace normalization complete.")
