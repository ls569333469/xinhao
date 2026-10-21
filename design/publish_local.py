"""Publish current shared sources and saved evidence, without migrations or network.

Old snapshots are not source code. No generated runtime-check copies are emitted.
--check computes all outputs but never writes. Missing/duplicate anchors fail closed.
"""
from pathlib import Path
import argparse
import hashlib
import json
import re
import sys
from html.parser import HTMLParser

DESIGN = Path(__file__).resolve().parent
ROOT = DESIGN.parent

def text(path):
    return path.read_text(encoding='utf-8')

def encode(value):
    return json.dumps(value, ensure_ascii=False).replace('</', '<\\/')

def block(source, ident, content, json_data=False):
    pattern = r'(<script\b[^>]*\bid="' + re.escape(ident) + r'"[^>]*>).*?(</script>)'
    found = list(re.finditer(pattern, source, flags=re.S))
    if len(found) != 1:
        raise ValueError(f'Expected one script #{ident}; found {len(found)}')
    payload = encode(content) if json_data else content.replace('</script', '<\\/script')
    return re.sub(pattern, lambda m: m[1] + payload + m[2], source, flags=re.S)

def runtime(source, filename, label):
    value = text(DESIGN / filename).strip()
    start, end = '// BEGIN ' + label, '// END ' + label
    if value.count(start) != 1 or value.count(end) != 1:
        raise ValueError('Invalid runtime source: ' + filename)
    pattern = re.escape(start) + r'.*?' + re.escape(end)
    if len(re.findall(pattern, source, flags=re.S)) != 1:
        raise ValueError('Expected one runtime block: ' + label)
    return re.sub(pattern, lambda _: value, source, flags=re.S)

def fragment(source):
    """Replace only the canonical host contents; nested divs are not regex anchors."""
    class HostParser(HTMLParser):
        def __init__(self):
            super().__init__(convert_charrefs=False)
            self.offsets = [0]
            for line in source.splitlines(keepends=True):
                self.offsets.append(self.offsets[-1] + len(line))
            self.depth = 0
            self.spans = []
            self.start = None
        def pos(self):
            line, col = self.getpos()
            return self.offsets[line - 1] + col
        def handle_starttag(self, tag, attrs):
            if tag != 'div':
                return
            if dict(attrs).get('id') == 'unifiedWorkbench':
                if self.start is not None:
                    raise ValueError('Nested workbench hosts')
                self.start = self.pos() + len(self.get_starttag_text())
                self.depth = 1
            elif self.start is not None:
                self.depth += 1
        def handle_endtag(self, tag):
            if tag == 'div' and self.start is not None:
                self.depth -= 1
                if self.depth == 0:
                    self.spans.append((self.start, self.pos()))
                    self.start = None
    parser = HostParser()
    parser.feed(source)
    if len(parser.spans) != 1 or parser.start is not None:
        raise ValueError('Expected exactly one complete #unifiedWorkbench')
    start, end = parser.spans[0]
    return source[:start] + text(DESIGN / 'unified-workbench-fragment.html') + source[end:]

def build(scope='all'):
    # Validate every input/anchor before writing any output.
    outputs = {}
    page = text(DESIGN / 'prototype.html')
    page = fragment(page)
    # The default full-width table must not depend on a second script download.
    page = block(page, 'shared-pro-table-runtime', text(DESIGN / 'pro-table-runtime.js'))
    if scope == 'all':
        raw = (ROOT / 'strategy-registry.json').read_bytes().replace(b'\r\n', b'\n').replace(b'\r', b'\n')
        template = text(DESIGN / 'strategy-registry.template.js')
        if template.count("'__REGISTRY_JSON__'") != 1 or template.count('__REGISTRY_HASH__') != 1:
            raise ValueError('Registry template placeholders invalid')
        registry = template.replace("'__REGISTRY_JSON__'", json.dumps(raw.decode('utf-8'), ensure_ascii=False)).replace('__REGISTRY_HASH__', hashlib.sha256(raw).hexdigest())
        outputs[DESIGN / 'strategy-registry.js'] = registry
        page = block(page, 'snapshot-data', json.loads(text(ROOT / 'scan.json')), True)
        page = block(page, 'prompt-data', json.loads(text(DESIGN / 'prompt-templates.json')), True)
        page = runtime(page, 'evidence-runtime.js', 'COMPLETE EVIDENCE RUNTIME')
        formal = text(ROOT / 'index.html')
        formal = block(formal, 'shared-strategy-registry', registry)
        formal = block(formal, 'shared-market-evidence', text(DESIGN / 'market-evidence.js'))
        formal = block(formal, 'supplemental-evidence', {
            'reserves': json.loads(text(ROOT / 'cex-reserves.json')),
            'history': json.loads(text(ROOT / 'chain-flows.json')),
        }, True)
        outputs[ROOT / 'index.html'] = formal
    reserve = json.loads(text(ROOT / 'cex-reserves.json'))
    page = block(page, 'reserves-data', reserve, True)
    page = runtime(page, 'reserves-runtime.js', 'RESERVE RUNTIME')
    outputs[DESIGN / 'reserves-data.json'] = json.dumps(reserve, ensure_ascii=False, indent=2)
    outputs[DESIGN / 'prototype.html'] = page
    return outputs

def main(scope='all'):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check', action='store_true', help='Report stale outputs without changing files')
    args = parser.parse_args()
    outputs = build(scope)
    stale = [p for p, value in outputs.items() if not p.exists() or text(p) != value]
    print(json.dumps({'check': args.check, 'stale': [p.relative_to(ROOT).as_posix() for p in stale]}, ensure_ascii=False))
    if args.check:
        return 1 if stale else 0
    for p in stale:
        p.write_text(outputs[p], encoding='utf-8', newline='\n')
    return 0

if __name__ == '__main__':
    sys.exit(main())
