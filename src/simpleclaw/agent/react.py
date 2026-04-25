"""ReAct (Reasoning + Acting) engine for the agent."""

from __future__ import annotations

import json
import logging
import re

logger = logging.getLogger(__name__)


def _extract_json_object(text: str) -> str | None:
    """Extract the first top-level JSON object from text using brace-depth counting.

    Handles nested objects correctly, unlike non-greedy regex.
    """
    start = text.find("{")
    if start == -1:
        return None

    depth = 0
    in_string = False
    escape = False

    for i in range(start, len(text)):
        c = text[i]
        if escape:
            escape = False
            continue
        if c == "\\":
            escape = True
            continue
        if c == '"':
            in_string = not in_string
            continue
        if in_string:
            continue
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return text[start:i + 1]

    return None


def parse_react(
    response: str,
) -> tuple[str | None, dict | None, str | None]:
    """Parse a ReAct response into (thought, action, answer).

    Returns:
        thought: The Thought text, or None.
        action: Parsed Action JSON dict, or None.
        answer: The Answer text, or None.
    """
    thought = None
    action = None
    answer = None

    # Extract Thought (line-anchored to avoid false matches)
    thought_match = re.search(
        r"^Thought:\s*(.+?)(?=\nAction:|\nAnswer:|\Z)",
        response, re.DOTALL | re.MULTILINE,
    )
    if thought_match:
        thought = thought_match.group(1).strip()

    # Extract Answer (line-anchored)
    answer_match = re.search(
        r"^Answer:\s*(.+)", response, re.DOTALL | re.MULTILINE
    )
    if answer_match:
        answer = answer_match.group(1).strip()
        return thought, None, answer

    # Extract Action (brace-depth counting for nested JSON)
    action_match = re.search(
        r"^Action:\s*", response, re.MULTILINE
    )
    if action_match:
        json_text = _extract_json_object(response[action_match.end():])
        if json_text:
            try:
                action = json.loads(json_text)
            except json.JSONDecodeError:
                logger.warning(
                    "Failed to parse Action JSON: %s",
                    json_text[:200],
                )

    return thought, action, answer
