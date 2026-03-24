"""
llm.py — provider-agnostic LLM client for repomap.

Supported providers
───────────────────
  anthropic   Claude models via api.anthropic.com
  openai      GPT models via api.openai.com
  ollama      Local models via localhost:11434
  openai-compat  Any OpenAI-compatible endpoint (Together, Groq, Mistral, etc.)

Auto-detection order (when --llm-provider is not set):
  1. ANTHROPIC_API_KEY  → anthropic
  2. OPENAI_API_KEY     → openai
  3. OLLAMA_HOST / localhost:11434 reachable → ollama
  4. Nothing found → raises LLMNotConfigured
"""

from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Optional


class LLMNotConfigured(Exception):
    """Raised when no LLM provider can be auto-detected."""

class LLMError(Exception):
    """Raised when an LLM API call fails."""


@dataclass
class LLMConfig:
    provider: str
    api_key: Optional[str]  = None
    model:   Optional[str]  = None
    base_url: Optional[str] = None  # for openai-compat / custom ollama host
    max_tokens: int         = 1024
    timeout: int            = 60

    DEFAULTS: dict = field(default_factory=lambda: {
        "anthropic":    {"model": "claude-3-5-haiku-20241022",  "base_url": "https://api.anthropic.com"},
        "openai":       {"model": "gpt-4o-mini",                "base_url": "https://api.openai.com"},
        "ollama":       {"model": "llama3",                     "base_url": "http://localhost:11434"},
        "openai-compat":{"model": "mistral-7b-instruct",        "base_url": None},
    }, repr=False)

    def __post_init__(self):
        defaults = self.DEFAULTS.get(self.provider, {})
        if not self.model:
            self.model = defaults.get("model")
        if not self.base_url:
            self.base_url = defaults.get("base_url")


def _post_json(url: str, headers: dict, body: dict, timeout: int) -> dict:
    data = json.dumps(body).encode()
    req  = urllib.request.Request(url, data=data, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        raw = e.read().decode(errors="replace")
        raise LLMError(f"HTTP {e.code} from {url}: {raw}") from e
    except urllib.error.URLError as e:
        raise LLMError(f"Connection error to {url}: {e.reason}") from e


def _get_json(url: str, timeout: int = 5) -> dict:
    req = urllib.request.Request(url)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read())
    except Exception:
        raise


def _call_anthropic(cfg: LLMConfig, system: str, user: str) -> str:
    url = f"{cfg.base_url.rstrip('/')}/v1/messages"
    headers = {
        "Content-Type":      "application/json",
        "x-api-key":         cfg.api_key or "",
        "anthropic-version": "2023-06-01",
    }
    body = {
        "model":      cfg.model,
        "max_tokens": cfg.max_tokens,
        "system":     system,
        "messages":   [{"role": "user", "content": user}],
    }
    resp = _post_json(url, headers, body, cfg.timeout)
    try:
        return resp["content"][0]["text"].strip()
    except (KeyError, IndexError) as e:
        raise LLMError(f"Unexpected Anthropic response shape: {resp}") from e


def _call_openai_compat(cfg: LLMConfig, system: str, user: str, *, provider_name: str = "openai") -> str:
    url = f"{cfg.base_url.rstrip('/')}/v1/chat/completions"
    headers = {
        "Content-Type":  "application/json",
        "Authorization": f"Bearer {cfg.api_key or ''}",
    }
    body = {
        "model":      cfg.model,
        "max_tokens": cfg.max_tokens,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user",   "content": user},
        ],
    }
    resp = _post_json(url, headers, body, cfg.timeout)
    try:
        return resp["choices"][0]["message"]["content"].strip()
    except (KeyError, IndexError) as e:
        raise LLMError(f"Unexpected {provider_name} response shape: {resp}") from e


def _call_ollama(cfg: LLMConfig, system: str, user: str) -> str:
    """Ollama has its own /api/chat endpoint that mirrors OpenAI chat."""
    url = f"{cfg.base_url.rstrip('/')}/api/chat"
    headers = {"Content-Type": "application/json"}
    body = {
        "model":  cfg.model,
        "stream": False,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user",   "content": user},
        ],
    }
    resp = _post_json(url, headers, body, cfg.timeout)
    try:
        return resp["message"]["content"].strip()
    except (KeyError, IndexError) as e:
        raise LLMError(f"Unexpected Ollama response shape: {resp}") from e


def call(cfg: LLMConfig, system: str, user: str) -> str:
    """Dispatch to the right provider and return the assistant text."""
    if cfg.provider == "anthropic":
        return _call_anthropic(cfg, system, user)
    elif cfg.provider == "openai":
        return _call_openai_compat(cfg, system, user, provider_name="openai")
    elif cfg.provider == "openai-compat":
        return _call_openai_compat(cfg, system, user, provider_name="openai-compat")
    elif cfg.provider == "ollama":
        return _call_ollama(cfg, system, user)
    else:
        raise LLMError(f"Unknown provider: '{cfg.provider}'")


def auto_detect(model: Optional[str] = None,
                base_url: Optional[str] = None) -> LLMConfig:
    """
    Try to find a working provider from the environment.
    Order: ANTHROPIC_API_KEY → OPENAI_API_KEY → Ollama → fail.
    """
    ak = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if ak:
        return LLMConfig(provider="anthropic", api_key=ak, model=model, base_url=base_url)

    ok = os.environ.get("OPENAI_API_KEY", "").strip()
    if ok:
        return LLMConfig(provider="openai", api_key=ok, model=model, base_url=base_url)

    for env_var, bu in [
        ("GROQ_API_KEY",     "https://api.groq.com/openai"),
        ("TOGETHER_API_KEY", "https://api.together.xyz"),
        ("MISTRAL_API_KEY",  "https://api.mistral.ai"),
        ("FIREWORKS_API_KEY","https://api.fireworks.ai/inference"),
    ]:
        k = os.environ.get(env_var, "").strip()
        if k:
            return LLMConfig(
                provider="openai-compat",
                api_key=k,
                model=model,
                base_url=base_url or bu,
            )

    ollama_host = os.environ.get("OLLAMA_HOST", "http://localhost:11434").strip()
    try:
        _get_json(f"{ollama_host}/api/tags", timeout=3)
        return LLMConfig(provider="ollama", model=model, base_url=ollama_host)
    except Exception:
        pass

    raise LLMNotConfigured(
        "No LLM provider found. Set one of:\n"
        "  ANTHROPIC_API_KEY, OPENAI_API_KEY, GROQ_API_KEY,\n"
        "  TOGETHER_API_KEY, MISTRAL_API_KEY — or run Ollama locally."
    )


def build_config(
    provider:  Optional[str] = None,
    api_key:   Optional[str] = None,
    model:     Optional[str] = None,
    base_url:  Optional[str] = None,
    max_tokens: int = 1024,
    timeout:    int = 60,
) -> LLMConfig:
    """
    Build an LLMConfig from explicit CLI flags, falling back to auto-detection.
    If provider is given but api_key is not, we also check env vars.
    """
    if provider:
        resolved_key = api_key or _env_key_for(provider)
        return LLMConfig(
            provider=provider,
            api_key=resolved_key,
            model=model,
            base_url=base_url,
            max_tokens=max_tokens,
            timeout=timeout,
        )

    cfg = auto_detect(model=model, base_url=base_url)
    cfg.max_tokens = max_tokens
    cfg.timeout    = timeout
    if model:
        cfg.model = model
    if base_url:
        cfg.base_url = base_url
    return cfg


def _env_key_for(provider: str) -> Optional[str]:
    mapping = {
        "anthropic":    "ANTHROPIC_API_KEY",
        "openai":       "OPENAI_API_KEY",
        "groq":         "GROQ_API_KEY",
        "together":     "TOGETHER_API_KEY",
        "mistral":      "MISTRAL_API_KEY",
        "fireworks":    "FIREWORKS_API_KEY",
        "openai-compat": None,   # base_url required; key optional
        "ollama":        None,
    }
    env = mapping.get(provider)
    return os.environ.get(env, "").strip() or None if env else None


SYSTEM_PROMPT = """\
You are an expert software engineer performing an onboarding analysis of a code repository.
Your job is to help a new developer quickly understand how to get started with the project.
Be concise, technical, and practical. Use plain text — no markdown formatting in your output.
Focus on what a developer needs to know in the first 30 minutes of working on this codebase.
""".strip()


def summarize_entry_points(cfg: LLMConfig, repo_url: str, scan_data: dict,
                            file_contents: dict[str, str]) -> str:
    """
    Ask the LLM to summarize the entry points given their source code.
    Returns a plain-text summary paragraph.
    """
    files_block = ""
    for path, content in file_contents.items():
        files_block += f"\n### {path}\n```\n{content[:3000]}\n```\n"

    user = f"""
Repository: {repo_url}

Detected entry points: {', '.join(scan_data.get('entry_points', [])) or 'none'}
Languages: {', '.join(scan_data.get('languages', {}).keys()) or 'unknown'}
Config files: {', '.join(scan_data.get('configs', [])[:5]) or 'none'}
Test directories: {', '.join(scan_data.get('tests', [])[:3]) or 'none'}

Entry point file contents:
{files_block.strip()}

Task:
1. Explain in 2–3 sentences what this project does based on the entry point(s).
2. Describe how a developer would run/start it locally (exact commands if visible).
3. Note any non-obvious setup steps (env vars needed, services required, etc.).
4. One-sentence verdict on onboarding friendliness.

Keep your answer under 200 words. Plain text only, no markdown.
""".strip()

    return call(cfg, SYSTEM_PROMPT, user)


def summarize_full_report(cfg: LLMConfig, repo_url: str, scan_data: dict) -> str:
    """
    Ask the LLM to write a holistic onboarding narrative from the full scan data.
    Returns a plain-text multi-paragraph summary.
    """
    friction_list = "\n".join(
        f"  [{i['severity']}] {i['message']}" for i in scan_data.get("friction", [])
    )

    user = f"""
Repository: {repo_url}
Score: {scan_data['score']['value']}/100 ({scan_data['score']['label']})
Files: {scan_data['file_count']}
Languages: {', '.join(scan_data.get('languages', {}).keys())}
Entry points: {', '.join(scan_data.get('entry_points', [])[:5]) or 'none'}
Routes: {', '.join(scan_data.get('routes', [])[:5]) or 'none'}
Models/schemas: {', '.join(scan_data.get('models', [])[:5]) or 'none'}
Tests: {', '.join(scan_data.get('tests', [])[:5]) or 'none'}
Docs: {', '.join(scan_data.get('docs', [])[:5]) or 'none'}
CI: {', '.join(scan_data.get('ci', [])) or 'none'}

Friction analysis:
{friction_list or '  (none)'}

Write a 3-paragraph onboarding narrative for a developer joining this project:
Paragraph 1 — What the repo appears to be and its overall structure.
Paragraph 2 — How to get started (setup, run, test).
Paragraph 3 — The biggest onboarding gaps or risks to be aware of.

Keep it under 250 words. Plain text only.
""".strip()

    return call(cfg, SYSTEM_PROMPT, user)
