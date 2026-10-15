"""Read-only checks for this fixed RGBA8 PNG corpus; Python standard library only.

Copyright (c) 2026 AXIALIS SA. MIT for this original script; see LICENSE and LICENSE-SCOPE.md.
Not a general-purpose PNG decoder or SVG renderer.
"""
import csv
import hashlib
import json
from pathlib import Path
import struct
import sys
import zlib

ROOT = Path(__file__).resolve().parent


def require(condition, message):
    if not condition:
        raise ValueError(message)


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def rgba_png(path):
    data = path.read_bytes()
    require(data[:8] == b'\x89PNG\r\n\x1a\n', 'PNG signature')
    pos, chunks, packed = 8, [], bytearray()
    while pos < len(data):
        require(pos + 12 <= len(data), 'Truncated chunk')
        size = struct.unpack('>I', data[pos:pos+4])[0]
        kind = data[pos+4:pos+8]
        body = data[pos+8:pos+8+size]
        require(pos + size + 12 <= len(data), 'Truncated data')
        crc = struct.unpack('>I', data[pos+8+size:pos+12+size])[0]
        require(zlib.crc32(kind + body) == crc, 'PNG CRC')
        chunks.append(kind)
        if kind == b'IHDR':
            w, h, depth, color, compression, filtering, interlace = struct.unpack('>IIBBBBB', body)
            require((depth, color, compression, filtering, interlace) == (8, 6, 0, 0, 0), 'Expected noninterlaced RGBA8')
            require(w == h and w in (16, 24, 32), 'Unexpected dimensions')
        elif kind == b'IDAT':
            packed.extend(body)
        pos += size + 12
        if kind == b'IEND':
            break
    require(chunks[0] == b'IHDR' and chunks[-1] == b'IEND' and pos == len(data), 'PNG structure')
    raw = zlib.decompress(packed)
    stride = w * 4
    require(len(raw) == (stride + 1) * h, 'Scanline size')
    previous = bytearray(stride)
    pixels = []
    for y in range(h):
        start = y * (stride + 1)
        mode = raw[start]
        require(mode in range(5), 'PNG filter')
        row = bytearray(raw[start+1:start+1+stride])
        for x in range(stride):
            a = row[x-4] if x >= 4 else 0
            b = previous[x]
            c = previous[x-4] if x >= 4 else 0
            p = a + b - c
            distances = (abs(p-a), abs(p-b), abs(p-c))
            paeth = (a, b, c)[distances.index(min(distances))]
            predictor = (0, a, b, (a+b)//2, paeth)[mode]
            row[x] = (row[x] + predictor) & 255
        pixels.extend(tuple(row[x:x+4]) for x in range(0, stride, 4))
        previous = row
    return w, h, pixels


def verify(root=ROOT):
    package = json.loads((root/'package-manifest.json').read_text(encoding='utf-8'))
    for entry in package['files']:
        target = root/entry['path']
        require(not target.is_symlink() and target.resolve().is_relative_to(root.resolve()), 'Unsafe path')
        require(sha(target) == entry['sha256'], 'Package hash: '+entry['path'])
    manifest = json.loads((root/'source-manifest.json').read_text(encoding='utf-8'))
    names = {i['name'] for i in manifest['icons']}
    require(len(names) == 12, 'Expected twelve distinct icons')
    require({p.name for p in (root/'source').iterdir()} == {n+'.svg' for n in names}, 'Source inventory')
    require(sha(root/'licenses/lucide-LICENSE.txt') == manifest['licenseSha256'], 'License hash')
    for icon in manifest['icons']:
        require(sha(root/'source'/(icon['name']+'.svg')) == icon['sha256'], 'Source hash: '+icon['name'])
    expected = {f'{n}_{s}x{s}.png' for n in names for s in (16, 24, 32)}
    require({p.name for p in (root/'png').iterdir()} == expected, 'PNG inventory')
    with (root/'evidence/m14-results.csv').open(encoding='utf-8', newline='') as f:
        rows = list(csv.DictReader(f))
    require(len(rows) == 36 and {r['file'] for r in rows} == expected, 'Result inventory')
    edge_cases, no_opaque = [], []
    for r in rows:
        path = root/'png'/r['file']
        require(not path.is_symlink() and sha(path) == r['sha256'], 'Export hash: '+path.name)
        w, h, pixels = rgba_png(path)
        require((w, h) == (int(r['width']), int(r['height'])), 'Dimensions: '+path.name)
        require(path.name == f"{Path(r['source']).stem}_{w}x{h}.png", 'Source mapping')
        alpha = [p[3] for p in pixels]
        painted = [p for p in pixels if p[3]]
        opaque = [p for p in pixels if p[3] == 255]
        require(alpha.count(0) > 0 and painted, 'Transparency/content')
        for key, value in [('transparent_pixels', alpha.count(0)), ('painted_pixels', len(painted)), ('opaque_pixels', len(opaque)), ('alpha_min', min(alpha)), ('alpha_max', max(alpha))]:
            require(value == int(r[key]), key+': '+path.name)
        error = max(max(abs(p[i]-c) for i, c in enumerate((51,65,85))) * p[3]/255 for p in painted)
        require(error <= 2 and abs(error-float(r['alpha_weighted_rgb_max_error'])) < 0.000001, 'Weighted RGB')
        require(all(max(abs(p[i]-c) for i,c in enumerate((51,65,85))) <= 1 for p in opaque), 'Opaque RGB')
        edge = max(p[3] for i,p in enumerate(pixels) if i//w in (0,h-1) or i%w in (0,w-1))
        require(edge == int(r['edge_alpha_max']), 'Edge alpha')
        if edge:
            edge_cases.append(path.name)
        if not opaque:
            no_opaque.append(path.name)
    return {'sources': 12, 'pngs': 36, 'result': 'PASS', 'edge_review': edge_cases, 'no_opaque_pixels': no_opaque}


if __name__ == '__main__':
    try:
        print(json.dumps(verify(), indent=2))
    except (ValueError, OSError, KeyError, struct.error, zlib.error) as exc:
        print('FAIL: '+str(exc), file=sys.stderr)
        sys.exit(1)
