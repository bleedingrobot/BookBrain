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

RESOLVE_BOOK_REQUEST_TOOL = {
    "name": "resolve_book_request",
    "description": (
        "The user typed a rough description of a book they want to add to a "
        "wishlist. Work out which specific published book they mean, using your "
        "bibliographic knowledge. Fill title and author as precisely as you can. "
        "If it's part of a series, give the series name and this book's number. "
        "If you can recall an ISBN-13, include it. If the description is too "
        "vague to identify one specific book, set found=false and leave the "
        "other fields empty."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "found": {"type": "boolean"},
            "title": {"type": ["string", "null"]},
            "author": {"type": ["string", "null"]},
            "series": {"type": ["string", "null"]},
            "series_number": {"type": ["number", "null"]},
            "isbn13": {"type": ["string", "null"]},
            "note": {
                "type": ["string", "null"],
                "description": "One short sentence: how confident you are and anything ambiguous.",
            },
        },
        "required": ["found", "title", "author", "series", "series_number", "isbn13", "note"],
        "additionalProperties": False,
    },
    "strict": True,
}


IDENTIFY_SERIES_TOOL = {
    "name": "identify_series",
    "description": (
        "Report whether this already-identified book is part of a series, using "
        "your general bibliographic knowledge. Called only when neither the EPUB "
        "nor any metadata provider had series information for it."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "series": {"type": ["string", "null"]},
            "series_number": {"type": ["number", "null"]},
        },
        "required": ["series", "series_number"],
        "additionalProperties": False,
    },
    "strict": True,
}
