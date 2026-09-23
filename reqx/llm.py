"""Model runners. AnthropicRunner is the real one; ReplayRunner re-reads saved responses so the
validator and tests run without network; DryRunRunner prints what would be sent and stops."""
from __future__ import annotations

import hashlib
import json
import os
from typing import Any, Dict, Optional

from .prompt import OUTPUT_SCHEMA, SYSTEM_PROMPT, show, user_message

MODEL = "claude-opus-5"


class DryRun(Exception):
    pass


def request_key(program_title: str, stated_total: str, group_text: str) -> str:
    return hashlib.sha1(f"{MODEL}\x1f{SYSTEM_PROMPT}\x1f{program_title}\x1f{stated_total}\x1f{group_text}".encode()).hexdigest()[:16]


class AnthropicRunner:
    def __init__(self, cache_dir: str = "reqx_cache", model: str = MODEL, effort: str = "high"):
        import anthropic
        self.client = anthropic.Anthropic()      # ANTHROPIC_API_KEY / ANTHROPIC_AUTH_TOKEN / `ant auth login` profile
        self.model, self.effort, self.cache_dir = model, effort, cache_dir
        os.makedirs(cache_dir, exist_ok=True)

    def run(self, program_title: str, stated_total: str, group_text: str, preamble) -> Dict[str, Any]:
        key = request_key(program_title, stated_total, group_text)
        response = self.client.messages.create(
            model=self.model,
            max_tokens=16000,
            system=[{"type": "text", "text": SYSTEM_PROMPT, "cache_control": {"type": "ephemeral"}}],
            messages=[{"role": "user", "content": user_message(program_title, stated_total, group_text, preamble)}],
            thinking={"type": "adaptive"},
            output_config={"effort": self.effort, "format": {"type": "json_schema", "schema": OUTPUT_SCHEMA}},
        )
        raw = {"request_id": getattr(response, "_request_id", None), "model": response.model, "stop_reason": response.stop_reason,
               "usage": response.usage.to_dict() if hasattr(response.usage, "to_dict") else None}
        if response.stop_reason == "refusal":
            raw["stop_details"] = response.stop_details.to_dict() if response.stop_details else None
            raise RuntimeError(f"model refused: {raw['stop_details']}")
        if response.stop_reason == "max_tokens":
            raise RuntimeError("output truncated at max_tokens; raise max_tokens or split the section")
        text = next(b.text for b in response.content if b.type == "text")
        data = json.loads(text)
        raw["output"] = data
        json.dump(raw, open(os.path.join(self.cache_dir, key + ".json"), "w"), indent=1)
        return data


class ReplayRunner:
    """Reads responses saved by AnthropicRunner (or fixtures with the same layout)."""

    def __init__(self, cache_dir: str):
        self.cache_dir = cache_dir

    def run(self, program_title, stated_total, group_text, preamble) -> Dict[str, Any]:
        key = request_key(program_title, stated_total, group_text)
        fn = os.path.join(self.cache_dir, key + ".json")
        if not os.path.exists(fn):
            raise FileNotFoundError(f"no saved response for this section (key {key}); run with --runner anthropic first")
        return json.load(open(fn))["output"]


class DryRunRunner:
    def run(self, program_title, stated_total, group_text, preamble):
        print(show(program_title, stated_total, group_text, preamble))
        raise DryRun()
