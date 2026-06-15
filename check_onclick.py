with open('mkdb/server/control/files/security.html', encoding='utf-8') as f:
    content = f.read()

# Check for any lines that have obvious issues
lines = content.split('\n')

# Look for lines with onclick handlers containing JS function calls
import re
for i, line in enumerate(lines, 1):
    if 'onclick=' in line:
        print(f"Line {i}: {line.strip()[:120]}")
