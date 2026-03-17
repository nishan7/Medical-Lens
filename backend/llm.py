from __future__ import annotations

import asyncio
import logging
from typing import Any, AsyncIterator, Dict, Iterable, List, Sequence

from langchain_nvidia_ai_endpoints import ChatNVIDIA
from langchain_core.messages import AIMessage, AIMessageChunk, BaseMessage, HumanMessage, SystemMessage, ToolMessage
from langgraph.prebuilt import create_react_agent

from config import settings
from tools import get_langchain_tools


Message = Dict[str, str]
TOOL_WATCHDOG_SECONDS = 8.0
TOOL_INVOKE_FALLBACK_TIMEOUT_SECONDS = 12.0
AGENT_RECURSION_LIMIT = max(1, int(settings.agent_recursion_limit))
GENERIC_STREAM_FAILURE_MESSAGE = (
    "I hit a temporary model/tool timeout. "
    "Please retry with a narrower query (for example: city, insurer, or hospital)."
)
logger = logging.getLogger(__name__)


def _client_kwargs(
    *,
    temperature: float | None = None,
    max_completion_tokens: int | None = None,
) -> Dict[str, Any]:
    base_kwargs: Dict[str, Any] = {
        "model": settings.nvidia_model,
        "api_key": settings.nvidia_api_key,
        "temperature": settings.nvidia_temperature if temperature is None else temperature,
        "top_p": settings.nvidia_top_p,
        "max_completion_tokens": (
            settings.nvidia_max_tokens if max_completion_tokens is None else max_completion_tokens
        ),
        "chat_template_kwargs": {"enable_thinking": False},
    }

    # Some NVIDIA models reject reasoning_budget as an extra field.
    if "nemotron-3-super-120b" in settings.nvidia_model:
        base_kwargs["reasoning_budget"] = settings.nvidia_reasoning_budget

    return base_kwargs


def build_client(
    *,
    temperature: float | None = None,
    max_completion_tokens: int | None = None,
) -> ChatNVIDIA:
    return ChatNVIDIA(
        **_client_kwargs(
            temperature=temperature,
            max_completion_tokens=max_completion_tokens,
        )
    )


def _build_client() -> ChatNVIDIA:
    return build_client()


client = _build_client()


def _to_lc_messages(messages: Sequence[Message]) -> List[BaseMessage]:
    out: List[BaseMessage] = []
    for m in messages:
        role = m.get("role")
        content = m.get("content", "")
        if role == "system":
            out.append(SystemMessage(content=content))
        elif role == "assistant":
            out.append(AIMessage(content=content))
        else:
            out.append(HumanMessage(content=content))
    return out


_agent: Any | None = None


def get_agent():
    global _agent
    if _agent is not None:
        return _agent

    tools = get_langchain_tools()
    system_prompt = (
        "You are a helpful assistant for checking the bill details and charges and verify validate or critize them "
        "Use tools when needed to answer questions about hospital standard charges. "
        "If the user asks about hospital prices, procedures, codes, or searching the dataset, call an appropriate tool. "
        "If required details are missing, ask a concise follow-up question. "
        "Format responses as clean markdown with short paragraphs or a flat bullet list. "
        "Avoid deeply nested lists and avoid empty bullet points. "
        "Be concise and cite tool results."
    )

    _agent = create_react_agent(model=client, tools=tools, prompt=system_prompt)
    return _agent


def _extract_message_text(content: Any) -> str:
    if isinstance(content, str):
        return content

    if isinstance(content, list):
        parts: List[str] = []
        for block in content:
            if not isinstance(block, dict):
                continue
            text = block.get("text")
            if text:
                parts.append(str(text))
        return "".join(parts)

    if content is None:
        return ""

    return str(content)


def complete_text(
    system_prompt: str,
    user_prompt: str,
    *,
    temperature: float | None = None,
    max_tokens: int | None = None,
) -> str:
    prompt_client = build_client(
        temperature=temperature,
        max_completion_tokens=max_tokens,
    )
    result = prompt_client.invoke(
        [
            SystemMessage(content=system_prompt),
            HumanMessage(content=user_prompt),
        ]
    )
    return _extract_message_text(getattr(result, "content", ""))


def agent_chat(messages: List[Message]) -> str:
    """
    ReAct chat using the configured model + tools.
    Returns final assistant text from the agent.
    """
    lc_messages = _to_lc_messages(messages)
    agent = get_agent()
    result = agent.invoke(
        {"messages": lc_messages},
        config={"recursion_limit": AGENT_RECURSION_LIMIT},
    )
    out_messages = result.get("messages") or []
    if not out_messages:
        return ""
    return _extract_message_text(getattr(out_messages[-1], "content", ""))


def _iter_text_deltas(chunk: AIMessageChunk) -> Iterable[str]:
    text = getattr(chunk, "text", None)
    if text:
        yield str(text)
        return

    for block in getattr(chunk, "content_blocks", []) or []:
        if block.get("type") == "text" and block.get("text"):
            yield str(block["text"])


async def _agent_astream_events(messages: List[Message]) -> AsyncIterator[Dict[str, Any]]:
    """
    Async event stream for tool-enabled agent.
    Yields dicts that can be forwarded to the frontend.
    """
    lc_messages = _to_lc_messages(messages)
    agent = get_agent()
    seen_tool_calls: set[str] = set()

    def _jsonable(value: Any) -> Any:
        if value is None:
            return None
        if isinstance(value, (str, int, float, bool)):
            return value
        if isinstance(value, (list, tuple)):
            return [_jsonable(v) for v in value]
        if isinstance(value, dict):
            return {str(k): _jsonable(v) for k, v in value.items()}
        content = getattr(value, "content", None)
        if isinstance(content, str):
            return content
        return str(value)

    async for stream_mode, chunk in agent.astream(
        {"messages": lc_messages},
        stream_mode=["messages", "updates"],
        config={"recursion_limit": AGENT_RECURSION_LIMIT},
    ):
        if stream_mode == "messages":
            token, _metadata = chunk
            if isinstance(token, AIMessageChunk):
                for delta in _iter_text_deltas(token):
                    yield {"type": "token", "delta": delta}
            continue

        if stream_mode != "updates":
            continue

        for step, data in chunk.items():
            out_messages = data.get("messages") or []
            if not out_messages:
                continue

            message = out_messages[-1]

            if isinstance(message, AIMessage):
                for tool_call in getattr(message, "tool_calls", []) or []:
                    tool_call_id = str(tool_call.get("id") or f"{tool_call.get('name')}:{tool_call.get('args')}")
                    if tool_call_id in seen_tool_calls:
                        continue
                    seen_tool_calls.add(tool_call_id)
                    logger.info(
                        "Agent tool_start name=%s input=%s",
                        tool_call.get("name"),
                        _jsonable(tool_call.get("args")),
                    )
            elif isinstance(message, ToolMessage):
                logger.info(
                    "Agent tool_end name=%s output=%s",
                    getattr(message, "name", None) or step,
                    _jsonable(getattr(message, "content_blocks", None) or message.content),
                )

    yield {"type": "done"}


async def _stream_agent_events_with_watchdog(messages: List[Message], request_id: str) -> AsyncIterator[Dict[str, Any]]:
    agent_stream = _agent_astream_events(messages)
    token_seen = False

    try:
        while True:
            timeout = TOOL_WATCHDOG_SECONDS if not token_seen else None
            try:
                if timeout is None:
                    event = await agent_stream.__anext__()
                else:
                    event = await asyncio.wait_for(agent_stream.__anext__(), timeout=timeout)
            except StopAsyncIteration:
                return
            except TimeoutError:
                logger.warning(
                    "ReAct stream watchdog triggered after %ss without visible token; falling back to invoke",
                    TOOL_WATCHDOG_SECONDS,
                )
                raise

            event_type = event.get("type")
            if event_type == "token":
                delta = str(event.get("delta", ""))
                if delta:
                    token_seen = True
                    yield {"type": "token", "delta": delta, "request_id": request_id}
            elif event_type == "done":
                yield {"type": "done", "request_id": request_id}
                return
    finally:
        await agent_stream.aclose()


async def _stream_agent_invoke_fallback(messages: List[Message], request_id: str) -> AsyncIterator[Dict[str, Any]]:
    """
    Fallback path when streaming stalls/fails.
    Uses non-streaming ReAct invoke, then emits one token payload.
    """
    text = await asyncio.wait_for(
        asyncio.to_thread(agent_chat, messages),
        timeout=TOOL_INVOKE_FALLBACK_TIMEOUT_SECONDS,
    )
    if text:
        yield {"type": "token", "delta": str(text), "request_id": request_id}
    yield {"type": "done", "request_id": request_id}


async def stream_events(messages: List[Message], request_id: str) -> AsyncIterator[Dict[str, Any]]:
    emitted_done = False
    try:
        async for event in _stream_agent_events_with_watchdog(messages, request_id):
            if event.get("type") == "done":
                emitted_done = True
            yield event
    except TimeoutError:
        logger.warning("ReAct streaming timed out; switching to invoke fallback")
    except Exception as exc:
        logger.exception("ReAct streaming failed, switching to invoke fallback: %s", exc)

    if emitted_done:
        return

    try:
        async for event in _stream_agent_invoke_fallback(messages, request_id):
            yield event
    except TimeoutError:
        logger.warning(
            "ReAct invoke fallback timed out after %ss",
            TOOL_INVOKE_FALLBACK_TIMEOUT_SECONDS,
        )
        yield {"type": "token", "delta": GENERIC_STREAM_FAILURE_MESSAGE, "request_id": request_id}
        yield {"type": "done", "request_id": request_id}
    except Exception as exc:
        logger.exception("ReAct invoke fallback failed: %s", exc)
        yield {"type": "token", "delta": GENERIC_STREAM_FAILURE_MESSAGE, "request_id": request_id}
        yield {"type": "done", "request_id": request_id}
