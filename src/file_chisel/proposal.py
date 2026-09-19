"""Read AI-generated folder proposals."""

import json


def parse_proposal_json(text: str) -> dict[str, object]:
    """Decode a JSON object; reject other top-level JSON values."""

    proposal = json.loads(text)

    if not isinstance(proposal, dict):
        raise ValueError("The proposal must be a JSON object.")

    return proposal
