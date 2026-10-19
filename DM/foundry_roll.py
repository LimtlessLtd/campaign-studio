"""Parse and roll Foundry dice formulas without Foundry, for the encounter smoke test.

Covers what D&D 5e items use: numbers, + - * /, parentheses, dice with keep/drop and min/max
modifiers, a calculated dice count such as `(floor(@item.level / 2))d8`, Math functions, flavor text in
brackets and `@` references resolved by the caller. Rerolls, explosions and success counts roll as
plain dice. Nothing is evaluated as code.
"""

import math
import re

MAX_LENGTH = 1000
MAX_DICE = 1000
MAX_SIDES = 10000
FUNCTIONS = {
    'abs': (1, abs),
    'ceil': (1, math.ceil),
    'floor': (1, math.floor),
    'round': (1, lambda value: math.floor(value + 0.5)),
    'trunc': (1, math.trunc),
    'sign': (1, lambda value: (value > 0) - (value < 0)),
    'sqrt': (1, math.sqrt),
    'max': (None, max),
    'min': (None, min),
}
REFERENCE = re.compile(r'@([A-Za-z0-9_.\-]+)')
NUMBER = re.compile(r'\d+(?:\.\d+)?|\.\d+')
NAME = re.compile(r'[A-Za-z_]\w*')
MODIFIER = re.compile(r'(kh|kl|k|dh|dl|rr|r|xo|x|min|max|cs|cf|sf|ms|df)([<>=]{0,2})(\d*)')


class FormulaError(ValueError):
    pass


def compile_formula(formula, resolve):
    """Parse a formula once: (tree, unknown references). Raises FormulaError.

    `resolve(path)` returns a number (or a formula string) for `@path`, or None when only the game
    system knows it at run time; such a reference counts as 0 and is listed.
    """
    text = str(formula)
    if '[object' in text:
        raise FormulaError('formula is "[object Object]"')
    if len(text) > MAX_LENGTH:
        raise FormulaError(f'cannot parse "{text[:60]}…"')
    unknown = []

    def substitute(match):
        value = resolve(match.group(1))
        if value is None:
            unknown.append(match.group(1))
            return '0'
        return f'({value})'

    text = REFERENCE.sub(substitute, text)
    text = re.sub(r'\[[^\]]*\]', '', text)  # flavor text, such as 2d6[fire]
    parser = _Parser(text)
    try:
        tree = parser.expression()
        parser.space()
        if parser.at != len(text):
            raise FormulaError('trailing text')
    except (FormulaError, IndexError, RecursionError) as error:
        raise FormulaError(f'cannot parse "{formula}"') from error
    return tree, unknown


def roll(tree, rng):
    """One roll of a compiled formula. Raises FormulaError when it is not a finite number."""
    try:
        value = _evaluate(tree, rng)
    except (ArithmeticError, ValueError, TypeError, RecursionError) as error:
        raise FormulaError('cannot evaluate') from error
    if not math.isfinite(value):
        raise FormulaError('is not a number')
    return value


def average(formula, resolve, rolls, rng):
    """The mean of `rolls` rolls: (average, unknown references). Raises FormulaError."""
    if formula is None or formula == '':
        return 0.0, []
    tree, unknown = compile_formula(formula, resolve)
    try:
        total = sum(roll(tree, rng) for _ in range(rolls))
    except FormulaError as error:
        if str(error) == 'is not a number':
            raise FormulaError(f'"{formula}" is not a number') from error
        raise FormulaError(f'cannot evaluate "{formula}"') from error
    return total / rolls, unknown


class _Parser:
    def __init__(self, text):
        self.text = text
        self.at = 0

    def space(self):
        while self.at < len(self.text) and self.text[self.at].isspace():
            self.at += 1

    def peek(self):
        self.space()
        return self.text[self.at] if self.at < len(self.text) else ''

    def expression(self):
        tree = self.term()
        while self.peek() in ('+', '-'):
            operator = self.text[self.at]
            self.at += 1
            tree = ('binary', operator, tree, self.term())
        return tree

    def term(self):
        tree = self.unary()
        while self.peek() in ('*', '/', '%'):
            operator = self.text[self.at]
            self.at += 1
            tree = ('binary', operator, tree, self.unary())
        return tree

    def unary(self):
        if self.peek() == '-':
            self.at += 1
            return ('negate', self.unary())
        if self.peek() == '+':
            self.at += 1
            return self.unary()
        return self.dice()

    def _dice_follows(self):
        """Whether a die (`d6`, `d%`, `d(…)`) starts here, with no space before it."""
        return (
            self.at + 1 < len(self.text)
            and self.text[self.at] in 'dD'
            and (self.text[self.at + 1].isdigit() or self.text[self.at + 1] in '%(')
        )

    def dice(self):
        self.space()
        count = ('number', 1.0) if self._dice_follows() else self.atom()
        while self._dice_follows():
            self.at += 1
            if self.text[self.at] == '%':
                self.at += 1
                sides = ('number', 100.0)
            elif self.text[self.at] == '(':
                sides = self.atom()
            else:
                found = NUMBER.match(self.text, self.at)
                self.at = found.end()
                sides = ('number', float(found.group()))
            modifiers = []
            while found := MODIFIER.match(self.text, self.at):
                modifiers.append((found.group(1), found.group(2), found.group(3)))
                self.at = found.end()
            count = ('dice', count, sides, tuple(modifiers))
        return count

    def atom(self):
        char = self.peek()
        if char == '(':
            self.at += 1
            tree = self.expression()
            if self.peek() != ')':
                raise FormulaError('unclosed parenthesis')
            self.at += 1
            return tree
        if found := NUMBER.match(self.text, self.at):
            self.at = found.end()
            return ('number', float(found.group()))
        found = NAME.match(self.text, self.at)
        if found and found.group().lower() in FUNCTIONS:
            self.at = found.end()
            if self.peek() != '(':
                raise FormulaError('function without arguments')
            self.at += 1
            arguments = [self.expression()]
            while self.peek() == ',':
                self.at += 1
                arguments.append(self.expression())
            if self.peek() != ')':
                raise FormulaError('unclosed function')
            self.at += 1
            expected = FUNCTIONS[found.group().lower()][0]
            if expected is not None and len(arguments) != expected:
                raise FormulaError('wrong number of arguments')
            return ('call', found.group().lower(), tuple(arguments))
        raise FormulaError('unexpected text')


def _evaluate(tree, rng):
    kind = tree[0]
    if kind == 'number':
        return tree[1]
    if kind == 'negate':
        return -_evaluate(tree[1], rng)
    if kind == 'call':
        return float(FUNCTIONS[tree[1]][1](*(_evaluate(item, rng) for item in tree[2])))
    if kind == 'binary':
        left, right = _evaluate(tree[2], rng), _evaluate(tree[3], rng)
        if tree[1] == '+':
            return left + right
        if tree[1] == '-':
            return left - right
        if tree[1] == '*':
            return left * right
        if tree[1] == '/':
            return left / right
        return math.fmod(left, right)
    count = math.floor(_evaluate(tree[1], rng))
    sides = math.floor(_evaluate(tree[2], rng))
    if not 0 <= count <= MAX_DICE or not 1 <= sides <= MAX_SIDES:
        raise ValueError('dice out of range')
    rolls = sorted(rng.randint(1, sides) for _ in range(count))
    for name, _, amount in tree[3]:
        number = int(amount) if amount else 1
        if name in ('kh', 'k'):
            rolls = rolls[-number:] if number else []
        elif name == 'kl':
            rolls = rolls[:number]
        elif name == 'dh':
            rolls = rolls[: len(rolls) - number] if number <= len(rolls) else []
        elif name == 'dl':
            rolls = rolls[number:]
        elif name == 'min' and amount:
            rolls = [max(value, number) for value in rolls]
        elif name == 'max' and amount:
            rolls = [min(value, number) for value in rolls]
    return float(sum(rolls))
