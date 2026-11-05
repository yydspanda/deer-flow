"""Small expression diagnostics; the existing strict parser decides acceptance."""

import hashlib
import re

from . import surge_ai_review as base

CONTRACT = "topic-expression-review-v1"
MAX_ALIASES = 16
MAX_EXPRESSION = 1024
MAX_PREVIEW = 256
EXPRESSION_ERRORS = frozenset(
    {
        "unsupported_expression_syntax",
        "missing_expression_operator",
        "invalid_expression_parentheses",
        "missing_expression_operand",
        "invalid_expression_reference",
        "incomplete_expression",
    }
)
_RESERVED = {"topic", "expression"}
_PREVIEW = re.compile(r"(?:[A-Z][A-Z0-9_]*|&&|\|\||[()]|[ \t\r\n\f\v])+")


def _digest(text):
    return hashlib.sha256(text.encode("utf-8", errors="surrogatepass")).hexdigest()


def _alias_label(value):
    if not isinstance(value, str):
        return "non_string_alias"
    if len(value) <= 32 and base.ALIAS.fullmatch(value):
        return value
    return "alias_sha256:" + _digest(value)[:16]


def _aliases(values):
    labels = sorted(_alias_label(value) for value in values)
    return labels[:MAX_ALIASES]


def _first_problem(tokens, aliases, length, lexical_failure):
    """Mirror error location only; never rewrite tokens or decide readiness."""
    depth, expect_operand = 0, True
    for token, position in tokens:
        if token == "(":
            if not expect_operand:
                return position, "missing_operator_before_parenthesis"
            depth += 1
        elif token == ")":
            if expect_operand:
                return position, "missing_operand_before_close_parenthesis"
            if not depth:
                return position, "unmatched_close_parenthesis"
            depth -= 1
        elif token in {"&&", "||"}:
            if expect_operand:
                return position, "missing_operand_before_operator"
            expect_operand = True
        else:
            if not expect_operand:
                return position, "missing_operator_between_references"
            if token not in aliases:
                return position, "undefined_condition_alias"
            expect_operand = False
    if lexical_failure is not None:
        return lexical_failure, "unsupported_expression_token"
    if depth:
        return length, "unclosed_parenthesis"
    if expect_operand:
        return length, "missing_final_operand"
    return None, None


def inspect_filter(value):
    """Inspect a decoded filter without storing condition names or values.

    Null/non-object filters have no expression to inspect. Oversized objects or
    expressions yield only counts, a hash and a limit diagnostic. Position is
    zero-based in the stripped expression, matching the existing parser's input.
    """
    if not isinstance(value, dict):
        return None
    aliases = set(value) - _RESERVED
    expression = value.get("expression")
    result = {
        "contract_version": CONTRACT,
        "defined_aliases": [],
        "referenced_aliases": [],
        "undefined_aliases": [],
        "unused_aliases": [],
        "defined_alias_count": len(aliases),
        "referenced_alias_count": None,
        "undefined_alias_count": None,
        "unused_alias_count": None,
        "alias_lists_truncated": False,
        "references_complete": False,
        "expression_length": len(expression) if isinstance(expression, str) else None,
        "expression_sha256": _digest(expression)
        if isinstance(expression, str)
        else None,
        "error_code": None,
        "path": ["filter", "expression"],
        "error_position": None,
        "position_basis": "trimmed_expression_zero_based",
        "problem": None,
    }
    if len(aliases) > MAX_ALIASES or (
        isinstance(expression, str) and len(expression) > MAX_EXPRESSION
    ):
        result.update(
            error_code="topic_expression_diagnostic_limit",
            problem="condition_or_expression_limit",
            alias_lists_truncated=bool(aliases),
        )
        return result
    result["defined_aliases"] = _aliases(aliases)
    if not aliases or any(
        not isinstance(alias, str) or not base.ALIAS.fullmatch(alias)
        for alias in aliases
    ):
        result.update(
            error_code="invalid_filter_alias",
            path=["filter"],
            problem="invalid_condition_alias",
        )
        return result
    if not isinstance(expression, str) or not expression.strip():
        result.update(
            error_code="nonempty_string_required",
            problem="expression_must_be_nonempty_string",
        )
        return result

    stripped = expression.strip()
    tokens, references, position, lexical_failure = [], set(), 0, None
    while position < len(stripped):
        match = base.TOKEN.match(stripped, position)
        if match is None:
            lexical_failure = position
            break
        token, position = match.group(1), match.end()
        tokens.append((token, match.start(1)))
        if token not in {"&&", "||", "(", ")"}:
            references.add(token)
    undefined = references - aliases
    unused = aliases - references if lexical_failure is None else set()
    result.update(
        referenced_aliases=_aliases(references),
        undefined_aliases=_aliases(undefined),
        unused_aliases=_aliases(unused),
        referenced_alias_count=len(references),
        undefined_alias_count=len(undefined),
        unused_alias_count=len(unused) if lexical_failure is None else None,
        alias_lists_truncated=any(
            len(items) > MAX_ALIASES for items in (references, undefined, unused)
        ),
        references_complete=lexical_failure is None,
    )
    if (
        len(expression) <= MAX_PREVIEW
        and _PREVIEW.fullmatch(expression)
        and all(len(alias) <= 32 for alias in references)
    ):
        result["expression_preview"] = expression
    # This is the authoritative code, rather than a second acceptance grammar.
    try:
        base._expression(expression, aliases)
    except base.ReplyValidationError as error:
        result["error_code"] = error.code
        result["path"] = list(error.path)
        result["error_position"], result["problem"] = _first_problem(
            tokens, aliases, len(stripped), lexical_failure
        )
    return result
