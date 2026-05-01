#!/usr/bin/env python3
"""Fix off-by-one indentation errors in anytype_openwebui_tool.py."""

with open('anytype_openwebui_tool.py', 'r') as f:
    content = f.read()

original_lines = content.split('\n')
fixed_lines = []

for i, line in enumerate(original_lines):
    if not line.strip():
        fixed_lines.append(line)
        continue
    
    orig_indent = len(line) - len(line.lstrip())
    
    # If indentation is NOT a multiple of 4, add 1 space to normalize it
    if orig_indent % 4 != 0:
        leading_spaces = line[:orig_indent] + ' '
        rest_of_line = line[orig_indent:]
        line = leading_spaces + rest_of_line
        print(f"Line {i+1}: fixed indent {orig_indent} -> {orig_indent+1}")
    
    fixed_lines.append(line)

output = '\n'.join(fixed_lines)

with open('anytype_openwebui_tool.py', 'w') as out:
    out.write(output)

print("\nDone. Running compile check...")
