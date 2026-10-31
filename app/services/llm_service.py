import asyncio
import logging
from pathlib import Path
import time

from ...config import settings
from .event_bus import EventBus
from ..utils.code_extractor import extract_code_and_requirements

logger = logging.getLogger(__name__)

_system_prompt: str | None = None

def _load_system_prompt() -> str:
    global _system_prompt
    if _system_prompt is None:
        prompt_path = Path(__file__).parent.parent / "prompts" / "system_prompt.txt"
        _system_prompt = prompt_path.read_text(encoding="utf-8")
    return _system_prompt

def _stream_ollama(user_prompt: str, system_prompt: str | None = None):
    """Blocking call to the Ollama API to stream the response. """
    import ollama

    system_prompt = system_prompt or _load_system_prompt()
    return ollama.chat(
        model=settings.OLLAMA_MODEL,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        stream=True,
    )

def _stream_groq(
    user_prompt: str,
    api_key: str,
    system_prompt: str | None = None,
    model: str | None = None,
):
    """Blocking call to the Groq API to stream the response. """
    import groq

    system_prompt = system_prompt or _load_system_prompt()
    client = groq.Groq(api_key=api_key)
    return client.chat.completions.create(
        model=model or settings.GROQ_MODEL,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        stream=True,
    )

async def generate_text(
    user_prompt: str,
    bus: EventBus,
    *,
    groq_api_key: str | None = None,
    groq_model: str | None = None,
    system_prompt: str | None = None,
    event_type: str | None = "planning",
) -> str:
    """Generate text using the configured provider, optionally streaming events."""
    loop = asyncio.get_running_loop()
    api_key = groq_api_key.strip() if groq_api_key and groq_api_key.strip() else None
    system_prompt = system_prompt or _load_system_prompt()

    full_response = ""
    token_queue: asyncio.Queue[str | Exception | None] = asyncio.Queue()

    # Environment gate (defense in depth with the router check): cloud demo
    # runs with ENVIRONMENT=production and has no Ollama server, so requests
    # without a user-supplied Groq key are rejected instead of silently
    # falling back to Ollama. Locally, no key means use local Ollama.
    if api_key is None and settings.is_production:
        raise ValueError(
            "No Groq API key provided. This deployment only supports the "
            "Groq provider; run locally to use Ollama."
        )

    provider = "groq" if api_key is not None else "ollama"
    model = (groq_model or settings.GROQ_MODEL) if api_key is not None else settings.OLLAMA_MODEL
    started_at = time.monotonic()
    logger.info(
        "LLM generation started provider=%s model=%s prompt_chars=%d streaming_event=%s",
        provider,
        model,
        len(user_prompt),
        event_type or "disabled",
    )

    def _stream_to_queue():
        nonlocal full_response
        try:
            if api_key is not None:
                stream = _stream_groq(
                    user_prompt,
                    api_key,
                    system_prompt if system_prompt != _load_system_prompt() else None,
                    model=groq_model,
                )
                for chunk in stream:
                    token = chunk.choices[0].delta.content or ""
                    if token:
                        full_response += token
                        asyncio.run_coroutine_threadsafe(
                            token_queue.put(token), loop
                        )
            else:
                stream = (
                    _stream_ollama(user_prompt)
                    if system_prompt == _load_system_prompt()
                    else _stream_ollama(user_prompt, system_prompt)
                )
                for chunk in stream:
                    token = chunk["message"]["content"] or " "
                    full_response += token
                    asyncio.run_coroutine_threadsafe(
                        token_queue.put(token), loop
                    )
        except Exception as error:
            logger.exception(
                "LLM stream failed provider=%s model=%s duration_seconds=%.2f",
                provider,
                model,
                time.monotonic() - started_at,
            )
            asyncio.run_coroutine_threadsafe(token_queue.put(error), loop)
        finally:
            asyncio.run_coroutine_threadsafe(token_queue.put(None), loop)

    # Start streaming in background thread
    loop.run_in_executor(None, _stream_to_queue)

    # Consume tokens and publish planning events
    while True:
        token = await token_queue.get()
        if token is None:
            break
        if isinstance(token, Exception):
            raise token
        if event_type:
            await bus.publish({"type": event_type, "content": token})

    logger.info(
        "LLM generation finished provider=%s model=%s duration_seconds=%.2f response_chars=%d",
        provider,
        model,
        time.monotonic() - started_at,
        len(full_response),
    )
    return full_response


async def generate_plan_and_code(
    user_prompt: str,
    bus: EventBus,
    *,
    groq_api_key: str | None = None,
    groq_model: str | None = None,
    system_prompt: str | None = None,
    event_type: str | None = "planning",
) -> tuple[str, str, list[str]]:
    """Generate a response, extract its first code block, and return requirements."""
    full_response = await generate_text(
        user_prompt,
        bus,
        groq_api_key=groq_api_key,
        groq_model=groq_model,
        system_prompt=system_prompt,
        event_type=event_type,
    )

    code, requirements = extract_code_and_requirements(full_response)
    if code:
        await bus.publish({
            "type": "code",
            "content": code
        })

    return full_response, code, requirements
