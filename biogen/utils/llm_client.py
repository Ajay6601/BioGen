from openai import OpenAI
from biogen.config import MODEL, MAX_TOKENS, OPENAI_API_KEY
from biogen.utils.logger import get_logger

log = get_logger("biogen.llm")
_client = None


def _get_client() -> OpenAI:
    global _client
    if _client is None:
        _client = OpenAI(api_key=OPENAI_API_KEY)
    return _client


def call_llm(system: str, user: str, temperature: float = 0.1) -> str:
    """Single LLM call. Returns the text response."""
    client = _get_client()
    try:
        resp = client.chat.completions.create(
            model=MODEL,
            max_tokens=MAX_TOKENS,
            temperature=temperature,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        )
        text = resp.choices[0].message.content or ""
        log.debug(f"LLM call: {len(text)} chars, model={MODEL}")
        return text.strip()
    except Exception as e:
        log.error(f"LLM call failed: {e}")
        raise


def call_llm_json(system: str, user: str, temperature: float = 0.1) -> str:
    """LLM call with JSON response format."""
    client = _get_client()
    try:
        resp = client.chat.completions.create(
            model=MODEL,
            max_tokens=MAX_TOKENS,
            temperature=temperature,
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        )
        return (resp.choices[0].message.content or "{}").strip()
    except Exception as e:
        log.error(f"LLM JSON call failed: {e}")
        raise
