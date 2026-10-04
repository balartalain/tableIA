import sys

with open('sheets_reports/widgets/base.py', 'r') as f:
    content = f.read()

try:
    compile(content, 'base.py', 'exec')
    print('Syntax OK')
except SyntaxError as e:
    print(f'Syntax error at line {e.lineno}: {e.msg}')
    lines = content.split('\n')
    for i in range(max(0, e.lineno-3), min(len(content.split('\n')), e.lineno+2)):
        print(f'{i+1:4d}: {content.split(chr(10))[i]}')
