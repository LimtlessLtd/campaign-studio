"""Roofs for Map Forge: one image per building, placed in Foundry as an overhead tile that fades away when a
token walks inside (so the base map shows interiors, and the street view shows roofs).

Rectangular buildings get a gable roof along their long side; any other shape gets a hip roof worked out from
the distance to its edges. Materials (slate, clay tile, thatch, shingle) and chimneys (over fireplaces) vary per
building but are fixed by the plan's seed."""

import math

import numpy as np
from PIL import Image, ImageFilter
from scipy import ndimage

import render2d
from forge import WALLS
from render2d import hash2, vnoise

MATERIALS = {  # base colour, tiled?
    'slate': ((92, 98, 110), True),
    'clay': ((156, 80, 54), True),
    'thatch': ((170, 140, 82), False),
    'shingle': ((112, 84, 60), True),
}
MIX = {
    'city': ['slate', 'clay', 'clay', 'shingle', 'thatch'],
    'outdoor': ['thatch', 'thatch', 'shingle', 'clay', 'slate'],
}


def half_mask(cells, comp):
    """The building in half-cells: its rooms plus the inner half of the walls around them."""
    inside = set(comp)
    i0, j0 = min(i for i, _ in comp) - 1, min(j for _, j in comp) - 1
    i1, j1 = max(i for i, _ in comp) + 2, max(j for _, j in comp) + 2
    R = np.zeros((2 * (i1 - i0), 2 * (j1 - j0)), bool)
    for i, j in comp:
        R[2 * (i - i0) : 2 * (i - i0) + 2, 2 * (j - j0) : 2 * (j - j0) + 2] = True
    H, W = len(cells), len(cells[0])
    for i in range(max(0, i0), min(H, i1)):
        for j in range(max(0, j0), min(W, j1)):
            if cells[i][j] not in WALLS:
                continue
            for qi in (0, 1):
                for qj in (0, 1):
                    dy, dx = qi * 2 - 1, qj * 2 - 1
                    if (i + dy, j) in inside or (i, j + dx) in inside or (i + dy, j + dx) in inside:
                        R[2 * (i - i0) + qi, 2 * (j - j0) + qj] = True
    return R, i0, j0


def paint_roof(job):
    k, comp, theme = job
    ctx = render2d.CTX
    cell, cells, seed = ctx['cell'], ctx['cells'], ctx['seed']
    half = cell // 2
    rng = np.random.default_rng([seed & 0xFFFF, k])
    R, i0, j0 = half_mask(cells, comp)
    rect = R.sum() >= 0.98 * R[np.ix_(R.any(1), R.any(0))].size
    mask = np.repeat(np.repeat(R, half, 0), half, 1)
    pad = int(cell * 0.5)
    mask = np.pad(mask, pad)
    ox, oy = j0 * cell - pad, i0 * cell - pad  # map pixel of mask[0, 0]
    roof = ndimage.distance_transform_edt(~mask) <= cell * 0.2  # eaves overhang the walls
    h, w = roof.shape
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    ys, xs = np.where(roof)
    ry0, ry1, rx0, rx1 = ys.min(), ys.max() + 1, xs.min(), xs.max() + 1

    material = MIX.get(theme, MIX['city'])[int(rng.integers(0, len(MIX.get(theme, MIX['city']))))]
    col, tiled = MATERIALS[material]
    col = np.array(col, np.float32) * rng.uniform(0.9, 1.08)
    ch, tw = cell * (0.13 if tiled else 0.2), cell * 0.18  # course height, tile width
    edge = ndimage.distance_transform_edt(roof)  # distance to the eaves

    if rect:  # gable: ridge along the long side
        along_x = (rx1 - rx0) >= (ry1 - ry0)
        if along_x:
            ridge = (ry0 + ry1) / 2
            dist, lit = np.abs(yy - ridge), yy < ridge  # the north slope faces the light (top left)
            run = xx
        else:
            ridge = (rx0 + rx1) / 2
            dist, lit = np.abs(xx - ridge), xx < ridge  # the west slope faces the light
            run = yy
        light = np.where(lit, 1.06, 0.78)
    else:  # hip: every point slopes down to its nearest edge
        gy, gx = np.gradient(edge)
        n = np.hypot(gx, gy) + 1e-6
        facing = (-gx - gy) / (n * math.sqrt(2))  # outward normal . towards the top-left light
        light = 0.9 + 0.2 * facing
        dist = edge.max() - edge
        run = np.where(np.abs(gx) > np.abs(gy), yy, xx)
    course = np.floor(dist / ch)
    out = np.broadcast_to(col, (h, w, 3)).copy() * light[..., None]
    if tiled:
        shift = (course % 2) * tw / 2
        tile = np.floor((run + shift) / tw)
        out *= (0.9 + 0.18 * hash2(tile.astype(np.int64), course.astype(np.int64), seed + k))[
            ..., None
        ]
        joint = ((run + shift) % tw) < 2.5
        lip = (dist % ch) > ch - 3.5  # the lower edge of each course casts a line
        out[joint | lip] *= 0.62
    else:  # thatch: straw running down the slope, soft bands
        streak_along_y = not rect or (rect and along_x)
        fib = (
            vnoise((0, 0, w, h), 2.5, cell * 0.35, seed + k)
            if streak_along_y
            else vnoise((0, 0, w, h), cell * 0.35, 2.5, seed + k)
        )
        out *= (0.8 + 0.35 * fib)[..., None]
        out *= np.where((dist % ch) > ch * 0.8, 0.85, 1.0)[..., None]
    out *= (0.9 + 0.2 * vnoise((0, 0, w, h), cell * 0.8, cell * 0.8, seed + 7 * k))[..., None]
    if rect:
        out[dist < cell * 0.05] *= 0.8  # ridge cap
    out[edge < cell * 0.05] *= 0.72  # darker eaves

    img = Image.fromarray(np.clip(out, 0, 255).astype(np.uint8), 'RGB')
    # chimneys over fireplaces
    from PIL import ImageDraw

    d = ImageDraw.Draw(img)
    for i, j in comp:
        if (
            cells[i][j] == 'fireplace'
            and (i, j - 1) not in comp
            or cells[i][j] == 'fireplace'
            and cells[i][j - 1] != 'fireplace'
        ):
            if cells[i][j] != 'fireplace':
                continue
            cx, cy = (j - j0) * cell + half + pad, (i - i0) * cell + half + pad
            s = cell * 0.2
            d.rectangle(
                (
                    cx - s + cell * 0.06,
                    cy - s + cell * 0.08,
                    cx + s + cell * 0.06,
                    cy + s + cell * 0.08,
                ),
                fill=(0, 0, 0),
            )
            d.rectangle(
                (cx - s, cy - s, cx + s, cy + s), fill=(112, 104, 96), outline=(60, 56, 52), width=3
            )
            d.rectangle((cx - s * 0.5, cy - s * 0.5, cx + s * 0.5, cy + s * 0.5), fill=(22, 20, 18))
    # the roof's shadow on the street, part of the same tile so it fades with it
    shadow = Image.fromarray((roof * 255).astype(np.uint8), 'L').filter(
        ImageFilter.GaussianBlur(cell * 0.08)
    )
    sh = Image.new('L', (w, h), 0)
    sh.paste(shadow, (int(cell * 0.08), int(cell * 0.1)))
    alpha = np.where(roof, 255, (np.asarray(sh, np.float32) * 0.5)).astype(np.uint8)
    rgba = np.dstack([np.asarray(img), alpha])
    rgba[~roof, :3] = 0
    ys, xs = np.where(alpha > 3)
    y0, y1, x0, x1 = ys.min(), ys.max() + 1, xs.min(), xs.max() + 1
    return k, int(ox + x0), int(oy + y0), rgba[y0:y1, x0:x1]
