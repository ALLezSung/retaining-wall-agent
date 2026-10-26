"""Strict, domain-independent JSON encoding used by tools and host adapters."""
import json


def dumps(value):
    return json.dumps(value, ensure_ascii=False, allow_nan=False)


def loads(value):
    def pairs(items):
        result = {}
        for key, item in items:
            if key in result:
                raise ValueError("duplicate JSON key")
            result[key] = item
        return result

    def constant(value):
        raise ValueError("non-finite JSON number")
    result = json.loads(value, object_pairs_hook=pairs, parse_constant=constant)
    dumps(result)  # Also reject exponent overflow (1e999).
    return result
