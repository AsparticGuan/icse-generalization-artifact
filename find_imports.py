import ast
import os
import sys

def get_imports(filepath):
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            tree = ast.parse(f.read(), filename=filepath)
    except Exception:
        return set()
    
    imports = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for n in node.names:
                imports.add(n.name.split('.')[0])
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                imports.add(node.module.split('.')[0])
    return imports

all_imports = set()
for root, _, files in os.walk('.'):
    if '.venv' in root or '.git' in root or 'dataset' in root or 'dataset-all' in root:
        continue
    for file in files:
        if file.endswith('.py'):
            all_imports.update(get_imports(os.path.join(root, file)))

print(sorted(list(all_imports)))
