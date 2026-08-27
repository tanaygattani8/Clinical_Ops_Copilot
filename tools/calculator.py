import ast
import operator

# Safe binary operators only — no eval()
_OPS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.Pow: operator.pow,
    ast.USub: operator.neg,
}


# ** grows fast enough to hang the process before any error can be raised
# (9**9**9 never returns), and this runs in-process inside the web app. Clinic
# arithmetic never needs a large exponent.
_MAX_EXPONENT = 64


def _safe_eval(node):
    if isinstance(node, ast.Constant):
        return node.value
    if isinstance(node, ast.BinOp):
        op = type(node.op)
        if op not in _OPS:
            raise ValueError(f"Unsupported operator: {op}")
        left, right = _safe_eval(node.left), _safe_eval(node.right)
        if op is ast.Pow and abs(right) > _MAX_EXPONENT:
            raise ValueError(f"Exponent {right} exceeds the maximum of {_MAX_EXPONENT}")
        return _OPS[op](left, right)
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub):
        return -_safe_eval(node.operand)
    raise ValueError(f"Unsupported expression type: {type(node)}")


def calculate(expression: str, context: str) -> dict:
    """Safely evaluate an arithmetic expression (no eval).

    Args:
        expression: Math expression string (e.g. '100 * 0.15').
        context: What this calculation represents.
    """
    try:
        tree = ast.parse(expression.strip(), mode="eval")
        result = _safe_eval(tree.body)
        return {"expression": expression, "result": float(result), "context": context, "validated": True}
    except ZeroDivisionError:
        return {"expression": expression, "result": None, "context": context, "validated": False, "error": "Division by zero"}
    except Exception as e:
        return {"expression": expression, "result": None, "context": context, "validated": False, "error": str(e)}


def percentage_change(old_value: float, new_value: float) -> dict:
    """Compute percentage change between two values.

    Args:
        old_value: Original value.
        new_value: New value.
    """
    if old_value == 0:
        return {"old_value": old_value, "new_value": new_value, "change_pct": None, "direction": "undefined", "error": "Division by zero"}
    change = ((new_value - old_value) / old_value) * 100.0
    direction = "increase" if change > 0 else ("decrease" if change < 0 else "no_change")
    return {"old_value": old_value, "new_value": new_value, "change_pct": round(change, 4), "direction": direction}


def rate_per_unit(numerator: float, denominator: float, unit: str) -> dict:
    """Compute a rate (numerator / denominator).

    Args:
        numerator: Value in the numerator.
        denominator: Value in the denominator.
        unit: Label describing the rate (e.g. 'visits/provider').
    """
    if denominator == 0:
        return {"numerator": numerator, "denominator": denominator, "unit": unit, "rate": None, "error": "Division by zero"}
    return {"numerator": numerator, "denominator": denominator, "unit": unit, "rate": round(numerator / denominator, 4)}
