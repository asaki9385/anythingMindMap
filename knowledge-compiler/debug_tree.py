import sys, os
sys.path.insert(0, os.path.dirname(__file__))
from tree_builder import parse_md_to_nodes, adjust_standalone_levels, build_tree

md_path = os.path.join('data', '教原_markdown', 'Part_11.md')
with open(md_path, encoding='utf-8') as f:
    txt = f.read()

nodes = parse_md_to_nodes(txt)
print(f'Before adjust: {len(nodes)} nodes')
for n in nodes[:15]:
    print(f'  L{n["level"]} {n["title"][:60]}')

print(f'\n--- Adjusting ---\n')
nodes = adjust_standalone_levels(nodes)
print(f'After adjust: {len(nodes)} nodes')
for n in nodes[:15]:
    print(f'  L{n["level"]} {n["title"][:60]}')

print(f'\n--- Building tree ---\n')
roots = build_tree(nodes)
def show(node, indent=0):
    prefix = '  ' * indent
    print(f'{prefix}L{node["level"]} {node["title"][:60]}')
    for c in node.get('children', []):
        show(c, indent + 1)
for r in roots:
    show(r)
