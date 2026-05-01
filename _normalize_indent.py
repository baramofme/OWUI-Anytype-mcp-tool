#!/usr/bin/env python3
"""Normalize all leading whitespace in file — same logic as Black/Ruff."""

import sys

with open('anytype_openwebui_tool.py', 'r') as f:
    lines = f.readlines()

out_lines = []
fixes = 0

for lineno, raw_line in enumerate(lines, start=1):
    # Preserve blank lines
    if not raw_line.strip():
        out_lines.append(raw_line)
        continue
    
    stripped = raw_line.lstrip()
    
    # Skip docstrings/comments/literal strings
    if stripped.startswith(('#', '"')) or stripped.startswith("'''"):
        out_lines.append(raw_line)
        continue
        
    orig_len = len(raw_line) - len(stripped)
    
    # Round to nearest multiple of 4
    target = round(orig_len / 4) * 4
    if target != orig_len:
        print(f'L{lineno}: {orig_len} -> {target}', end='\r')
        fixes += 1
        out_lines.append(' ' * target + stripped)
    else:
        out_lines.append(raw_line)

if fixes > 0:
    print(f'\nNormalized {fixes} off-by-one indents   ')
else:
    print('\nAll indents already normalized ✓\n')

# Write back only if changes were made
if fixes > 0:
    with open('anytype_openwebui_tool.py', 'w') as out:
        out.writelines(out_lines)

sys.exit(0 if not fixes else None)
