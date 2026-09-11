"""Exact arithmetic over supplied cards, using Python's AST and Fraction."""
import ast
from collections import Counter
from fractions import Fraction
from functools import lru_cache


def calculate(expression, numbers, operations, fractional=True):
    if not isinstance(expression, str) or not expression.strip() or len(expression) > 256:
        raise ValueError("请输入不超过256字符的算式")
    expression = expression.translate(str.maketrans({"×": "*", "÷": "/", "（": "(", "）": ")"})).strip()
    try:
        tree = ast.parse(expression, mode="eval")
    except (SyntaxError, RecursionError) as error:
        raise ValueError("算式格式不正确，请使用数字、运算符和括号") from error
    leaves = []
    symbols = {ast.Add: "+", ast.Sub: "-", ast.Mult: "*", ast.Div: "/"}

    def visit(node):
        if isinstance(node, ast.Constant) and type(node.value) is int and 1 <= node.value <= 100:
            leaves.append(node.value)
            return Fraction(node.value)
        if not isinstance(node, ast.BinOp) or type(node.op) not in symbols or symbols[type(node.op)] not in operations:
            raise ValueError("算式包含未允许的操作；不能拼接数字、使用幂、函数或额外常数")
        left, right = visit(node.left), visit(node.right)
        try:
            result = apply(symbols[type(node.op)], left, right)
        except ZeroDivisionError as error:
            raise ValueError("不能除以0") from error
        if not fractional and result.denominator != 1:
            raise ValueError("本局规则不允许中间结果为分数")
        return result

    if sum(1 for _ in ast.walk(tree)) > 32:
        raise ValueError("算式过于复杂")
    result = visit(tree.body)
    if Counter(leaves) != Counter(numbers):
        raise ValueError("必须将题面上的每张牌恰好使用一次，不能漏用、重复或加入额外数字")
    return result


def apply(op, a, b):
    if op == "+": return a + b
    if op == "-": return a - b
    if op == "*": return a * b
    return a / b


@lru_cache(maxsize=4096)
def solve(numbers: tuple[int, ...], target=24, operations=("+", "-", "*", "/"), fractional=True):
    """Enumerate binary expression trees; never use floating-point tolerances."""
    def search(items):
        if len(items) == 1:
            return items[0][1] if items[0][0] == target else None
        seen = set()
        for i, (a, ea) in enumerate(items):
            for j, (b, eb) in enumerate(items):
                if i == j: continue
                rest = [item for k, item in enumerate(items) if k not in {i, j}]
                for op in operations:
                    if op in {"+", "*"} and i > j: continue
                    if op == "/" and b == 0: continue
                    value = apply(op, a, b)
                    if not fractional and value.denominator != 1: continue
                    key = (tuple(sorted(v for v, _ in rest)), value)
                    if key in seen: continue
                    seen.add(key)
                    answer = search(rest + [(value, f"({ea}{op}{eb})")])
                    if answer is not None: return answer
        return None

    return search([(Fraction(n), str(n)) for n in numbers])
