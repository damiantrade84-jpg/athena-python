import ast
with open('engine_c.py') as f:
    src = f.read()
tree = ast.parse(src)
for node in ast.walk(tree):
    if isinstance(node, ast.Call):
        if isinstance(node.func, ast.Attribute) and node.func.attr == 'get':
            line = node.lineno
            obj = node.func.value
            if isinstance(obj, ast.Name):
                print(f'Line {line}: {obj.id}.get(...)')
            elif isinstance(obj, ast.Subscript):
                if isinstance(obj.value, ast.Name):
                    print(f'Line {line}: {obj.value.id}[...].get(...)')
            elif isinstance(obj, ast.Call):
                if isinstance(obj.func, ast.Name):
                    print(f'Line {line}: {obj.func.id}(...).get(...)')
