with open('mkdb/server/control/files/security.html', encoding='utf-8') as f:
    content = f.read()
start = content.find('<script>')
end = content.rfind('</script>')
script = content[start+8:end]
print('Script length:', len(script))

# Count braces/parens with a simple state machine
braces = 0
parens = 0
i = 0
in_str = None
while i < len(script):
    c = script[i]
    if in_str:
        if c == '\\':
            i += 2
            continue
        if c == in_str:
            in_str = None
    elif c == '"' or c == "'":
        in_str = c
    elif c == '`':
        in_str = '`'
    elif c == '{':
        braces += 1
    elif c == '}':
        braces -= 1
    elif c == '(':
        parens += 1
    elif c == ')':
        parens -= 1
    i += 1

print('Unclosed braces:', braces)
print('Unclosed parens:', parens)

# Look for any template literals with ${...} that might interfere
import re
tl = re.findall(r'`[^`]*`', script)
print('Template literals found:', len(tl))
