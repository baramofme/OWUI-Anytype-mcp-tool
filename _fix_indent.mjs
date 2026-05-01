// Fix indentation like Black/Ruff format (normalize to 4-space multiples)
const fs = require('fs');

const content = fs.readFileSync('/home/baramofme/IdeaProjects/OWUI-Anytype-mcp-tool/anytype_openwebui_tool.py', 'utf8');
let fixedCount = 0;

const lines = content.split('\n').map((line, idx) => {
    if (!line || !/[^\s]/.test(line)) return line;
    
    const origLen = len(line);
    let targetLen = origLen;
    
    // Normalize: round down to nearest valid multiple of 4 (or up by +1 if off-by-one)
    if (origLen % 4 !== 0 && (origLen - 1) % 4 === 0) {
        targetLen--; // e.g., 12→8, 11→8, 23→20
    } else if ((origLen + 1) % 4 === 0) {
        targetLen++; // e.g., 7→8, 11→12, 19→20
    }
    
    if (targetLen !== origLen) {
        console.log(`L${idx + 1}: ${origLen} → ${targetLen}`);
        fixedCount++;
        return ' '.repeat(targetLen) + line.slice(origLen);
    }
    
    return line;
});

console.log(`\nFixed ${fixedCount} lines\n`);
process.stdout.write(lines.join('\n'));
