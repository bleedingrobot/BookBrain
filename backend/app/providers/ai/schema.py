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


PROPOSE_SERIES_MERGE_TOOL = {
    "name": "propose_series_merge",
    "description": (
        "Two or more Series records in a book library have similar-looking names "
        "and may actually be the same series, split into separate database rows "
        "because an earlier identification pass phrased the name differently. "
        "Decide whether they really are one series, and if so, which of the "
        "*existing* names should be kept as canonical. Do not invent a new name "
        "that isn't already one of the given series names — pick the best of the "
        "ones provided, e.g. the more complete/correctly-spelled/official-looking "
        "one. When more than two series are given, some may be genuine matches "
        "while others are unrelated false positives that merely share a word — "
        "call out any of those in excluded_series_names rather than folding "
        "them into the merge."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "is_same_series": {
                "type": "boolean",
                "description": "True if at least two of these records really do represent one "
                "series split across multiple rows, false if none of them match at all "
                "(i.e. this whole cluster is a false-positive flag).",
            },
            "canonical_series_name": {
                "type": "string",
                "description": "Must be an exact copy of one of the series names given in the "
                "prompt — never a new or modified name.",
            },
            "excluded_series_names": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Exact copies of any of the given series names that should NOT "
                "be merged in — genuinely different works that only coincidentally look "
                "similar. Never include canonical_series_name here. Empty array if every "
                "series given is part of the same merge.",
            },
            "confidence": {
                "type": "number",
                "description": "0-100, your confidence that is_same_series and canonical_series_name are correct.",
            },
            "explanation": {
                "type": "string",
                "description": "1-3 sentences, shown directly to the human reviewer: why these "
                "look like the same series (or don't), and why the chosen name is the "
                "more likely correct/canonical one.",
            },
            "warnings": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Anything the human should double check before approving, e.g. "
                "duplicate series_number values across the merge, book counts that don't "
                "add up cleanly. Do not use this to flag a series that shouldn't be merged "
                "at all — that belongs in excluded_series_names instead. Empty array if none.",
            },
        },
        "required": [
            "is_same_series",
            "canonical_series_name",
            "excluded_series_names",
            "confidence",
            "explanation",
            "warnings",
        ],
        "additionalProperties": False,
    },
    "strict": True,
}
