"""City generator for Map Forge.

Streets are cut by recursive splitting (wide main roads, narrower side streets, the odd through-ginnel); deep
blocks get a back alley or back gardens between two rows of terraced houses. Every lot becomes a house, shop,
tavern, temple, smithy or yard. Houses get rooms, inner doors, a front door onto the street, maybe a back
door onto the alley, windows only where there is outside to look at, and furniture that fits each room.
Optional: a canal with bridges, a city wall with towers and gates, a market square.

Returns the plan grid plus a DM key: every building and square numbered, with its rooms, for loot and events.
"""

import numpy as np

FLOOR_IN = ('-', '^')
OUTSIDE = (' ', ':', ',', ';', '`')  # street, alley, garden, sand, bridge
WALL = ('#', '$')

TRADES = [
    'baker',
    'cobbler',
    'chandler',
    'tailor',
    'apothecary',
    'cooper',
    'fletcher',
    'jeweller',
    'pawnbroker',
    'butcher',
    'candlemaker',
    'herbalist',
    'bookbinder',
    'locksmith',
    'weaver',
    'potter',
    'tanner',
    'cartographer',
]
SURNAMES = [
    'Ashdown',
    'Brackwater',
    'Cobb',
    'Dunmore',
    'Fenwick',
    'Grissom',
    'Hale',
    'Ironside',
    'Kettle',
    'Larkin',
    'Marlow',
    'Nettle',
    'Oakes',
    'Pike',
    'Quill',
    'Rook',
    'Sallow',
    'Thatcher',
    'Underhill',
    'Vane',
    'Wick',
    'Yarrow',
    'Brindle',
    'Crane',
    'Dunn',
    'Emberly',
    'Flint',
    'Gale',
    'Hobb',
    'Mossgrave',
]
TAVERN_A = [
    'Rusty',
    'Gilded',
    'Drowned',
    'Singing',
    'Crooked',
    'Frozen',
    'Laughing',
    'Burnt',
    'Silver',
    'Salty',
    'Sleeping',
    'Wandering',
]
TAVERN_B = [
    'Anchor',
    'Goat',
    'Lantern',
    'Kettle',
    'Crow',
    'Mermaid',
    'Barrel',
    'Stag',
    'Wheel',
    'Boot',
    'Axe',
    'Moon',
]


class Grid:
    def __init__(self, W, H, fill=' '):
        self.W, self.H = W, H
        self.g = [[fill] * W for _ in range(H)]

    def inb(self, i, j):
        return 0 <= i < self.H and 0 <= j < self.W

    def get(self, i, j, default='?'):
        return self.g[i][j] if self.inb(i, j) else default

    def set(self, i, j, ch):
        if self.inb(i, j):
            self.g[i][j] = ch

    def rect(self, i0, j0, i1, j1, ch):  # exclusive end
        for i in range(max(0, i0), min(self.H, i1)):
            for j in range(max(0, j0), min(self.W, j1)):
                self.g[i][j] = ch

    def text(self):
        return '\n'.join(''.join(r) for r in self.g)


class City:
    def __init__(self, W, H, seed, density=0.75, alleys=0.6, canal=False, wall=False, market=True):
        self.rng = np.random.default_rng(seed)
        self.G = Grid(W, H, ' ')
        self.W, self.H = W, H
        self.density, self.alleys, self.canal, self.wall, self.market = (
            density,
            alleys,
            canal,
            wall,
            market,
        )
        self.blocks, self.houses, self.areas, self.streets = [], [], [], []
        self.used_names = set()

    def r(self, a, b):  # inclusive random int
        return int(self.rng.integers(a, b + 1))

    def chance(self, p):
        return self.rng.random() < p

    def pick(self, seq):
        return seq[int(self.rng.integers(0, len(seq)))]

    # ---------- streets ----------
    def carve(self, vertical, pos, width, lo, hi, ch=' '):
        for k in range(width):
            for t in range(lo, hi):
                if vertical:
                    self.G.set(t, pos + k, ch)
                else:
                    self.G.set(pos + k, t, ch)
        self.streets.append((vertical, pos, width, lo, hi, ch))

    def split(self, i0, j0, i1, j1, depth, forced=None):
        h, w = i1 - i0, j1 - j0
        minb = 7 if self.density > 0.6 else 9
        if forced is None and (
            h < 2 * minb + 2
            and w < 2 * minb + 2
            or depth >= 3
            and h * w < (4 * minb) ** 2 / 3
            and self.chance(0.35)
        ):
            self.blocks.append((i0, j0, i1, j1))
            return
        vertical = w > h if abs(w - h) > 3 else self.chance(0.5)
        if forced:
            vertical, abs_pos, width = forced
            pos = abs_pos - (j0 if vertical else i0)
        else:
            width = (
                3
                if depth == 0
                else 2
                if depth <= 2
                else (1 if self.chance(self.alleys * 0.5) else 2)
            )
            size = w if vertical else h
            if size < 2 * minb + width:
                self.blocks.append((i0, j0, i1, j1))
                return
            pos = self.r(minb, size - minb - width)
        ch = ':' if width == 1 else ' '
        if vertical:
            self.carve(True, j0 + pos, width, i0, i1, ch)
            a, b = (i0, j0, i1, j0 + pos), (i0, j0 + pos + width, i1, j1)
        else:
            self.carve(False, i0 + pos, width, j0, j1, ch)
            a, b = (i0, j0, i0 + pos, j1), (i0 + pos + width, j0, i1, j1)
        self.split(*a, depth + 1)
        self.split(*b, depth + 1)

    def lay_streets(self):
        i0, j0, i1, j1 = self.inner
        if self.canal:
            vertical = (j1 - j0) > (i1 - i0)
            size = (j1 - j0) if vertical else (i1 - i0)
            pos = self.r(size // 3, size // 2)
            width = 4
            self.canal_line = (vertical, (j0 if vertical else i0) + pos, width)
            for k in range(width):
                for t in range((i0 if vertical else j0), (i1 if vertical else j1)):
                    self.G.set(
                        t, (j0 if vertical else i0) + pos + k, '~'
                    ) if vertical else self.G.set((i0 if vertical else i0) + pos + k, t, '~')
            # two streets cross the canal at the same place on both banks
            cross = [
                (
                    not vertical,
                    (i0 if vertical else j0)
                    + self.r(8, ((i1 - i0) if vertical else (j1 - j0)) // 2 - 4),
                    3,
                )
            ]
            cross.append((not vertical, cross[0][1] + self.r(16, 24), 2))
            halves = [
                (i0, j0, i1, j0 + pos) if vertical else (i0, j0, i0 + pos, j1),
                (i0, j0 + pos + width, i1, j1) if vertical else (i0 + pos + width, j0, i1, j1),
            ]
            for hv in halves:
                self.split_forced(hv, cross)
            self.bridges = cross
        else:
            self.bridges = []
            self.split(i0, j0, i1, j1, 0)

    def split_forced(self, rect, forced):
        """Split a canal bank at the given crossing lines first, so streets meet at the bridges."""
        rects = [rect]
        for vertical, pos, width in forced:
            nxt = []
            for a, b, c, d in rects:
                lo, hi = (b, d) if vertical else (a, c)
                if lo + 5 < pos < hi - width - 5:
                    self.carve(vertical, pos, width, a if vertical else b, c if vertical else d)
                    if vertical:
                        nxt += [(a, b, c, pos), (a, pos + width, c, d)]
                    else:
                        nxt += [(a, b, pos, d), (pos + width, b, c, d)]
                else:
                    nxt.append((a, b, c, d))
            rects = nxt
        for rct in rects:
            self.split(*rct, 1)

    def build_bridges(self):
        if not self.canal:
            return
        cv, cpos, cw = self.canal_line
        for vertical, pos, width in self.bridges:
            for k in range(width):
                for t in range(cpos, cpos + cw):
                    if vertical:
                        self.G.set(t, pos + k, '`')
                    else:
                        self.G.set(pos + k, t, '`')
            for t in range(cpos, cpos + cw):  # railings along both sides of the deck
                for side in (pos - 1, pos + width):
                    if vertical:
                        self.G.set(t, side, '=')
                    else:
                        self.G.set(side, t, '=')
            mid = (
                (cpos + cw // 2, pos + width // 2)
                if vertical
                else (pos + width // 2, cpos + cw // 2)
            )
            self.area('Bridge', 'bridge', mid, None)

    # ---------- walls and gates ----------
    def city_wall(self):
        m = 3
        G = self.G
        G.rect(0, 0, self.H, self.W, ',')
        G.rect(m, m, self.H - m, self.W - m, ' ')
        for i in range(m, self.H - m):
            G.set(i, m, '$')
            G.set(i, self.W - m - 1, '$')
        for j in range(m, self.W - m):
            G.set(m, j, '$')
            G.set(self.H - m - 1, j, '$')
        # a lane runs round the inside of the wall, so no house is built against it
        self.inner = (m + 2, m + 2, self.H - m - 2, self.W - m - 2)
        self.towers = [
            (m - 2, m - 2),
            (m - 2, self.W - m - 3),
            (self.H - m - 3, m - 2),
            (self.H - m - 3, self.W - m - 3),
        ]

    def gates_and_towers(self):
        m = 3
        G = self.G
        for i, j in self.towers:  # 5x5 towers straddling the corners
            G.rect(i, j, i + 5, j + 5, '$')
            G.rect(i + 1, j + 1, i + 4, j + 4, '^')
            G.set(i + 2, j + 2, 't')
            # the door faces into the city, clear of the wall line
            G.set(i + 4 if i < self.H // 2 else i, j + 3 if j < self.W // 2 else j + 1, '+')
            self.area('Wall tower', 'tower', (i + 2, j + 2), (i, j, i + 5, j + 5))
        i0, j0, i1, j1 = self.inner
        for vertical, pos, width, lo, hi, ch in self.streets:
            if width < 3:
                continue
            # where a main road reaches the edge of town, open the wall and carry the road on outside
            spots = (
                ([(m, -1)] if lo == i0 else []) + ([(self.H - m - 1, 1)] if hi == i1 else [])
                if vertical
                else ([(m, -1)] if lo == j0 else []) + ([(self.W - m - 1, 1)] if hi == j1 else [])
            )
            for line, outward in spots:
                for k in range(width):
                    for step in range(0, m + 1):
                        ii, jj = (
                            (line + outward * step, pos + k)
                            if vertical
                            else (pos + k, line + outward * step)
                        )
                        G.set(ii, jj, ' ' if step == 0 else ':')
                self.area(
                    'City gate',
                    'gate',
                    (line, pos + width // 2) if vertical else (pos + width // 2, line),
                    None,
                )
                for side in (-1, width):  # a lamp either side, just inside
                    li, lj = (
                        (line - outward, pos + side) if vertical else (pos + side, line - outward)
                    )
                    if G.get(li, lj) == ' ':
                        G.set(li, lj, 'l')
        # trees in the fields outside
        for _ in range(self.W * self.H // 40):
            i, j = self.r(0, self.H - 1), self.r(0, self.W - 1)
            if G.get(i, j) == ',' and all(
                G.get(i + a, j + b) in (',', '&', '%') for a in (-1, 0, 1) for b in (-1, 0, 1)
            ):
                G.set(i, j, self.pick(['&', '&', '%']))

    # ---------- blocks, lots, buildings ----------
    def market_square(self):
        if not self.market or not self.blocks:
            return
        ci, cj = self.H / 2, self.W / 2
        big = [b for b in self.blocks if (b[2] - b[0]) >= 9 and (b[3] - b[1]) >= 9]
        if not big:
            return
        b = min(big, key=lambda b: abs((b[0] + b[2]) / 2 - ci) + abs((b[1] + b[3]) / 2 - cj))
        self.blocks.remove(b)
        i0, j0, i1, j1 = b
        G = self.G
        G.rect(i0, j0, i1, j1, ' ')
        mi, mj = (i0 + i1) // 2, (j0 + j1) // 2
        if (i1 - i0) >= 11 and (j1 - j0) >= 11:
            G.rect(mi - 1, mj - 1, mi + 1, mj + 1, 'Q')
        else:
            G.set(mi, mj, 'O')
        for i in range(i0 + 2, i1 - 2, 3):  # rows of stalls with walkways
            for j in range(j0 + 2, j1 - 3, 4):
                if abs(i - mi) <= 2 and abs(j - mj) <= 3:
                    continue
                if self.chance(0.75):
                    G.set(i, j, 'Z')
                    G.set(i, j + 1, 'Z')
                    if self.chance(0.5):
                        G.set(i + 1, j + self.r(0, 1), self.pick(['x', 'b', 'q']))
        for i, j in ((i0, j0), (i0, j1 - 1), (i1 - 1, j0), (i1 - 1, j1 - 1)):
            G.set(i, j, 'l')
        self.area('Market square', 'market', (mi, mj), b)

    def lots_for_block(self, b):
        i0, j0, i1, j1 = b
        h, w = i1 - i0, j1 - j0
        G = self.G
        if min(h, w) < 5:  # a scrap of land: yard with a tree or a well
            G.rect(i0, j0, i1, j1, ',')
            G.set((i0 + i1) // 2, (j0 + j1) // 2, self.pick(['&', 'O', '%']))
            return []
        along_j = w >= h  # rows run along the long side
        depth = h if along_j else w
        rows = []  # (start, end inclusive, front side), in the depth direction
        if depth >= 11 and self.chance(0.4 + 0.5 * self.alleys):
            a = (depth - 1) // 2  # a one-wide back alley between two rows of houses
            self.back_alley(b, along_j, a)
            rows = [(0, a - 1, -1), (a + 1, depth - 1, +1)]
        elif depth >= 12:
            g = 2
            a = (depth - g) // 2
            rows = [(0, a - 1, -1), (a + g, depth - 1, +1)]
            self.back_gardens(b, along_j, a, g)
        elif depth >= 8:
            a = depth // 2
            rows = [(0, a, -1), (a, depth - 1, +1)]  # back to back, sharing the back wall
        else:
            rows = [(0, depth - 1, -1)]
        lots = []
        length = w if along_j else h
        for d0, d1, front in rows:
            k = 0
            while k < length - 1:
                lw = self.r(4, 7) if self.density > 0.6 else self.r(5, 9)
                end = min(k + lw, length - 1)
                if length - 1 - end < 4:
                    end = length - 1
                if along_j:
                    lots.append(
                        dict(
                            rect=(i0 + d0, j0 + k, i0 + d1, j0 + end),
                            front='N' if front < 0 else 'S',
                            axis='h',
                        )
                    )
                else:
                    lots.append(
                        dict(
                            rect=(i0 + k, j0 + d0, i0 + end, j0 + d1),
                            front='W' if front < 0 else 'E',
                            axis='v',
                        )
                    )
                k = end
        return lots

    def back_alley(self, b, along_j, a):
        i0, j0, i1, j1 = b
        if along_j:
            self.G.rect(i0 + a, j0, i0 + a + 1, j1, ':')
        else:
            self.G.rect(i0, j0 + a, i1, j0 + a + 1, ':')

    def back_gardens(self, b, along_j, a, g):
        i0, j0, i1, j1 = b
        if along_j:
            self.G.rect(i0 + a, j0, i0 + a + g, j1, ',')
        else:
            self.G.rect(i0, j0 + a, i1, j0 + a + g, ',')

    def assign_types(self, lots):
        n = len(lots)
        big = sorted(range(n), key=lambda k: -self.area_of(lots[k]['rect']))
        kinds = ['house'] * n
        want = [
            ('tavern', max(1, n // 45)),
            ('temple', 1 if n > 30 else 0),
            ('smithy', max(1, n // 60)),
            ('guardhouse', 1 if self.wall and n > 40 else 0),
            ('warehouse', max(0, n // 50) if self.canal else 0),
        ]
        taken = 0
        for kind, count in want:
            for _ in range(count):
                if taken < len(big):
                    kinds[big[taken]] = kind
                    taken += 1
        for k in range(n):
            if kinds[k] == 'house':
                if self.chance((1 - self.density) * 0.55):
                    kinds[k] = 'yard'
                elif self.chance(0.28):
                    kinds[k] = 'shop'
        return kinds

    @staticmethod
    def area_of(r):
        return (r[2] - r[0] + 1) * (r[3] - r[1] + 1)

    def outward(self, lot):
        return {'N': (-1, 0), 'S': (1, 0), 'W': (0, -1), 'E': (0, 1)}[lot['front']]

    def build(self, lot, kind):
        """Phase 1: walls and floors (doors and windows wait until every neighbour exists)."""
        i0, j0, i1, j1 = lot['rect']
        G = self.G
        if kind == 'yard':
            ground = self.pick([',', ',', ':'])
            for i in range(i0, i1 + 1):  # never over a neighbour's shared wall
                for j in range(j0, j1 + 1):
                    if G.get(i, j) not in WALL:
                        G.set(i, j, ground)
            for i in range(i0, i1 + 1):  # fences where it meets the street
                for j in range(j0, j1 + 1):
                    if (
                        (i in (i0, i1) or j in (j0, j1))
                        and G.get(i, j) not in WALL
                        and any(
                            G.get(i + a, j + b) == ' '
                            for a, b in ((0, 1), (1, 0), (0, -1), (-1, 0))
                        )
                    ):
                        G.set(i, j, '=')
            gi, gj = (i0 + i1) // 2, (j0 + j1) // 2
            fi, fj = self.outward(lot)
            gate = (i0 if fi < 0 else i1 if fi > 0 else gi, j0 if fj < 0 else j1 if fj > 0 else gj)
            G.set(*gate, ',')
            for _ in range(self.r(1, 3)):
                ti, tj = self.r(i0 + 1, max(i0 + 1, i1 - 1)), self.r(j0 + 1, max(j0 + 1, j1 - 1))
                G.set(
                    ti,
                    tj,
                    self.pick(
                        ['&', '%', 'y', 'y', 'O', 'x', 'b', 'g' if self.chance(0.1) else 'y']
                    ),
                )
            lot['yard'] = True
            return None
        # sometimes a small front yard, so the street frontage isn't a straight line
        di, dj = self.outward(lot)
        depth = (i1 - i0) if di else (j1 - j0)
        if kind == 'house' and depth >= 7 and self.chance((1 - self.density) * 0.8):
            s = self.r(1, 2)
            yard = (
                (i0, j0, i0 + s - 1, j1)
                if di < 0
                else (i1 - s + 1, j0, i1, j1)
                if di > 0
                else (i0, j0, i1, j0 + s - 1)
                if dj < 0
                else (i0, j1 - s + 1, i1, j1)
            )
            for i in range(yard[0], yard[2] + 1):
                for j in range(yard[1], yard[3] + 1):
                    if G.get(i, j) not in WALL:
                        G.set(i, j, ',')
            if di < 0:
                i0 += s
            elif di > 0:
                i1 -= s
            elif dj < 0:
                j0 += s
            else:
                j1 -= s
            for a, b in [(yard[0], yard[1]), (yard[2], yard[3])]:
                G.set(a, b, self.pick(['%', 'y', '&']))
        wall = '$' if kind in ('temple', 'guardhouse') else '#'
        floor = (
            '^'
            if kind in ('temple', 'smithy', 'guardhouse', 'warehouse') or self.chance(0.2)
            else '-'
        )
        for i in range(i0, i1 + 1):
            for j in range(j0, j1 + 1):
                edge = i in (i0, i1) or j in (j0, j1)
                if edge:
                    if G.get(i, j) not in WALL:
                        G.set(i, j, wall)
                else:
                    G.set(i, j, floor)
        house = dict(
            kind=kind,
            rect=(i0, j0, i1, j1),
            front=lot['front'],
            floor=floor,
            rooms=[],
            inner_doors=[],
        )
        self.rooms(house)
        return house

    def rooms(self, house):
        i0, j0, i1, j1 = house['rect']
        G = self.G
        interior = (i0 + 1, j0 + 1, i1 - 1, j1 - 1)
        if house['kind'] in ('temple', 'warehouse'):
            house['rooms'] = [interior]
            return
        todo, done = [interior], []
        limit = {'house': 3, 'shop': 3, 'tavern': 3, 'smithy': 2, 'guardhouse': 3}[house['kind']]
        while todo:
            r = todo.pop()
            a, b, c, d = r
            h, w = c - a + 1, d - b + 1
            if (
                len(done) + len(todo) + 1 >= limit + 1
                or max(h, w) < 6
                or (house['kind'] == 'tavern' and not done and h * w < 60)
            ):
                done.append(r)
                continue
            if w >= h:
                cut = (
                    self.r(b + 2, d - 2)
                    if house['kind'] != 'tavern'
                    else self.r(b + (w * 3) // 5, d - 2)
                )
                for i in range(a, c + 1):
                    G.set(i, cut, '#')
                house['inner_doors'].append(('v', cut, a, c))
                todo += [(a, b, c, cut - 1), (a, cut + 1, c, d)]
            else:
                cut = (
                    self.r(a + 2, c - 2)
                    if house['kind'] != 'tavern'
                    else self.r(a + (h * 3) // 5, c - 2)
                )
                for j in range(b, d + 1):
                    G.set(cut, j, '#')
                house['inner_doors'].append(('h', cut, b, d))
                todo += [(a, b, cut - 1, d), (cut + 1, b, c, d)]
        house['rooms'] = done

    # ---------- phase 2: doors and windows ----------
    def is_out(self, i, j):
        return self.G.get(i, j, ' ') in OUTSIDE or self.G.get(i, j) in (
            '&',
            '%',
            'y',
            'O',
            'l',
            'Z',
            'x',
            'b',
            'q',
            'k',
            'j',
            'g',
            '=',
        )

    def wall_cells(self, house):
        """(i, j, outward) for every straight stretch of outer wall between a room and the outside."""
        i0, j0, i1, j1 = house['rect']
        out = []
        G = self.G
        for j in range(j0 + 1, j1):
            out += [(i0, j, (-1, 0)), (i1, j, (1, 0))]
        for i in range(i0 + 1, i1):
            out += [(i, j0, (0, -1)), (i, j1, (0, 1))]
        ok = []
        for i, j, (di, dj) in out:
            if G.get(i, j) not in WALL:
                continue
            inside = G.get(i - di, j - dj)
            along = [G.get(i + dj, j + di), G.get(i - dj, j - di)]
            if (
                inside in FLOOR_IN
                and all(a in WALL + ('W',) for a in along)
                and self.is_out(i + di, j + dj)
            ):
                ok.append((i, j, (di, dj)))
        return ok

    def openings(self, house):
        G = self.G
        cands = self.wall_cells(house)
        fi, fj = self.outward(house)
        front = [c for c in cands if c[2] == (fi, fj)]
        street = [c for c in front if G.get(c[0] + fi, c[1] + fj) == ' '] or front or cands
        doors = []
        if street:
            c = street[int(self.rng.integers(0, len(street)))]
            G.set(c[0], c[1], '+')
            doors.append(c)
            if house['kind'] in ('tavern', 'smithy', 'warehouse', 'temple') and len(street) > 3:
                c2 = next(
                    (
                        x
                        for x in street
                        if abs(x[0] - c[0]) + abs(x[1] - c[1]) == 1 and G.get(x[0], x[1]) != '+'
                    ),
                    None,
                )
                if c2:
                    G.set(c2[0], c2[1], '+')  # double doors
        back = [
            c for c in cands if c[2] == (-fi, -fj) and G.get(c[0] - fi, c[1] - fj) in (':', ',')
        ]
        if back and self.chance(0.65):
            c = back[int(self.rng.integers(0, len(back)))]
            G.set(c[0], c[1], '+')
            doors.append(c)
        for kind, pos, lo, hi in house['inner_doors']:
            spots = []
            for t in range(lo, hi + 1):
                i, j = (t, pos) if kind == 'v' else (pos, t)
                n1, n2 = (
                    (G.get(i, j - 1), G.get(i, j + 1))
                    if kind == 'v'
                    else (G.get(i - 1, j), G.get(i + 1, j))
                )
                a1, a2 = (
                    (G.get(i - 1, j), G.get(i + 1, j))
                    if kind == 'v'
                    else (G.get(i, j - 1), G.get(i, j + 1))
                )
                if (
                    n1 in FLOOR_IN
                    and n2 in FLOOR_IN
                    and a1 in WALL + ('+',)
                    and a2 in WALL + ('+',)
                ):
                    spots.append((i, j))
            if spots:
                G.set(*spots[int(self.rng.integers(0, len(spots)))], '+')
        last = None
        for i, j, d in cands:
            if G.get(i, j) != '#' and G.get(i, j) != '$':
                continue
            near_door = any(
                G.get(i + a, j + b) == '+' for a, b in ((0, 1), (1, 0), (0, -1), (-1, 0))
            )
            if near_door or (last and abs(last[0] - i) + abs(last[1] - j) < 3):
                continue
            if self.chance(0.4 if house['kind'] != 'warehouse' else 0.1):
                G.set(i, j, 'W')
                last = (i, j)

    # ---------- phase 3: furniture ----------
    def free(self, i, j):
        G = self.G
        return G.get(i, j) in FLOOR_IN and not any(
            G.get(i + a, j + b) == '+'
            for a, b in ((0, 1), (1, 0), (0, -1), (-1, 0), (1, 1), (1, -1), (-1, 1), (-1, -1))
        )

    def against_wall(self, room):
        a, b, c, d = room
        G = self.G
        spots = []
        for i in range(a, c + 1):
            for j in range(b, d + 1):
                if self.free(i, j) and any(
                    G.get(i + x, j + y) in WALL for x, y in ((0, 1), (1, 0), (0, -1), (-1, 0))
                ):
                    spots.append((i, j))
        self.rng.shuffle(spots)
        return spots

    def place_pair(self, room, ch, n=2):
        """n cells in a line along a wall (beds, shelves, counters)."""
        G = self.G
        for i, j in self.against_wall(room):
            for di, dj in ((0, 1), (1, 0)):
                cells = [(i + di * k, j + dj * k) for k in range(n)]
                wall_side = [
                    (x, y)
                    for x, y in ((-dj, -di), (dj, di))
                    if all(G.get(p + x, q + y) in WALL for p, q in cells)
                ]
                if all(self.free(p, q) for p, q in cells) and wall_side:
                    for p, q in cells:
                        G.set(p, q, ch)
                    return True
        return False

    def place_one(self, room, ch, wall=True):
        spots = self.against_wall(room) if wall else self.centre_spots(room)
        if spots:
            self.G.set(*spots[0], ch)
            return spots[0]
        return None

    def centre_spots(self, room):
        a, b, c, d = room
        G = self.G
        spots = [
            (i, j)
            for i in range(a + 1, c)
            for j in range(b + 1, d)
            if self.free(i, j)
            and not any(G.get(i + x, j + y) in WALL for x, y in ((0, 1), (1, 0), (0, -1), (-1, 0)))
        ]
        self.rng.shuffle(spots)
        return spots

    def table_with_chairs(self, room, size=2):
        G = self.G
        for i, j in self.centre_spots(room):
            cells = [(i, j + k) for k in range(size)]
            if all(
                self.free(p, q)
                and not any(
                    G.get(p + x, q + y) in WALL for x, y in ((0, 1), (1, 0), (0, -1), (-1, 0))
                )
                for p, q in cells
            ):
                for p, q in cells:
                    G.set(p, q, 'T')
                for p, q in cells:
                    for x in (-1, 1):
                        if self.free(p + x, q) and self.chance(0.8):
                            G.set(p + x, q, 'c')
                return True
        return False

    def furnish(self, house):
        rooms = sorted(house['rooms'], key=lambda r: -(r[2] - r[0] + 1) * (r[3] - r[1] + 1))
        kind = house['kind']
        names = []
        if kind == 'temple':
            a, b, c, d = rooms[0]
            for i in range(a + 1, c, 2) if (c - a) > (d - b) else []:
                for j in (b + 1, d - 1):
                    if self.free(i, j):
                        self.G.set(i, j, 'P')
            for j in range(b + 1, d, 2) if (c - a) <= (d - b) else []:
                for i in (a + 1, c - 1):
                    if self.free(i, j):
                        self.G.set(i, j, 'P')
            self.place_pair(rooms[0], 'A', 2)
            for _ in range(3):
                self.place_one(rooms[0], 'i', wall=False)
            for _ in range(4):  # pews
                spot = self.centre_spots(rooms[0])
                if spot and self.free(spot[0][0], spot[0][1] + 1):
                    i, j = spot[0]
                    self.G.set(i, j, 'j')
                    self.G.set(i, j + 1, 'j')
            return ['nave']
        for n, room in enumerate(rooms):
            area = (room[2] - room[0] + 1) * (room[3] - room[1] + 1)
            if kind == 'tavern' and n == 0:
                for _ in range(max(2, area // 12)):
                    self.table_with_chairs(room, 2 if self.chance(0.6) else 1)
                self.place_pair(room, 'u', 3) or self.place_pair(room, 'u', 2)
                self.place_pair(room, 'f', 2) or self.place_one(room, 'f')
                for _ in range(2):
                    self.place_one(room, 'b')
                self.place_one(room, 't', wall=False)
                names.append('common room')
            elif kind == 'shop' and n == 0:
                self.place_pair(room, 'u', 2)
                for _ in range(2):
                    self.place_pair(room, 's', 2) or self.place_one(room, 's')
                self.place_one(room, self.pick(['x', 'b', 'q', 'C']))
                self.place_one(room, 't', wall=False)
                names.append('shop floor')
            elif kind == 'smithy' and n == 0:
                self.place_pair(room, 'f', 2) or self.place_one(room, 'f')
                self.place_one(room, 'n', wall=False)
                for ch in ('x', 'b', 'x'):
                    self.place_one(room, ch)
                names.append('forge')
            elif kind == 'warehouse':
                for _ in range(area // 4):
                    self.place_one(room, self.pick(['x', 'x', 'b', 'q']), wall=self.chance(0.5))
                names.append('storehouse')
            elif kind == 'guardhouse' and n == 0:
                self.table_with_chairs(room, 2)
                self.place_one(room, 'C')
                self.place_one(room, 't', wall=False)
                names.append('guard room')
            elif n == 0 or (area >= 12 and n == 1 and kind != 'house'):
                self.place_pair(room, 'f', 1 if area < 16 else 2) or self.place_one(room, 'f')
                self.table_with_chairs(room, 2 if area >= 16 else 1)
                self.place_one(room, self.pick(['s', 'b', 'x']))
                names.append('hall' if kind == 'house' else 'back room')
            elif area <= 9 and self.chance(0.4):
                for _ in range(max(1, area // 3)):
                    self.place_one(room, self.pick(['x', 'b', 'q']))
                names.append('store room')
            else:
                self.place_pair(room, 'B', 2) or self.place_one(room, 'B')
                if self.chance(0.7):
                    self.place_one(room, 'C')
                if self.chance(0.4) and area >= 9:
                    self.place_pair(room, 'B', 2)
                names.append('bedroom')
        return names

    # ---------- street furniture ----------
    def street_life(self):
        G = self.G
        for vertical, pos, width, lo, hi, ch in self.streets:
            if width < 2:
                continue
            for t in range(lo + self.r(1, 4), hi, self.r(7, 10)):
                for side in (pos - 1, pos + width):
                    # a lamp on the street edge, against a wall
                    edge = (
                        (t, pos if side == pos - 1 else pos + width - 1)
                        if vertical
                        else (pos if side == pos - 1 else pos + width - 1, t)
                    )
                    beyond = (t, side) if vertical else (side, t)
                    if G.get(*edge) == ' ' and G.get(*beyond) in WALL and self.chance(0.6):
                        G.set(*edge, 'l')
                        break
            if width >= 3 and self.chance(0.5):
                t = self.r(lo + 2, max(lo + 2, hi - 3))
                a, b = (
                    ((t, pos + width - 1), (t + 1, pos + width - 1))
                    if vertical
                    else ((pos + width - 1, t), (pos + width - 1, t + 1))
                )
                if G.get(*a) == ' ' and G.get(*b) == ' ':
                    G.set(*a, 'k')
                    G.set(*b, 'k')
        for h in self.houses:  # crates and barrels outside shops, not in the doorway
            if h['kind'] not in ('shop', 'tavern', 'warehouse', 'smithy'):
                continue
            i0, j0, i1, j1 = h['rect']
            fi, fj = self.outward(h)
            line = (
                [(i0 - 1, j) for j in range(j0, j1 + 1)]
                if fi < 0
                else [(i1 + 1, j) for j in range(j0, j1 + 1)]
                if fi > 0
                else [(i, j0 - 1) for i in range(i0, i1 + 1)]
                if fj < 0
                else [(i, j1 + 1) for i in range(i0, i1 + 1)]
            )
            for i, j in line:
                if (
                    G.get(i, j) == ' '
                    and not any(
                        G.get(i + a, j + b) == '+' for a, b in ((0, 1), (1, 0), (0, -1), (-1, 0))
                    )
                    and self.chance(0.25)
                ):
                    G.set(i, j, self.pick(['b', 'x', 'q']))

    # ---------- the DM key ----------
    def name_for(self, kind):
        for _ in range(20):
            if kind == 'tavern':
                n = f'The {self.pick(TAVERN_A)} {self.pick(TAVERN_B)}'
            elif kind == 'shop':
                n = f'{self.pick(SURNAMES)} the {self.pick(TRADES)}'
            elif kind == 'house':
                n = f'{self.pick(SURNAMES)} house'
            elif kind == 'temple':
                n = self.pick(
                    [
                        'Temple of the Dawn',
                        'Shrine of Auril',
                        'Chapel of Gaia',
                        'Temple of the Timekeeper',
                    ]
                )
            elif kind == 'smithy':
                n = f"{self.pick(SURNAMES)}'s smithy"
            elif kind == 'guardhouse':
                n = 'Watch house'
            elif kind == 'warehouse':
                n = f'{self.pick(SURNAMES)} warehouse'
            else:
                n = kind.title()
            if n not in self.used_names:
                self.used_names.add(n)
                return n
        return n

    def area(self, name, kind, at, rect, rooms=None):
        self.areas.append(
            dict(
                n=len(self.areas) + 1,
                name=name,
                kind=kind,
                at=[int(at[0]), int(at[1])],
                rect=[int(x) for x in rect] if rect else None,
                rooms=rooms or [],
                text='',
                loot=[],
                events=[],
                creatures='',
            )
        )

    def generate(self):
        if self.wall:
            self.city_wall()
        else:
            self.inner = (0, 0, self.H, self.W)
        self.lay_streets()
        self.market_square()
        lots = []
        for b in self.blocks:
            lots += self.lots_for_block(b)
        kinds = self.assign_types(lots)
        for lot, kind in zip(lots, kinds):
            house = self.build(lot, kind)
            if house:
                self.houses.append(house)
        self.build_bridges()
        for h in self.houses:
            self.openings(h)
        for h in self.houses:
            rooms = self.furnish(h)
            i0, j0, i1, j1 = h['rect']
            self.area(
                self.name_for(h['kind']),
                h['kind'],
                ((i0 + i1) // 2, (j0 + j1) // 2),
                h['rect'],
                rooms,
            )
        self.street_life()
        if self.wall:
            self.gates_and_towers()
        return self.G.text(), self.areas


def generate(W, H, seed, density=0.75, alleys=0.6, canal=False, wall=False, market=True, **_):
    return City(W, H, seed, density, alleys, canal, wall, market).generate()
