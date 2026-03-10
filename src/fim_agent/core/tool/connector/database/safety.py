"""SQL safety validation for database connector queries.

Validates and sanitises SQL statements before execution to prevent
dangerous operations. Only SELECT queries (and WITH...SELECT CTEs)
are allowed unless ``allow_write=True`` is explicitly passed.
"""

from __future__ import annotations

import re


class SqlSafetyError(Exception):
    """Raised when a SQL statement fails safety validation."""


# Patterns that are always blocked regardless of allow_write
_DANGEROUS_PATTERNS: list[tuple[re.Pattern, str]] = [
    (re.compile(r"\bINTO\s+OUTFILE\b", re.IGNORECASE), "INTO OUTFILE is not allowed"),
    (re.compile(r"\bLOAD_FILE\s*\(", re.IGNORECASE), "LOAD_FILE() is not allowed"),
    (re.compile(r"\bBENCHMARK\s*\(", re.IGNORECASE), "BENCHMARK() is not allowed"),
    (re.compile(r"\bSLEEP\s*\(", re.IGNORECASE), "SLEEP() is not allowed"),
    (re.compile(r"\bpg_sleep\s*\(", re.IGNORECASE), "pg_sleep() is not allowed"),
    (re.compile(r"\bxp_cmdshell\b", re.IGNORECASE), "xp_cmdshell is not allowed"),
    (re.compile(r"\bEXEC\s*\(", re.IGNORECASE), "EXEC() is not allowed"),
    (re.compile(r"\bEXECUTE\s*\(", re.IGNORECASE), "EXECUTE() is not allowed"),
    (re.compile(r"\bINTO\s+DUMPFILE\b", re.IGNORECASE), "INTO DUMPFILE is not allowed"),
    (re.compile(r"\bLOAD\s+DATA\b", re.IGNORECASE), "LOAD DATA is not allowed"),
]

# SQL statement types that are only allowed for reads
_WRITE_KEYWORDS = {"INSERT", "UPDATE", "DELETE", "DROP", "ALTER", "CREATE", "TRUNCATE", "REPLACE", "MERGE", "GRANT", "REVOKE"}


def validate_sql(sql: str, *, allow_write: bool = False) -> str:
    """Validate and clean a SQL statement for safe execution.

    Parameters
    ----------
    sql:
        The raw SQL string to validate.
    allow_write:
        If ``False`` (default), only SELECT and WITH...SELECT are allowed.

    Returns
    -------
    str
        The cleaned SQL string (trailing semicolons stripped).

    Raises
    ------
    SqlSafetyError
        If the SQL fails any safety check.
    """
    if not sql or not sql.strip():
        raise SqlSafetyError("Empty SQL statement")

    cleaned = sql.strip()

    # Strip trailing semicolons
    cleaned = cleaned.rstrip(";").strip()

    if not cleaned:
        raise SqlSafetyError("Empty SQL statement after cleaning")

    # Block multi-statement queries (multiple ; separated statements)
    # Simple heuristic: check for semicolons that are not inside string literals
    if _has_multiple_statements(cleaned):
        raise SqlSafetyError("Multi-statement queries are not allowed")

    # Check dangerous patterns (always blocked)
    for pattern, message in _DANGEROUS_PATTERNS:
        if pattern.search(cleaned):
            raise SqlSafetyError(message)

    # Check statement type if writes are not allowed
    if not allow_write:
        # Extract the first keyword to determine statement type
        # Handle WITH ... SELECT (CTEs)
        first_keyword = _get_first_keyword(cleaned)
        if first_keyword == "WITH":
            # CTE — verify it ultimately does a SELECT, not INSERT/UPDATE/DELETE
            if not _cte_is_select(cleaned):
                raise SqlSafetyError(
                    "WITH (CTE) queries must end with SELECT, not a write operation"
                )
        elif first_keyword == "SELECT":
            pass  # OK
        elif first_keyword == "EXPLAIN":
            pass  # EXPLAIN is read-only
        elif first_keyword == "SHOW":
            pass  # SHOW is read-only (MySQL)
        elif first_keyword == "DESCRIBE" or first_keyword == "DESC":
            pass  # DESCRIBE is read-only
        else:
            raise SqlSafetyError(
                f"Only SELECT queries are allowed (got {first_keyword}). "
                "Write operations require explicit permission."
            )

    return cleaned


def _get_first_keyword(sql: str) -> str:
    """Extract the first SQL keyword from a statement."""
    # Skip comments
    stripped = sql.lstrip()
    # Skip -- comments
    while stripped.startswith("--"):
        newline = stripped.find("\n")
        if newline == -1:
            return ""
        stripped = stripped[newline + 1:].lstrip()
    # Skip /* */ comments
    while stripped.startswith("/*"):
        end = stripped.find("*/")
        if end == -1:
            return ""
        stripped = stripped[end + 2:].lstrip()

    # Extract first word
    match = re.match(r"[A-Za-z_]+", stripped)
    return match.group(0).upper() if match else ""


def _cte_is_select(sql: str) -> bool:
    """Check whether a WITH (CTE) query ends with SELECT rather than a write op."""
    upper = sql.upper()
    # Find the last top-level statement keyword after the CTE definitions
    # Look for the final SELECT/INSERT/UPDATE/DELETE after the last closing paren
    # of the CTE block. A simplified approach: find the last occurrence of
    # write keywords and SELECT, and ensure SELECT comes last.
    for kw in _WRITE_KEYWORDS:
        # Check if any write keyword appears after the last ) that closes CTEs
        pattern = re.compile(r"\)\s*" + kw + r"\b", re.IGNORECASE)
        if pattern.search(sql):
            return False
    return True


def _has_multiple_statements(sql: str) -> bool:
    """Detect multiple SQL statements separated by semicolons.

    Ignores semicolons inside single-quoted string literals.
    """
    in_string = False
    escape_next = False
    for char in sql:
        if escape_next:
            escape_next = False
            continue
        if char == "\\":
            escape_next = True
            continue
        if char == "'":
            in_string = not in_string
            continue
        if char == ";" and not in_string:
            return True
    return False
