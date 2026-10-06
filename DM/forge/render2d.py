"""Map Forge's 2D painter: procedural textures, walls along the exact Foundry wall lines, props, shadows and glows.

The map is painted in tiles, in parallel, so size is limited by patience rather than memory: an 80x80-square
map at 150 px a square (12000 x 12000 px) paints in a few minutes. Every texture is a function of map
coordinates (hashed lattice noise), so tiles join without seams and re-forging a plan gives the same picture.
Pure Pillow + numpy."""

import math
import os
from concurrent.futures import ProcessPoolExecutor

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

from scipy import ndimage

from forge import FLOORS, PROPS, WALLS, is_open, wall_nodes

Image.MAX_IMAGE_PIXELS = None  # big maps are the point
SS = 2  # vector layers are drawn at 2x and scaled down, for smooth edges
TILE = 1536
WEBP_MAX = 16383

THEMES = {
    #           floor texture, floor colour,   wall style, wall colour,     void texture, void colour
    'dungeon': ('flagstone', (116, 110, 100), 'stone', (84, 82, 80), 'rock', (30, 29, 28)),
    'cellar': ('flagstone', (122, 104, 84), 'stone', (92, 82, 70), 'rock', (32, 28, 25)),
    'temple': ('marble', (206, 200, 190), 'stone', (150, 144, 134), 'rock', (34, 32, 30)),
    'tavern': ('planks', (132, 92, 58), 'timber', (70, 50, 36), 'dirt', (104, 84, 60)),
    'ship': ('planks', (150, 110, 70), 'timber', (78, 54, 36), 'sky', (160, 198, 232)),
    'cave': ('cave', (98, 90, 80), 'cavewall', (60, 56, 52), 'rock', (40, 38, 36)),
    # outdoors, space is the open ground and '.' is the floor inside buildings
    'outdoor': ('planks', (132, 92, 58), 'stone', (110, 106, 98), 'grass', (84, 116, 56)),
    # towns: space is the cobbled street, '#' plastered house walls, '$' stone walls
    'city': ('planks', (132, 92, 58), 'house', (204, 194, 172), 'cobble', (124, 118, 110)),
}
GROUND = {
    'grass': (84, 116, 56),
    'dirt': (120, 94, 64),
    'sand': (196, 172, 124),
    'water': (44, 92, 122),
}
# floor types other than the theme's own '.': texture and colour
SPECIAL = {
    'grass': ('grass', GROUND['grass']),
    'dirt': ('dirt', GROUND['dirt']),
    'sand': ('dirt', GROUND['sand']),
    'water': ('water', GROUND['water']),
    'wood': ('planks', (132, 92, 58)),
    'stone': ('flagstone', (116, 110, 100)),
    'cobble': ('cobble', (124, 118, 110)),
}
NATURAL = {'grass', 'dirt', 'sand', 'water'}
M32 = np.uint64(0xFFFFFFFF)


# ---------- coordinate-hashed noise ----------
def hash2(ix, iy, seed):
    """Deterministic value in [0, 1) for integer lattice points (arrays broadcast)."""
    h = (
        np.asarray(ix).astype(np.int64).astype(np.uint64) * np.uint64(0x9E3779B1)
        ^ np.asarray(iy).astype(np.int64).astype(np.uint64) * np.uint64(0x85EBCA77)
        ^ np.uint64((seed * 0xC2B2AE3D) & 0xFFFFFFFF)
    ) & M32
    h ^= h >> np.uint64(15)
    h = (h * np.uint64(0x2C1B3C6D)) & M32
    h ^= h >> np.uint64(12)
    h = (h * np.uint64(0x297A2D39)) & M32
    h ^= h >> np.uint64(15)
    return (h & M32).astype(np.float32) / np.float32(4294967296.0)


def vnoise(win, sx, sy, seed):
    x0, y0, w, h = win
    xs, ys = np.arange(x0, x0 + w) / sx, np.arange(y0, y0 + h) / sy
    ix, iy = np.floor(xs).astype(np.int64), np.floor(ys).astype(np.int64)
    fx, fy = (xs - ix).astype(np.float32), (ys - iy).astype(np.float32)
    fx, fy = fx * fx * (3 - 2 * fx), fy * fy * (3 - 2 * fy)
    gx, gy = np.arange(ix.min(), ix.max() + 2), np.arange(iy.min(), iy.max() + 2)
    G = hash2(gx[None, :], gy[:, None], seed)
    cx, cy = ix - gx[0], iy - gy[0]
    top = G[np.ix_(cy, cx)] + (G[np.ix_(cy, cx + 1)] - G[np.ix_(cy, cx)]) * fx[None, :]
    bot = G[np.ix_(cy + 1, cx)] + (G[np.ix_(cy + 1, cx + 1)] - G[np.ix_(cy + 1, cx)]) * fx[None, :]
    return top + (bot - top) * fy[:, None]


def fbm(win, size, seed, octaves=4):
    total, amp, norm = None, 1.0, 0.0
    for o in range(octaves):
        s = max(1.5, size / 2**o)
        n = vnoise(win, s, s, seed + 101 * o) * amp
        total = n if total is None else total + n
        norm += amp
        amp *= 0.5
    return total / norm


def specks(win, seed, p):
    x0, y0, w, h = win
    return hash2(np.arange(x0, x0 + w)[None, :], np.arange(y0, y0 + h)[:, None], seed) < p


def fill(win, col):
    return np.broadcast_to(np.array(col, np.float32), (win[3], win[2], 3)).copy()


def strips(start, length, lo, hi, seed, extent):
    """Deterministic run boundaries along one axis: rows of flagstones, planks in a row."""
    rng = np.random.default_rng([seed & 0xFFFFFFFF, 12345])
    n = int((extent + 16 * length) / (lo * length)) + 8
    return start - 8 * length + np.concatenate([[0], np.cumsum(length * rng.uniform(lo, hi, n))])


# ---------- textures: float32 (h, w, 3), 0..255, for a window of map pixels ----------
def flagstone(win, ctx, col, tile=0.5, mortar_px=3, dark=0.45, seed=0):
    x0, y0, w, h = win
    cell, W, H = ctx['cell'], ctx['W'], ctx['H']
    out, mortar = fill(win, col), np.zeros((h, w), bool)
    th = max(8, int(cell * tile))
    xs, ys = np.arange(x0, x0 + w), np.arange(y0, y0 + h)
    rows = strips(0, th, 0.8, 1.2, seed + 1, H)
    ri = np.searchsorted(rows, ys, side='right') - 1
    mortar |= ((ys - rows[ri]) < mortar_px)[:, None]
    for r in np.unique(ri):
        band = ri == r
        cols = strips(-int(hash2(r, 0, seed + 2) * th), th, 0.8, 1.7, seed * 7919 + int(r) + 3, W)
        k = np.searchsorted(cols, xs, side='right') - 1
        shade = 0.8 + 0.32 * hash2(k, r, seed + 4)
        tint = (hash2(k, r, seed + 5) - 0.5)[:, None] * 10 + (
            np.stack([hash2(k, r, seed + 6 + c) for c in range(3)], 1) - 0.5
        ) * 6
        out[band] = (np.array(col, np.float32) * shade[:, None] + tint)[None, :, :]
        mortar[band] |= ((xs - cols[k]) < mortar_px)[None, :]
    out *= (0.82 + 0.34 * fbm(win, cell * 0.7, seed + 7))[..., None]
    out *= (0.9 + 0.2 * fbm(win, 5, seed + 8, 2))[..., None]
    out[mortar] *= dark
    return out


def planks(win, ctx, col, cell=None, seed=0):
    x0, y0, w, h = win
    cell = cell or ctx['cell']
    out, seam = fill(win, col), np.zeros((h, w), bool)
    rowh = max(8, cell // 4)
    xs, ys = np.arange(x0, x0 + w), np.arange(y0, y0 + h)
    ri = np.floor_divide(ys, rowh)
    seam |= ((ys - ri * rowh) < 2)[:, None]
    for r in np.unique(ri):
        band = ri == r
        cols = strips(
            -int(hash2(r, 1, seed) * cell * 3),
            cell,
            1.5,
            4.0,
            seed * 104729 + int(r) + 11,
            ctx['W'],
        )
        k = np.searchsorted(cols, xs, side='right') - 1
        shade = 0.8 + 0.35 * hash2(k, r, seed + 12)
        tint = (hash2(k, r, seed + 13) - 0.5) * 12
        out[band] = (np.array(col, np.float32) * shade[:, None] + tint[:, None])[None, :, :]
        dx = xs - cols[k]
        seam[band] |= (dx < 2)[None, :]
        nail_x = (dx >= 7) & (dx < 10)
        nail_y = band & (np.abs((ys - r * rowh) - rowh // 3) <= 1) | band & (
            np.abs((ys - r * rowh) - 2 * rowh // 3) <= 1
        )
        out[np.ix_(nail_y, nail_x)] *= 0.45
    out *= (0.84 + 0.3 * vnoise(win, cell, 3, seed + 14))[..., None]
    out *= (0.92 + 0.16 * fbm(win, 4, seed + 15, 2))[..., None]
    out[seam] *= 0.42
    return out


def marble(win, ctx, col, seed=0):
    out = flagstone(win, ctx, col, tile=1.0, mortar_px=2, dark=0.7, seed=seed)
    n = fbm(win, ctx['cell'] * 1.5, seed + 21, 5)
    xs = np.arange(win[0], win[0] + win[2], dtype=np.float32)[None, :] / ctx['cell']
    vein = np.abs(np.sin((xs * 0.7 + n * 7) * math.pi))
    out *= np.where(vein < 0.05, 0.78, 1.0)[..., None]
    return out


def ground(win, ctx, col, rough=0.35, seed=0):
    out = fill(win, col)
    out *= (1 - rough / 2 + rough * fbm(win, ctx['cell'] * 1.2, seed + 31, 5))[..., None]
    out *= (0.88 + 0.24 * fbm(win, 3, seed + 32, 2))[..., None]
    out[specks(win, seed + 33, 0.004)] *= 0.6
    return out


def grass(win, ctx, col, seed=0):
    out = ground(win, ctx, col, 0.3, seed)
    tint = fbm(win, ctx['cell'] * 2, seed + 41, 3)
    out[..., 0] += (tint - 0.5) * 30  # patches that go yellow
    out[specks(win, seed + 42, 0.02)] *= 1.25
    return out


def water(win, ctx, col, seed=0):
    out = ground(win, ctx, col, 0.25, seed)
    n = fbm(win, ctx['cell'] * 0.8, seed + 51, 4)
    out += np.where(np.abs(np.sin(n * 22)) < 0.07, 40, 0)[..., None].astype(np.float32)
    return out


def sky(win, ctx, col, seed=0):
    ys = (np.arange(win[1], win[1] + win[3], dtype=np.float32) / max(1, ctx['H']))[:, None, None]
    out = np.array(col, np.float32) * (0.92 + 0.12 * ys) * np.ones((1, win[2], 1), np.float32)
    c = fbm(win, ctx['cell'] * 3, seed + 61, 5)
    a = np.clip((c - 0.52) * 4, 0, 1)[..., None]
    shade = (0.88 + 0.12 * fbm(win, ctx['cell'], seed + 62, 3))[..., None]
    return out * (1 - a) + np.array([246, 248, 252], np.float32) * shade * a


def rock(win, ctx, col, seed=0):
    out = ground(win, ctx, col, 0.5, seed)
    n = fbm(win, ctx['cell'] * 0.6, seed + 71, 4)
    out *= np.where(np.abs(n - 0.5) < 0.012, 0.55, 1.0)[..., None]
    return out


def cobble(win, ctx, col, seed=0):
    """Rounded cobblestones: Worley noise (nearest and second-nearest jittered points)."""
    x0, y0, w, h = win
    size = max(10, ctx['cell'] * 0.19)
    xs, ys = np.arange(x0, x0 + w) / size, np.arange(y0, y0 + h) / size
    ix, iy = np.floor(xs).astype(np.int64), np.floor(ys).astype(np.int64)
    f1 = np.full((h, w), 9.0, np.float32)
    f2 = np.full((h, w), 9.0, np.float32)
    owner = np.zeros((h, w), np.float32)
    for dy in (-1, 0, 1):
        for dx in (-1, 0, 1):
            cx, cy = ix + dx, iy + dy
            px = cx[None, :] + 0.15 + 0.7 * hash2(cx[None, :], cy[:, None], seed + 1)
            py = cy[:, None] + 0.15 + 0.7 * hash2(cx[None, :], cy[:, None], seed + 2)
            d = np.hypot(xs[None, :] - px, ys[:, None] - py).astype(np.float32)
            nearer = d < f1
            f2 = np.where(nearer, f1, np.minimum(f2, d))
            owner = np.where(nearer, hash2(cx[None, :], cy[:, None], seed + 3), owner)
            f1 = np.where(nearer, d, f1)
    edge = np.clip((f2 - f1) / 0.16, 0, 1)  # 0 in the gaps between stones
    dome = np.clip(1.1 - f1 * 1.3, 0.55, 1.1)  # stones catch the light in the middle
    out = fill(win, col) * (0.78 + 0.34 * owner)[..., None]
    out *= (dome * (0.35 + 0.65 * edge))[..., None]
    out *= (0.9 + 0.2 * fbm(win, ctx['cell'] * 1.5, seed + 4, 3))[..., None]
    return out


def plaster(win, ctx, col, seed=0):
    out = ground(win, ctx, col, 0.18, seed)
    return out * (0.94 + 0.08 * vnoise(win, 3, ctx['cell'] * 0.5, seed + 9))[..., None]


def texture(kind, win, ctx, col, seed):
    if kind == 'cobble':
        return cobble(win, ctx, col, seed)
    if kind == 'house':
        return plaster(win, ctx, col, seed)
    if kind == 'flagstone':
        return flagstone(win, ctx, col, seed=seed)
    if kind == 'stone':
        return flagstone(win, ctx, col, tile=0.34, mortar_px=2, dark=0.55, seed=seed)
    if kind == 'planks':
        return planks(win, ctx, col, seed=seed)
    if kind == 'timber':
        return planks(win, ctx, col, cell=ctx['cell'] // 2, seed=seed)
    if kind in ('cave', 'dirt', 'sand'):
        return ground(win, ctx, col, seed=seed)
    return {
        'marble': marble,
        'grass': grass,
        'water': water,
        'sky': sky,
        'rock': rock,
        'cavewall': rock,
    }[kind](win, ctx, col, seed=seed)


def to_img(arr):
    return Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8), 'RGB')


# ---------- layout helpers ----------
def floor_type(cells, i, j, default='floor'):
    """What the floor is under (i, j). Furniture stands on the nearest floor reachable through other furniture
    (never through a wall), so a shelf in a stone room sits on stone even when boxed in by more furniture."""
    name = cells[i][j]
    if name in FLOORS:
        return 'floor' if name == 'rug' else name
    H, W = len(cells), len(cells[0])
    frontier, seen = [(i, j)], {(i, j)}
    for _ in range(8):
        counts, nxt = {}, []
        for a, b in frontier:
            for di, dj in ((0, 1), (1, 0), (0, -1), (-1, 0)):
                p, q = a + di, b + dj
                if not (0 <= p < H and 0 <= q < W) or (p, q) in seen:
                    continue
                seen.add((p, q))
                n = cells[p][q]
                if n in FLOORS - {'rug'}:
                    counts[n] = counts.get(n, 0) + 1
                elif n == 'rug':
                    counts['floor'] = counts.get('floor', 0) + 1
                elif n in PROPS:
                    nxt.append((p, q))
        if counts:
            return max(counts, key=counts.get)
        frontier = nxt
        if not frontier:
            break
    return default


def type_map(cells):
    """Half-cell map of floor types. The half of a wall cell that faces a room shows that room's floor."""
    H, W = len(cells), len(cells[0])
    nodes = wall_nodes(cells)
    tm = [['void'] * (2 * W) for _ in range(2 * H)]
    for i in range(H):
        for j in range(W):
            for qi in (0, 1):
                for qj in (0, 1):
                    if cells[i][j] not in WALLS:
                        t = 'void' if cells[i][j] == 'void' else floor_type(cells, i, j)
                    elif (i, j) in nodes:
                        dy, dx = (qi * 2 - 1), (qj * 2 - 1)
                        t = next(
                            (
                                floor_type(cells, a, b)
                                for a, b in ((i + dy, j), (i, j + dx), (i + dy, j + dx))
                                if is_open(cells, a, b)
                            ),
                            'void',
                        )
                    else:
                        t = 'void'
                    tm[2 * i + qi][2 * j + qj] = t
    return tm


def components(cells, name):
    seen, out = set(), []
    for i, row in enumerate(cells):
        for j, n in enumerate(row):
            if n != name or (i, j) in seen:
                continue
            stack, comp = [(i, j)], []
            seen.add((i, j))
            while stack:
                a, b = stack.pop()
                comp.append((a, b))
                for da, db in ((0, 1), (1, 0), (0, -1), (-1, 0)):
                    q = (a + da, b + db)
                    if (
                        0 <= q[0] < len(cells)
                        and 0 <= q[1] < len(cells[0])
                        and q not in seen
                        and cells[q[0]][q[1]] == name
                    ):
                        seen.add(q)
                        stack.append(q)
            out.append(comp)
    return out


def neighbour_dir(cells, i, j, kinds):
    for di, dj in ((0, 1), (1, 0), (0, -1), (-1, 0)):
        a, b = i + di, j + dj
        if 0 <= a < len(cells) and 0 <= b < len(cells[0]) and cells[a][b] in kinds:
            return di, dj
    return None


MERGED = {
    'table',
    'bed',
    'shelf',
    'altar',
    'fireplace',
    'stairs',
    'hatch',
    'rug',
    'stall',
    'counter',
    'fountain',
    'cart',
    'bench',
}


def prop_list(cells, seed):
    """Every piece of furniture once: (name, cells it covers, i0, j0, i1, j1, seed)."""
    out = []
    for name in sorted({n for row in cells for n in row if n in PROPS or n == 'rug'}):
        for comp in components(cells, name):
            for g in [comp] if name in MERGED else [[x] for x in comp]:
                i0, i1 = min(a for a, _ in g), max(a for a, _ in g) + 1
                j0, j1 = min(b for _, b in g), max(b for _, b in g) + 1
                out.append(
                    (
                        name,
                        g,
                        i0,
                        j0,
                        i1,
                        j1,
                        (seed * 31 + i0 * 7919 + j0 * 104729 + sum(map(ord, name))) & 0xFFFFFFFF,
                    )
                )
    return out


# ---------- props, drawn at SS x on an RGBA layer ----------
def shade(col, f):
    return tuple(max(0, min(255, int(c * f))) for c in col[:3]) + (255,)


WOOD, DARKWOOD, IRON, STONE = (138, 94, 56), (92, 62, 38), (70, 72, 76), (150, 146, 138)


def inset(r, k):
    """Shrink a rectangle by k on every side, never past its own centre (small furniture stays drawable)."""
    kx, ky = min(k, (r[2] - r[0]) / 2 - 1), min(k, (r[3] - r[1]) / 2 - 1)
    return (r[0] + kx, r[1] + ky, r[2] - kx, r[3] - ky)


def prop(d, name, r, c, rng, cells, g):
    x0, y0, x1, y1 = r
    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
    horiz = (x1 - x0) >= (y1 - y0)
    if name == 'rug':
        d.rectangle(
            inset(r, c * 0.08),
            fill=(122, 32, 36, 255),
            outline=(196, 150, 70, 255),
            width=int(c * 0.05),
        )
        d.rectangle(inset(r, c * 0.22), outline=(196, 150, 70, 200), width=int(c * 0.02))
    elif name == 'table':
        rr = inset(r, c * 0.14)
        d.rounded_rectangle(
            rr,
            radius=c * 0.08,
            fill=shade(WOOD, 1.05),
            outline=shade(WOOD, 0.55),
            width=int(c * 0.03),
        )
        for k in range(1, 4):  # planks along the long side
            if horiz:
                y = rr[1] + (rr[3] - rr[1]) * k / 4
                d.line((rr[0] + 6, y, rr[2] - 6, y), fill=shade(WOOD, 0.8), width=2)
            else:
                x = rr[0] + (rr[2] - rr[0]) * k / 4
                d.line((x, rr[1] + 6, x, rr[3] - 6), fill=shade(WOOD, 0.8), width=2)
        for _ in range(max(1, len(g))):  # mugs and plates
            px, py = (
                rng.uniform(rr[0] + c * 0.15, rr[2] - c * 0.15),
                rng.uniform(rr[1] + c * 0.15, rr[3] - c * 0.15),
            )
            s = c * rng.uniform(0.07, 0.11)
            d.ellipse(
                (px - s, py - s, px + s, py + s),
                fill=(214, 206, 188, 255) if rng.random() < 0.5 else (120, 124, 130, 255),
                outline=(60, 50, 40, 255),
                width=2,
            )
    elif name == 'chair':
        s = c * 0.24
        d.rounded_rectangle(
            (cx - s, cy - s, cx + s, cy + s),
            radius=c * 0.04,
            fill=shade(WOOD, 0.95),
            outline=shade(WOOD, 0.5),
            width=3,
        )
        i, j = g[0]
        t = neighbour_dir(cells, i, j, {'table'}) or (1, 0)
        if t[1]:  # the back faces away from the table
            bx = cx - t[1] * s
            d.rectangle((bx - c * 0.05, cy - s, bx + c * 0.05, cy + s), fill=shade(WOOD, 0.6))
        else:
            by = cy - t[0] * s
            d.rectangle((cx - s, by - c * 0.05, cx + s, by + c * 0.05), fill=shade(WOOD, 0.6))
    elif name == 'barrel':
        s = c * 0.36
        d.ellipse(
            (cx - s, cy - s, cx + s, cy + s),
            fill=shade(WOOD, 1.0),
            outline=shade(IRON, 0.8),
            width=int(c * 0.04),
        )
        d.ellipse(
            (cx - s * 0.7, cy - s * 0.7, cx + s * 0.7, cy + s * 0.7),
            outline=shade(IRON, 0.9),
            width=int(c * 0.025),
        )
        for k in (-0.35, 0, 0.35):
            d.line(
                (cx - s * 0.62, cy + s * k, cx + s * 0.62, cy + s * k),
                fill=shade(WOOD, 0.75),
                width=2,
            )
    elif name == 'crate':
        rr = inset(r, c * 0.1)
        d.rectangle(rr, fill=(160, 124, 76, 255), outline=(90, 66, 40, 255), width=int(c * 0.035))
        d.line((rr[0], rr[1], rr[2], rr[3]), fill=(110, 82, 50, 255), width=int(c * 0.04))
        d.rectangle(inset(rr, c * 0.06), outline=(110, 82, 50, 255), width=int(c * 0.025))
    elif name == 'chest':
        w, h = (c * 0.34, c * 0.22) if horiz else (c * 0.22, c * 0.34)
        d.rounded_rectangle(
            (cx - w, cy - h, cx + w, cy + h),
            radius=c * 0.04,
            fill=(120, 72, 36, 255),
            outline=(50, 30, 16, 255),
            width=3,
        )
        for k in (-0.5, 0.5):
            if horiz:
                d.line(
                    (cx + w * k, cy - h, cx + w * k, cy + h),
                    fill=shade(IRON, 1.2),
                    width=int(c * 0.035),
                )
            else:
                d.line(
                    (cx - w, cy + h * k, cx + w, cy + h * k),
                    fill=shade(IRON, 1.2),
                    width=int(c * 0.035),
                )
        d.rectangle(
            (cx - c * 0.04, cy - c * 0.04, cx + c * 0.04, cy + c * 0.04), fill=(222, 184, 70, 255)
        )
    elif name == 'bed':
        rr = inset(r, c * 0.1)
        d.rounded_rectangle(rr, radius=c * 0.06, fill=shade(WOOD, 0.7))
        blanket = inset(rr, c * 0.05)
        d.rounded_rectangle(blanket, radius=c * 0.05, fill=(118, 40, 44, 255))
        i, j = g[0]
        wall = neighbour_dir(cells, i, j, set(WALLS)) or ((-1, 0) if not horiz else (0, -1))
        pw, b = c * 0.3, blanket
        pillow = {
            (-1, 0): (b[0] + 4, b[1] + 4, b[2] - 4, b[1] + pw),
            (1, 0): (b[0] + 4, b[3] - pw, b[2] - 4, b[3] - 4),
            (0, -1): (b[0] + 4, b[1] + 4, b[0] + pw, b[3] - 4),
            (0, 1): (b[2] - pw, b[1] + 4, b[2] - 4, b[3] - 4),
        }[wall]
        d.rounded_rectangle(pillow, radius=c * 0.05, fill=(232, 226, 212, 255))
    elif name == 'shelf':
        rr = (
            (x0 + c * 0.08, cy - c * 0.2, x1 - c * 0.08, cy + c * 0.2)
            if horiz
            else (cx - c * 0.2, y0 + c * 0.08, cx + c * 0.2, y1 - c * 0.08)
        )
        d.rectangle(rr, fill=shade(DARKWOOD, 1.0), outline=shade(DARKWOOD, 0.5), width=3)
        n = int(((rr[2] - rr[0]) if horiz else (rr[3] - rr[1])) / (c * 0.07))
        books = [(140, 40, 40), (40, 70, 120), (60, 100, 50), (150, 120, 60), (90, 60, 100)]
        for k in range(n):
            col = books[int(rng.integers(0, len(books)))] + (255,)
            if horiz:
                x = rr[0] + 4 + k * c * 0.07
                d.rectangle((x, rr[1] + 5, x + c * 0.05, rr[3] - 5), fill=col)
            else:
                y = rr[1] + 4 + k * c * 0.07
                d.rectangle((rr[0] + 5, y, rr[2] - 5, y + c * 0.05), fill=col)
    elif name == 'altar':
        rr = inset(r, c * 0.12)
        d.rectangle(rr, fill=shade(STONE, 1.0), outline=shade(STONE, 0.5), width=int(c * 0.03))
        runner = (
            (rr[0], cy - c * 0.12, rr[2], cy + c * 0.12)
            if horiz
            else (cx - c * 0.12, rr[1], cx + c * 0.12, rr[3])
        )
        d.rectangle(runner, fill=(110, 30, 40, 255))
    elif name in ('pillar', 'statue', 'mast'):
        s = c * {'pillar': 0.32, 'statue': 0.4, 'mast': 0.26}[name]
        base = {'pillar': STONE, 'statue': STONE, 'mast': DARKWOOD}[name]
        for k in range(8, 0, -1):  # radial shading, lit from the top left
            f = 0.62 + 0.5 * (1 - k / 8)
            rk, o = s * k / 8, s * 0.1 * (1 - k / 8)
            d.ellipse((cx - rk - o, cy - rk - o, cx + rk - o, cy + rk - o), fill=shade(base, f))
        d.ellipse((cx - s, cy - s, cx + s, cy + s), outline=shade(base, 0.45), width=3)
        if name == 'statue':
            d.ellipse(
                (cx - s * 0.45, cy - s * 0.55, cx + s * 0.45, cy + s * 0.35),
                fill=shade(STONE, 1.25),
            )
            d.ellipse(
                (cx - s * 0.2, cy - s * 0.75, cx + s * 0.2, cy - s * 0.35), fill=shade(STONE, 1.35)
            )
        if name == 'mast':
            d.line(
                (cx - c * 0.9, cy, cx + c * 0.9, cy), fill=shade(DARKWOOD, 0.8), width=int(c * 0.08)
            )
    elif name in ('tree', 'bush'):
        big = name == 'tree'
        greens = [(52, 88, 40), (62, 102, 46), (74, 116, 52), (88, 130, 60)]
        R = c * (0.85 if big else 0.42)
        blobs = int(rng.integers(9, 14)) if big else int(rng.integers(5, 8))
        for layer, f in enumerate(
            (1.0, 0.72, 0.45)
        ):  # canopy built up in lighter layers, lit from the top left
            for _ in range(blobs if layer == 0 else blobs // (layer + 1)):
                a, dist = rng.uniform(0, 2 * math.pi), rng.uniform(0, R * 0.55 * f)
                rr_ = R * rng.uniform(0.3, 0.5) * (f + 0.2)
                px, py = (
                    cx + math.cos(a) * dist - layer * c * 0.05,
                    cy + math.sin(a) * dist - layer * c * 0.05,
                )
                d.ellipse(
                    (px - rr_, py - rr_, px + rr_, py + rr_),
                    fill=greens[min(3, layer + int(rng.integers(0, 2)))] + (255,),
                )
    elif name == 'rock':
        pts = []
        n = int(rng.integers(7, 10))
        for k in range(n):
            a = 2 * math.pi * k / n + rng.uniform(-0.2, 0.2)
            rad = c * rng.uniform(0.28, 0.42)
            pts.append((cx + rad * math.cos(a), cy + rad * math.sin(a)))
        d.polygon(pts, fill=(112, 108, 102, 255), outline=(60, 58, 55, 255))
        d.polygon(
            [(cx + (x - cx) * 0.5 - c * 0.06, cy + (y - cy) * 0.5 - c * 0.06) for x, y in pts],
            fill=(138, 134, 126, 255),
        )
    elif name == 'cannon':
        i, j = g[0]
        t = neighbour_dir(cells, i, j, {'railing', 'wall', 'window', 'void'}) or (0, 1)
        d.rounded_rectangle(
            (cx - c * 0.25, cy - c * 0.25, cx + c * 0.25, cy + c * 0.25),
            radius=c * 0.05,
            fill=shade(DARKWOOD, 1.0),
        )
        ex, ey = cx + t[1] * c * 0.5, cy + t[0] * c * 0.5
        d.line(
            (cx - t[1] * c * 0.2, cy - t[0] * c * 0.2, ex, ey),
            fill=(40, 42, 46, 255),
            width=int(c * 0.2),
        )
        d.ellipse((ex - c * 0.1, ey - c * 0.1, ex + c * 0.1, ey + c * 0.1), fill=(20, 20, 22, 255))
    elif name == 'brazier':
        s = c * 0.3
        d.ellipse(
            (cx - s, cy - s, cx + s, cy + s),
            fill=(46, 44, 42, 255),
            outline=(20, 20, 20, 255),
            width=3,
        )
        for f, col in ((0.75, (200, 70, 20)), (0.5, (255, 140, 40)), (0.25, (255, 226, 130))):
            d.ellipse((cx - s * f, cy - s * f, cx + s * f, cy + s * f), fill=col + (255,))
    elif name == 'lantern':
        s = c * 0.12
        d.rectangle((cx - s, cy - s, cx + s, cy + s), fill=(40, 36, 30, 255))
        d.ellipse(
            (cx - s * 0.7, cy - s * 0.7, cx + s * 0.7, cy + s * 0.7), fill=(255, 220, 130, 255)
        )
    elif name == 'candles':
        for _ in range(3):
            px, py = cx + rng.uniform(-c * 0.2, c * 0.2), cy + rng.uniform(-c * 0.2, c * 0.2)
            d.ellipse(
                (px - c * 0.05, py - c * 0.05, px + c * 0.05, py + c * 0.05),
                fill=(236, 226, 200, 255),
            )
            d.ellipse(
                (px - c * 0.02, py - c * 0.02, px + c * 0.02, py + c * 0.02),
                fill=(255, 190, 80, 255),
            )
    elif name == 'crystal':
        for _ in range(4):
            a = rng.uniform(0, 2 * math.pi)
            px, py = cx + math.cos(a) * c * 0.12, cy + math.sin(a) * c * 0.12
            s = c * rng.uniform(0.08, 0.16)
            d.polygon(
                [(px, py - s * 1.6), (px + s, py), (px, py + s * 1.2), (px - s, py)],
                fill=(120, 214, 255, 255),
                outline=(40, 110, 160, 255),
            )
    elif name == 'fireplace':
        rr = inset(r, c * 0.06)
        d.rectangle(rr, fill=shade(STONE, 0.8), outline=shade(STONE, 0.4), width=4)
        fire = inset(rr, c * 0.2)
        d.rectangle(fire, fill=(30, 22, 18, 255))
        small = min(fire[2] - fire[0], fire[3] - fire[1])
        for f, col in ((0.0, (200, 70, 20)), (0.18, (255, 150, 40)), (0.34, (255, 230, 140))):
            d.ellipse(inset(fire, small * f), fill=col + (255,))
    elif name == 'hatch':
        rr = inset(r, c * 0.12)
        d.rectangle(rr, fill=shade(DARKWOOD, 1.1), outline=shade(IRON, 0.8), width=int(c * 0.04))
        d.ellipse(
            (cx - c * 0.08, cy - c * 0.08, cx + c * 0.08, cy + c * 0.08),
            outline=shade(IRON, 1.3),
            width=4,
        )
    elif name == 'well':
        s = c * 0.4
        d.ellipse(
            (cx - s, cy - s, cx + s, cy + s),
            fill=shade(STONE, 0.95),
            outline=shade(STONE, 0.5),
            width=int(c * 0.03),
        )
        d.ellipse(
            (cx - s * 0.62, cy - s * 0.62, cx + s * 0.62, cy + s * 0.62), fill=(24, 34, 44, 255)
        )
        d.line(
            (cx - s * 1.05, cy, cx + s * 1.05, cy), fill=shade(DARKWOOD, 1.0), width=int(c * 0.07)
        )
        d.rectangle(
            (cx - c * 0.06, cy - c * 0.06, cx + c * 0.06, cy + c * 0.06), fill=shade(WOOD, 0.8)
        )
    elif name == 'fountain':
        rr = inset(r, c * 0.08)
        d.ellipse(rr, fill=shade(STONE, 1.0), outline=shade(STONE, 0.5), width=int(c * 0.04))
        d.ellipse(inset(rr, c * 0.12), fill=(58, 118, 150, 255))
        for k in range(3):
            q = c * (0.12 + 0.12 * k)
            d.ellipse((cx - q, cy - q, cx + q, cy + q), outline=(150, 200, 225, 160), width=2)
        d.ellipse(
            (cx - c * 0.12, cy - c * 0.12, cx + c * 0.12, cy + c * 0.12), fill=shade(STONE, 1.2)
        )
    elif name == 'stall':
        rr = inset(r, c * 0.06)
        colours = [(176, 52, 48), (40, 96, 150), (196, 150, 46), (70, 120, 60), (120, 60, 120)]
        a_, b_ = colours[int(rng.integers(0, 5))], (226, 216, 192)
        n = max(3, int(((rr[2] - rr[0]) if horiz else (rr[3] - rr[1])) / (c * 0.22)))
        for k in range(n):  # striped canopy
            f0, f1 = k / n, (k + 1) / n
            box = (
                (rr[0] + (rr[2] - rr[0]) * f0, rr[1], rr[0] + (rr[2] - rr[0]) * f1, rr[3])
                if horiz
                else (rr[0], rr[1] + (rr[3] - rr[1]) * f0, rr[2], rr[1] + (rr[3] - rr[1]) * f1)
            )
            d.rectangle(box, fill=(a_ if k % 2 else b_) + (255,))
        d.rectangle(rr, outline=shade(a_, 0.5), width=int(c * 0.03))
        d.line(
            (rr[0], cy, rr[2], cy) if horiz else (cx, rr[1], cx, rr[3]),
            fill=shade(a_, 0.6),
            width=3,
        )
    elif name == 'counter':
        rr = (
            (x0 + c * 0.06, cy - c * 0.22, x1 - c * 0.06, cy + c * 0.22)
            if horiz
            else (cx - c * 0.22, y0 + c * 0.06, cx + c * 0.22, y1 - c * 0.06)
        )
        d.rectangle(
            rr, fill=shade(DARKWOOD, 1.15), outline=shade(DARKWOOD, 0.5), width=int(c * 0.03)
        )
        for _ in range(max(1, len(g))):
            px, py = (
                rng.uniform(rr[0] + c * 0.1, rr[2] - c * 0.1),
                rng.uniform(rr[1] + c * 0.1, rr[3] - c * 0.1),
            )
            q = c * 0.06
            d.ellipse(
                (px - q, py - q, px + q, py + q),
                fill=(206, 196, 170, 255),
                outline=(70, 60, 50, 255),
            )
    elif name == 'anvil':
        d.ellipse((cx - c * 0.3, cy - c * 0.3, cx + c * 0.3, cy + c * 0.3), fill=shade(WOOD, 0.8))
        d.polygon(
            [
                (cx - c * 0.28, cy - c * 0.1),
                (cx + c * 0.18, cy - c * 0.1),
                (cx + c * 0.34, cy),
                (cx + c * 0.18, cy + c * 0.1),
                (cx - c * 0.28, cy + c * 0.1),
            ],
            fill=(52, 54, 60, 255),
            outline=(20, 20, 24, 255),
        )
    elif name == 'cart':
        rr = inset(r, c * 0.16)
        d.rectangle(rr, fill=shade(WOOD, 1.0), outline=shade(WOOD, 0.45), width=int(c * 0.03))
        for k in range(1, 4):
            if horiz:
                x = rr[0] + (rr[2] - rr[0]) * k / 4
                d.line((x, rr[1], x, rr[3]), fill=shade(WOOD, 0.7), width=2)
            else:
                y = rr[1] + (rr[3] - rr[1]) * k / 4
                d.line((rr[0], y, rr[2], y), fill=shade(WOOD, 0.7), width=2)
        wh = c * 0.12
        if horiz:
            wheels = [
                ((rr[0] + rr[2]) / 2, rr[1] - wh / 2, c * 0.28, wh),
                ((rr[0] + rr[2]) / 2, rr[3] + wh / 2, c * 0.28, wh),
            ]
        else:
            wheels = [
                (rr[0] - wh / 2, (rr[1] + rr[3]) / 2, wh, c * 0.28),
                (rr[2] + wh / 2, (rr[1] + rr[3]) / 2, wh, c * 0.28),
            ]
        for px, py, ww, hh in wheels:
            d.rectangle(
                (px - ww / 2, py - hh / 2, px + ww / 2, py + hh / 2), fill=(40, 30, 22, 255)
            )
    elif name == 'sacks':
        for _ in range(3):
            px, py = cx + rng.uniform(-c * 0.18, c * 0.18), cy + rng.uniform(-c * 0.18, c * 0.18)
            q = c * rng.uniform(0.13, 0.18)
            d.ellipse(
                (px - q, py - q * 0.8, px + q, py + q * 0.8),
                fill=(190, 166, 120, 255),
                outline=(110, 90, 60, 255),
                width=2,
            )
    elif name == 'lamppost':
        d.ellipse((cx - c * 0.1, cy - c * 0.1, cx + c * 0.1, cy + c * 0.1), fill=(34, 34, 38, 255))
        d.ellipse(
            (cx - c * 0.06, cy - c * 0.06, cx + c * 0.06, cy + c * 0.06), fill=(255, 214, 140, 255)
        )
    elif name == 'bench':
        rr = (
            (x0 + c * 0.1, cy - c * 0.12, x1 - c * 0.1, cy + c * 0.12)
            if horiz
            else (cx - c * 0.12, y0 + c * 0.1, cx + c * 0.12, y1 - c * 0.1)
        )
        d.rectangle(rr, fill=shade(WOOD, 1.0), outline=shade(WOOD, 0.5), width=2)
    elif name == 'flowers':
        for _ in range(12):
            px, py = cx + rng.uniform(-c * 0.38, c * 0.38), cy + rng.uniform(-c * 0.38, c * 0.38)
            q = c * 0.05
            col = [(220, 70, 80), (240, 200, 70), (180, 110, 210), (240, 240, 240)][
                int(rng.integers(0, 4))
            ]
            d.ellipse((px - q, py - q, px + q, py + q), fill=col + (255,))
    elif name == 'grave':
        d.rounded_rectangle(
            (cx - c * 0.2, cy - c * 0.3, cx + c * 0.2, cy + c * 0.3),
            radius=c * 0.12,
            fill=shade(STONE, 0.95),
            outline=shade(STONE, 0.5),
            width=3,
        )
        d.line((cx, cy - c * 0.18, cx, cy + c * 0.1), fill=shade(STONE, 0.6), width=3)
        d.line(
            (cx - c * 0.1, cy - c * 0.08, cx + c * 0.1, cy - c * 0.08),
            fill=shade(STONE, 0.6),
            width=3,
        )
    elif name == 'stairs':
        n = max(4, int(((x1 - x0) if horiz else (y1 - y0)) / (c * 0.2)))
        for k in range(n):
            f = 1.15 - 0.6 * k / n
            if horiz:
                a = x0 + (x1 - x0) * k / n
                d.rectangle((a, y0 + 4, a + (x1 - x0) / n - 3, y1 - 4), fill=shade(STONE, f))
            else:
                a = y0 + (y1 - y0) * k / n
                d.rectangle((x0 + 4, a, x1 - 4, a + (y1 - y0) / n - 3), fill=shade(STONE, f))


def through(canvas, kind, win, ctx, col, seed, mask, factor=1.0):
    """Composite a texture onto canvas through mask, computing it only where the mask is set."""
    box = mask.getbbox()
    if not box:
        return canvas
    bx0, by0, bx1, by1 = box
    sub = (win[0] + bx0, win[1] + by0, bx1 - bx0, by1 - by0)
    tex = texture(kind, sub, ctx, col, seed) if kind != 'raw' else col(sub)
    if factor != 1.0:
        tex = tex * factor
    part = canvas.crop(box)
    canvas.paste(Image.composite(to_img(tex), part, mask.crop(box)), box[:2])
    return canvas


# ---------- one tile ----------
CTX = {}


def _init(ctx):
    CTX.update(ctx)


def seg_px(s, cell):
    (ax, ay), (bx, by) = s['pts']
    k = cell / 2
    return ax * k, ay * k, bx * k, by * k


def paint_tile(core):
    """Paint one tile plus a margin (so blurs and shadows cross tile edges correctly); return the core."""
    ctx = CTX
    cell, cells, seed = ctx['cell'], ctx['cells'], ctx['seed']
    cx0, cy0, cw, ch = core
    M = ctx['margin']
    win = (cx0 - M, cy0 - M, cw + 2 * M, ch + 2 * M)
    x0, y0, w, h = win
    floor_tex, floor_col, wall_style, wall_col, void_tex, void_col = THEMES[ctx['theme']]
    half = cell // 2

    # 1. ground: void first, then each floor type painted through its (feathered) mask
    tm = ctx['tm']
    ys = np.clip(np.floor_divide(np.arange(y0, y0 + h), half), 0, tm.shape[0] - 1)
    xs = np.clip(np.floor_divide(np.arange(x0, x0 + w), half), 0, tm.shape[1] - 1)
    tmi = tm[np.ix_(ys, xs)]
    names = ctx['type_names']
    canvas = to_img(texture(void_tex, win, ctx, void_col, seed + 1000))
    for t_index in sorted(set(np.unique(tmi).tolist()) - {names.index('void')}):
        t = names[t_index]
        mask = Image.fromarray(((tmi == t_index) * 255).astype(np.uint8), 'L')
        if t in NATURAL and ctx['theme'] in ('outdoor', 'cave'):
            # organic edges: blur the blocky outline, then let noise decide where it lands
            b = np.asarray(mask.filter(ImageFilter.GaussianBlur(half * 0.8)), np.float32) / 255
            n = fbm(win, cell * 0.8, seed + 77 + t_index, 3)
            mask = Image.fromarray(
                (np.clip((b - 0.5 + (n - 0.5) * 0.5) * 6 + 0.5, 0, 1) * 255).astype(np.uint8), 'L'
            )
        elif t in NATURAL or ctx['theme'] == 'cave':
            mask = mask.filter(ImageFilter.GaussianBlur(half * 0.35))
        kind, col = (floor_tex, floor_col) if t == 'floor' else SPECIAL[t]
        canvas = through(canvas, kind, win, ctx, col, seed + 2000 + t_index * 97, mask)

    # 2. wall masks at SS x, only for segments near this tile
    body_w = {'stone': 0.42, 'timber': 0.3, 'cavewall': 0.5, 'house': 0.34}[wall_style] * cell
    slack = cell * 1.5
    masks = {
        k: Image.new('L', (w * SS, h * SS), 0)
        for k in ('wall', 'stone', 'window', 'railing', 'hedge', 'door')
    }
    draws = {k: ImageDraw.Draw(m) for k, m in masks.items()}
    T = lambda x, y: ((x - x0) * SS, (y - y0) * SS)
    for s in ctx['segments']:
        if s.get('prop'):
            continue
        ax, ay, bx, by = seg_px(s, cell)
        if (
            max(ax, bx) < x0 - slack
            or min(ax, bx) > x0 + w + slack
            or max(ay, by) < y0 - slack
            or min(ay, by) > y0 + h + slack
        ):
            continue
        kind = {'door': 'door', 'secret door': 'wall', 'stone wall': 'stone'}.get(
            s['kind'], s['kind']
        )
        L = math.hypot(bx - ax, by - ay) or 1
        ux, uy = (bx - ax) / L, (by - ay) / L
        ext = lambda e: (*T(ax - ux * e, ay - uy * e), *T(bx + ux * e, by + uy * e))
        if kind == 'door':
            draws['door'].line(ext(-cell * 0.1), fill=255, width=int(cell * SS * 0.26))
            r = body_w * 0.55 * SS
            for px, py in (T(ax, ay), T(bx, by)):  # posts either side of the door
                draws['wall'].rectangle((px - r, py - r, px + r, py + r), fill=255)
        elif kind == 'railing':
            draws['railing'].line(ext(cell * 0.06), fill=255, width=int(cell * SS * 0.12))
        elif kind == 'hedge':
            rng = np.random.default_rng(
                [
                    int(ax) & 0xFFFF,
                    int(ay) & 0xFFFF,
                    int(bx) & 0xFFFF,
                    int(by) & 0xFFFF,
                    seed & 0xFFFF,
                ]
            )
            n = int(L / (cell * 0.15)) + 1
            for k in range(n + 1):
                f = k / max(1, n)
                rad = cell * SS * rng.uniform(0.22, 0.34)
                px, py = T(ax + (bx - ax) * f, ay + (by - ay) * f)
                draws['hedge'].ellipse((px - rad, py - rad, px + rad, py + rad), fill=255)
        else:
            width = body_w * (0.75 if kind == 'window' else 1.25 if kind == 'stone' else 1)
            draws[kind].line(ext(width / 2), fill=255, width=int(width * SS))
    masks = {k: m.resize((w, h), Image.LANCZOS) for k, m in masks.items()}
    present = {k for k, m in masks.items() if m.getbbox()}
    solid = Image.fromarray(
        np.maximum.reduce([np.asarray(masks[k]) for k in ('wall', 'stone', 'window', 'hedge')]), 'L'
    )

    # 3. props near this tile
    props = Image.new('RGBA', (w * SS, h * SS), (0, 0, 0, 0))
    dp = ImageDraw.Draw(props)
    c2 = cell * SS
    for name, g, i0, j0, i1, j1, pseed in ctx['props']:
        if (
            j1 * cell < x0 - slack
            or j0 * cell > x0 + w + slack
            or i1 * cell < y0 - slack
            or i0 * cell > y0 + h + slack
        ):
            continue
        r = (
            (j0 * cell - x0) * SS,
            (i0 * cell - y0) * SS,
            (j1 * cell - x0) * SS,
            (i1 * cell - y0) * SS,
        )
        prop(dp, name, r, c2, np.random.default_rng(pseed), cells, g)
    props = props.resize((w, h), Image.LANCZOS)

    # 4. shadows and ambient occlusion under walls and props
    arr = np.asarray(canvas, np.float32)
    if solid.getbbox():
        ao = np.asarray(solid.filter(ImageFilter.GaussianBlur(cell * 0.3)), np.float32) / 255
        arr *= (1 - 0.35 * ao)[..., None]
    caster = Image.fromarray(np.maximum(np.asarray(solid), np.asarray(props.getchannel('A'))), 'L')
    if caster.getbbox():
        off = int(cell * 0.06)
        sh = Image.new('L', (w, h), 0)
        sh.paste(caster.filter(ImageFilter.GaussianBlur(cell * 0.06)), (off, off))
        arr *= (1 - 0.5 * np.asarray(sh, np.float32) / 255)[..., None]
    canvas = to_img(arr)
    canvas.paste(props, (0, 0), props)

    # 5. walls: textured bodies with darker rims (textures only made if this tile has that kind)
    if 'wall' in present:
        canvas = through(canvas, wall_style, win, ctx, wall_col, seed + 3000, masks['wall'])
    if 'window' in present:
        canvas = through(canvas, wall_style, win, ctx, wall_col, seed + 3000, masks['window'], 1.15)
        thin = masks['window'].filter(ImageFilter.MinFilter(max(3, int(cell * 0.09) // 2 * 2 + 1)))
        canvas = Image.composite(Image.new('RGB', (w, h), (150, 205, 225)), canvas, thin)
    if 'stone' in present:
        canvas = through(canvas, 'stone', win, ctx, (104, 100, 94), seed + 3500, masks['stone'])
    if 'hedge' in present:
        canvas = through(
            canvas,
            'raw',
            win,
            ctx,
            lambda sub: ground(sub, ctx, (58, 96, 44), 0.6, seed + 4000),
            0,
            masks['hedge'],
        )
    if 'railing' in present:
        canvas = through(
            canvas,
            'raw',
            win,
            ctx,
            lambda sub: planks(sub, ctx, (120, 84, 52), cell // 2, seed + 5000),
            0,
            masks['railing'],
        )
    if 'door' in present:
        canvas = through(
            canvas,
            'raw',
            win,
            ctx,
            lambda sub: planks(sub, ctx, (150, 100, 58), cell // 3, seed + 6000),
            0,
            masks['door'],
        )
    arr = np.asarray(canvas, np.float32)
    if present:
        rim_src = Image.fromarray(np.maximum.reduce([np.asarray(masks[k]) for k in present]), 'L')
        rim = np.asarray(rim_src, np.float32) - np.asarray(
            rim_src.filter(ImageFilter.MinFilter(5)), np.float32
        )
        arr = arr * (1 - 0.55 * np.clip(rim, 0, 255)[..., None] / 255)

    # 6. baked glow around light sources (Foundry adds the real, dynamic light on top)
    for l in ctx['lights']:
        lx, ly = l['x'] * half - x0, l['y'] * half - y0
        r = l['bright'] / 5 * cell * 0.9
        a0, a1, b0, b1 = (
            int(max(0, lx - r)),
            int(min(w, lx + r)),
            int(max(0, ly - r)),
            int(min(h, ly + r)),
        )
        if a1 <= a0 or b1 <= b0:
            continue
        yy, xx = np.mgrid[b0:b1, a0:a1]
        g = np.clip(1 - np.hypot(xx - lx, yy - ly) / r, 0, 1) ** 2 * 0.55
        col = np.array([int(l['color'][k : k + 2], 16) for k in (1, 3, 5)], np.float32)
        arr[b0:b1, a0:a1] = 255 - (255 - arr[b0:b1, a0:a1]) * (1 - g[..., None] * col / 255)
    return core, np.clip(arr[M : M + ch, M : M + cw], 0, 255).astype(np.uint8)


# ---------- the whole map ----------
def render(meta, cells, segments, lights, out_base, check_path, jobs=None, buildings=()):
    cell = meta['cell']
    H, W = len(cells) * cell, len(cells[0]) * cell
    tm_names = type_map(cells)
    names = sorted({t for row in tm_names for t in row} | {'void'})
    ctx = dict(
        cell=cell,
        cells=cells,
        seed=meta['seed'],
        theme=meta['theme'],
        segments=segments,
        lights=lights,
        W=W,
        H=H,
        margin=int(max(2 * cell, 256)),
        type_names=names,
        tm=np.array([[names.index(t) for t in row] for row in tm_names], np.int16),
        props=prop_list(cells, meta['seed']),
    )
    cores = [
        (x, y, min(TILE, W - x), min(TILE, H - y))
        for y in range(0, H, TILE)
        for x in range(0, W, TILE)
    ]
    img = Image.new('RGB', (W, H))
    jobs = jobs or max(1, min(len(cores) + len(buildings), (os.cpu_count() or 2) - 2, 12))
    pool = None
    if jobs == 1:
        _init(ctx)
        results = map(paint_tile, cores)
    else:
        pool = ProcessPoolExecutor(jobs, initializer=_init, initargs=(ctx,))
        results = pool.map(paint_tile, cores)
    if len(cores) > 4:
        print(f'  painting {W}x{H} px in {len(cores)} tiles on {jobs} cores...', flush=True)
    step = max(1, len(cores) // 4)
    for n, ((x, y, _, _), tile) in enumerate(results, 1):
        img.paste(Image.fromarray(tile, 'RGB'), (x, y))
        if len(cores) > 4 and n % step == 0 and n < len(cores):
            print(f'  {100 * n // len(cores)}%', flush=True)
        if n % step == 0 or n == len(cores):
            print(f'PROGRESS {5 + 80 * n // len(cores)}% painting', flush=True)
    roofs = []
    if buildings:
        import roofs as roof_painter

        print(f'  roofing {len(buildings)} buildings...', flush=True)
        print('PROGRESS 87% roofing', flush=True)
        work = [(k, comp, meta['theme']) for k, comp in enumerate(buildings)]
        done = (
            pool.map(roof_painter.paint_roof, work) if pool else map(roof_painter.paint_roof, work)
        )
        folder = out_base + '.roofs'
        os.makedirs(folder, exist_ok=True)
        for f in os.listdir(folder):
            os.remove(os.path.join(folder, f))
        for k, x, y, rgba in done:
            Image.fromarray(rgba, 'RGBA').save(
                os.path.join(folder, f'{k}.webp'), 'WEBP', quality=85, method=4
            )
            roofs.append(
                dict(
                    file=f'{k}.webp',
                    x=x,
                    y=y,
                    width=int(rgba.shape[1]),
                    height=int(rgba.shape[0]),
                    rgba=rgba,
                )
            )
    if pool:
        pool.shutdown()
    if max(W, H) <= WEBP_MAX:
        path = out_base + '.webp'
        img.save(path, 'WEBP', quality=86, method=4)
    else:  # WebP stops at 16383 px a side
        path = out_base + '.jpg'
        img.save(path, 'JPEG', quality=90, optimize=True)
    check(img, segments, lights, cell, check_path)
    if roofs:  # how the players first see it: roofs on
        k = min(0.5, 4000 / img.width)
        view = img.resize((max(1, int(img.width * k)), max(1, int(img.height * k))), Image.LANCZOS)
        for r in roofs:
            tile = Image.fromarray(r['rgba'], 'RGBA').resize(
                (max(1, int(r['width'] * k)), max(1, int(r['height'] * k))), Image.LANCZOS
            )
            view.paste(tile, (int(r['x'] * k), int(r['y'] * k)), tile)
        view.save(out_base + '.roofs.jpg', 'JPEG', quality=88)
    for r in roofs:
        del r['rgba']
    return path, roofs


def check(img, segments, lights, cell, path):
    """The picture, scaled to at most 4000 px wide, with the Foundry walls and lights drawn on."""
    k = min(0.5, 4000 / img.width)
    im = img.resize((max(1, int(img.width * k)), max(1, int(img.height * k))), Image.LANCZOS)
    d = ImageDraw.Draw(im)
    colours = {
        'wall': (255, 40, 40),
        'stone wall': (255, 110, 40),
        'door': (40, 230, 90),
        'secret door': (200, 60, 255),
        'window': (40, 220, 255),
        'railing': (255, 170, 30),
        'hedge': (150, 255, 60),
    }
    lw = 3 if k >= 0.3 else 2  # (saved as JPEG: big maps make huge PNGs)
    for s in segments:
        (ax, ay), (bx, by) = s['pts']
        f = cell / 2 * k
        d.line((ax * f, ay * f, bx * f, by * f), fill=colours[s['kind']], width=lw)
        if k >= 0.3:
            for px, py in ((ax * f, ay * f), (bx * f, by * f)):
                d.ellipse((px - 3, py - 3, px + 3, py + 3), fill=colours[s['kind']])
    for l in lights:
        x, y = l['x'] * cell / 2 * k, l['y'] * cell / 2 * k
        for ft, width in ((l['bright'], 2), (l['dim'], 1)):
            r = ft / 5 * cell * k
            d.ellipse((x - r, y - r, x + r, y + r), outline=(255, 230, 60), width=width)
    im.save(path, 'JPEG', quality=88)
