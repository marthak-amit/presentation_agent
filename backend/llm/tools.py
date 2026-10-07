"""Tool schemas for the Q&A model (OpenAI-compatible function calling)."""
from __future__ import annotations

TOOLS: list[dict] = [
    {
        "type": "function",
        "function": {
            "name": "search_kb",
            "description": "Search the presentation's knowledge base (slides, speaker notes, extra documents) for facts.",
            "parameters": {
                "type": "object",
                "properties": {"query": {"type": "string", "description": "What to look up"}},
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "goto_slide",
            "description": "Show a specific slide to the audience while answering (1-based slide number).",
            "parameters": {
                "type": "object",
                "properties": {"n": {"type": "integer", "description": "Slide number, starting at 1"}},
                "required": ["n"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "resume_presenting",
            "description": "Stop Q&A and go straight back to presenting. Only when the audience asked you to continue.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
]
