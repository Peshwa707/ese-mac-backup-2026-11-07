"""Manuscript reference manager: integrity check + Vancouver renderer.

Usage: python manuscript_refs.py <manuscript.md> <references.json>
- Verifies every in-text [key] resolves to a library entry (no broken citations).
- Flags library entries never cited (orphans).
- Numbers references by order of first appearance (Vancouver) and renders the list.
"""
import json, re, sys, pathlib


def vancouver(e, n):
    bits = [f"{n}. {e.get('authors','')}".strip().rstrip('.') + '.']
    if e.get('title'): bits.append(f"{e['title']}.")
    j = e.get('journal', '')
    yr = e.get('year', '')
    vol = e.get('volume', '')
    pg = e.get('pages', '')
    tail = j
    if yr: tail += f". {yr}"
    if vol: tail += f";{vol}"
    if pg: tail += f":{pg}"
    bits.append(tail.strip() + '.')
    ids = []
    if e.get('pmid'): ids.append(f"PMID: {e['pmid']}")
    if e.get('doi'): ids.append(f"doi:{e['doi']}")
    if ids: bits.append('(' + '; '.join(ids) + ')')
    return ' '.join(bits)


def main():
    md = pathlib.Path(sys.argv[1]).read_text()
    lib = json.loads(pathlib.Path(sys.argv[2]).read_text())
    # order of first appearance
    first = {}
    for key in lib:
        m = re.search(r'\[' + re.escape(key) + r'\]', md)
        if m:
            first[key] = m.start()
    cited = sorted(first, key=lambda k: first[k])
    uncited = [k for k in lib if k not in first]
    # broken: any [Something] that isn't a known key and looks like a citation
    bracket_tokens = set(re.findall(r'\[([A-Za-z][\w\-]+\d?)\]', md))
    known = set(lib)
    IGNORE = {'x', 'to be completed', 'pending', 'pending — not yet completed in this draft',
              'pending — requires km digitisation'}
    broken = [t for t in bracket_tokens if t not in known and t.lower() not in IGNORE
              and not t.upper().startswith('PENDING')]
    print("=== REFERENCE INTEGRITY ===")
    print(f"library entries : {len(lib)} (all verified={all(e.get('verified') for e in lib.values())})")
    print(f"cited in text   : {len(cited)}")
    print(f"uncited (orphan): {len(uncited)} {uncited or ''}")
    print(f"broken [keys]   : {len(broken)} {broken or '(none)'}")
    print()
    print("=== NUMBERED REFERENCE LIST (Vancouver, by first appearance) ===")
    for n, key in enumerate(cited, 1):
        print(vancouver(lib[key], n))
    # also emit a key->number map for converting in-text keys at submission
    return {k: i + 1 for i, k in enumerate(cited)}


if __name__ == "__main__":
    main()
