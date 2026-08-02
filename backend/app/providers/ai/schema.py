IDENTIFY_BOOK_TOOL = {
    "name": "identify_book",
    "description": (
        "Report the identified book based on the evidence and candidate matches "
        "provided. ai_confidence is your own self-assessed confidence — it is "
        "stored for the review UI but never drives automation decisions; the app "
        "computes its own confidence independently from the evidence."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "title": {"type": "string"},
            "author": {"type": "string"},
            "series": {"type": ["string", "null"]},
            "series_number": {"type": ["number", "null"]},
            "ai_confidence": {
                "type": "number",
                "description": "Your own confidence, 0-100, that this identification is correct.",
            },
            "reasoning_summary": {"type": "string"},
            "needs_human_review": {"type": "boolean"},
        },
        "required": [
            "title",
            "author",
            "series",
            "series_number",
            "ai_confidence",
            "reasoning_summary",
            "needs_human_review",
        ],
        "additionalProperties": False,
    },
    "strict": True,
}
