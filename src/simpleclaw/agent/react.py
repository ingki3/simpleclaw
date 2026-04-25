"""ReAct (Reasoning + Acting) engine for the agent."""

from __future__ import annotations

import json
import logging
import re

logger = logging.getLogger(__name__)


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

    # Extract Thought
    thought_match = re.search(
        r"Thought:\s*(.+?)(?=\nAction:|\nAnswer:|\Z)",
        response, re.DOTALL,
    )
    if thought_match:
        thought = thought_match.group(1).strip()

    # Extract Answer
    answer_match = re.search(r"Answer:\s*(.+)", response, re.DOTALL)
    if answer_match:
        answer = answer_match.group(1).strip()
        return thought, None, answer

    # Extract Action (JSON)
    action_match = re.search(r"Action:\s*(\{.+?\})", response, re.DOTALL)
    if action_match:
        try:
            action = json.loads(action_match.group(1))
        except json.JSONDecodeError:
            logger.warning(
                "Failed to parse Action JSON: %s",
                action_match.group(1)[:200],
            )

    return thought, action, answer
