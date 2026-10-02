import json

from dotenv import load_dotenv
from groq import Groq,APIConnectionError,APIStatusError,RateLimitError
import time
import subprocess
from enum import Enum
from rich import print
from system_prompt import SYSTEM_PROMPT
from groq.types.chat import ChatCompletionMessage

from groq.types.chat import ChatCompletionMessage,ChatCompletionMessageToolCall
from groq.types.chat.chat_completion_message_tool_call import Function


class ContextTooLargeError(RuntimeError):
    """The request was rejected for exceeding the per-minute input token budget."""


def is_oversized_request(error:APIStatusError) -> bool:
    """True when groq refused the request because it was too large.

    Retrying cannot help, the same payload is just as large on the next attempt.
    """
    if getattr(error,"status_code",None) == 413:
        return True

    body = getattr(error,"body",None)
    detail = body.get("error",{}) if isinstance(body,dict) else {}

    return detail.get("code") == "rate_limit_exceeded" and detail.get("type") == "tokens"


def call_model(client,**kwargs):
    MAX_RETRIES = 5
    BASE_BACKOFF_SECONDS = 1.0

    for attempt in range(MAX_RETRIES):
        try:
            return client.chat.completions.create(**kwargs)
        except (RateLimitError,APIStatusError) as error:
            if is_oversized_request(error):
                raise ContextTooLargeError(
                    "the request is over the model's per-minute input token budget,"
                    " so the conversation has to shrink before it can be sent again"
                ) from None
            if attempt == MAX_RETRIES - 1:
                if isinstance(error,RateLimitError):
                    raise RuntimeError("limit reached,wait and try again") from None
                raise RuntimeError(f"API call failed:{error}") from None
        except APIConnectionError:
            if attempt == MAX_RETRIES - 1:
                raise RuntimeError("API call failed:could not reach groq") from None

        delay = BASE_BACKOFF_SECONDS * (2**attempt)
        print(f"[yellow]API error,retrying in {delay:.0f}s (attempt:{attempt+1})[/]")
        time.sleep(delay)

    raise RuntimeError("Unreachable")

def stream_model(client,on_text,**kwargs):
    extra_body = dict(kwargs.pop("extra_body",None) or {})
    extra_body.setdefault("stream_options",{"include_usage":True})
    kwargs["extra_body"] = extra_body

    content,finish_reason,usage,slots = "",None,None,{}

    with call_model(client,stream=True,**kwargs) as stream:
        for chunk in stream:
            # usage arrives on a final chunk that carries no choices,so read it first
            if getattr(chunk,"usage",None):
                usage = chunk.usage

            if not chunk.choices:
                continue
            choice = chunk.choices[0]
            if choice.finish_reason:
                finish_reason = choice.finish_reason

            delta = choice.delta
            if delta.content:
                content += delta.content
                on_text(delta.content)

            for frag in delta.tool_calls or []:
                slot = slots.setdefault(frag.index,{"id":"","name":"","arguments":""})
                if frag.id:
                    slot["id"] = frag.id
                if frag.function:
                    slot["name"] += frag.function.name or ""
                    slot["arguments"] += frag.function.arguments or ""

    tool_calls = [ChatCompletionMessageToolCall(id=s["id"],type="function",function=Function(name=s["name"],arguments=s["arguments"])) for s in slots.values()] or None

    return ChatCompletionMessage(role="assistant",content=content or None,tool_calls=tool_calls), finish_reason, usage


def message_to_dict(message:ChatCompletionMessage):

    entry = {
        "role":"assistant",
        "content":message.content or ""
    }

    if message.tool_calls:
        entry["tool_calls"] = [
            {
                "id":call.id,
                "type":"function",
                "function":{
                    "name":call.function.name,
                    "arguments":call.function.arguments
                }
            }
            for call in message.tool_calls
        ]

    return entry


def format_process_result(result:subprocess.CompletedProcess)->str:
    parts = []

    if result.stdout:
        parts.append(result.stdout.rstrip())
    if result.stderr:
        parts.append(f"[stderr]:{result.stderr.rstrip()}")

    if not parts:
        return "command produced no output"

    return "\n".join(parts)

INPUT_COST_PER_MILLION = 0.80   # qwen/qwen3.8-27b
OUTPUT_COST_PER_MILLION = 4.00

class TokenCounter:

    def __init__(self)->None:
        self.input_token = 0
        self.output_token = 0
        self.turns = 0

    def add(self,usage):
        if usage is None:
            return
        self.input_token += getattr(usage,"prompt_tokens",0)
        self.output_token += getattr(usage,"completion_tokens",0)
        self.turns += 1

    @property
    def cost(self)->float:
        cost = ((self.input_token * INPUT_COST_PER_MILLION) + (self.output_token * OUTPUT_COST_PER_MILLION))/1000000
        return cost

    def report(self)->str:
        return f"[italic #56E39F]API calls:{self.turns}||input:{self.input_token}||output:{self.output_token}[/ italic #56E39F]"

COMMAND_HELP = """[#56E39F]
-> /exit : exit the from MintCode
-> /clear : Clear conversation history
-> /tokens : show API calls and token usage
[/]"""
class Status(Enum):
    QUIT = 1
    HANDLED = 2
    PROCEED = 3

def handle_slash_commands(user_input:str,messages:list[dict],token_counter_obj)->Status:

    if user_input == "/exit":
        return Status.QUIT

    if user_input == "/clear":
        messages[:] = [{"role":"system","content":SYSTEM_PROMPT}]
        print("[italic dim] conversation history cleared[/]")
        return Status.HANDLED


    if user_input == "/help":
        print(COMMAND_HELP)
        return Status.HANDLED

    if user_input == "/tokens":
        print(token_counter_obj.report())
        return Status.HANDLED



    return Status.PROCEED


CHARS_PER_TOKEN = 3.6
# the free tier caps a single request at 7000 input tokens per minute, and the
# system prompt plus tool schema already spends about 2050 of that before any
# history is sent, so the budget has to be sized against the limit and not
# against the model's much larger context window
CONTEXT_LIMIT_TOKENS = 7000
WARN_AT_FRACTION = 0.80

def estimate_tokens(messages:list[dict],tools:list[dict]|None=None)->int:

    tokens = 0
    for message in messages:
        tokens += int(len(str(message.get("content")or "")) / CHARS_PER_TOKEN)
        for call in message.get("tool_calls") or []:
            function = call.get("function",{})
            tokens += int((len(function.get("name","")) + len(function.get("arguments","") or "")) / CHARS_PER_TOKEN)

    # the schema is sent on every request but never appears in the history
    if tools:
        tokens += int(len(json.dumps(tools)) / CHARS_PER_TOKEN)

    return tokens

def check_context_budget(messages:list[dict],tools:list[dict]|None=None)->bool:
     tokens = estimate_tokens(messages=messages,tools=tools)
     fraction = tokens / CONTEXT_LIMIT_TOKENS

     return fraction >= WARN_AT_FRACTION


COMPACT_AT_TOKENS = int(CONTEXT_LIMIT_TOKENS * WARN_AT_FRACTION)

KEEP_RECENT_MESSAGES = 6
MAX_TRANSCRIPT_CHARS = 12000


def _drop_oldest_turns(body:list[dict])->list[dict]:
    """Remove the first turn, and any tool replies it produced.

    A tool message is only valid after the assistant call it answers, so the two
    have to be dropped together or the next request is malformed.
    """
    body = list(body)

    # a caller can hand over a window that starts on an orphan tool reply, and
    # dropping that alone is already enough progress to not also lose a turn
    orphans = 0
    while body and body[0]["role"] == "tool":
        body.pop(0)
        orphans += 1

    if orphans or not body:
        return body

    body.pop(0)

    while body and body[0]["role"] == "tool":
        body.pop(0)

    return body


def _trim_to_allowance(body:list[dict],allowance:int)->list[dict]:
    """Drop whole turns off the front of body until it fits the token allowance."""
    while body and estimate_tokens(messages=body) > allowance:
        trimmed = _drop_oldest_turns(body)
        if len(trimmed) == len(body):
            break
        body = trimmed

    return body


def compact_messages(messages:list[dict],client,model,tools=None,keep_recent:int=KEEP_RECENT_MESSAGES)->bool:
    """Shrink the history to fit the request budget. Returns True if it got smaller."""

    before = estimate_tokens(messages=messages,tools=tools)

    system_message = [message for message in messages if message["role"] == "system"]
    recent_messages = messages[-keep_recent:]
    while recent_messages and recent_messages[0]["role"] == "tool":
           recent_messages = recent_messages[1:]
    older_messages = messages[len(system_message):-keep_recent]

    if not older_messages:
        # nothing to summarize yet, so only the recent window has to give way
        allowance = COMPACT_AT_TOKENS - estimate_tokens(messages=system_message,tools=tools)
        trimmed = _trim_to_allowance(recent_messages,allowance)
        if len(trimmed) < len(recent_messages):
            messages[:] = system_message + trimmed
            print(f"[yellow]NOTE:[/]dropped {len(recent_messages) - len(trimmed)} old messages to stay inside the input budget.")
        return estimate_tokens(messages=messages,tools=tools) < before

    transcript_parts = []
    for message in older_messages:
        line = f"{message['role']}: {str(message.get('content') or '')[:2000]}"
        for call in message.get("tool_calls") or []:
            function = call.get("function",{})
            name = function.get("name","")
            arguments = str(function.get("arguments","") or "")[:2000]
            line += f" [called {name} {arguments}]"
        transcript_parts.append(line)

    # the summarizing call is itself billed against the same per-minute budget,
    # so the transcript is clipped to keep that request small enough to land
    transcript = "\n".join(transcript_parts)
    if len(transcript) > MAX_TRANSCRIPT_CHARS:
        head = MAX_TRANSCRIPT_CHARS // 3
        transcript = transcript[:head] + "\n[... middle of the conversation dropped ...]\n" + transcript[-(MAX_TRANSCRIPT_CHARS - head):]

    summary = client.chat.completions.create(
            model=model,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "Summarize this conversation between a user and a coding agent. "
                        "Keep: the user's goal, decisions made, files created or changed, "
                        "commands run and their results, and anything still unresolved. "
                        "Drop: exact code listings and tool output. Be under 400 words."
                    ),
                },
                {"role": "user", "content": transcript},
            ],
            reasoning_format="hidden",
            max_tokens=1000,
        ).choices[0].message.content

    summary_message = {
        "role":"user",
        "content":f"[Summary of earlier conversation]\n{summary}",
    }

    # the summary is the only record of the dropped turns, so it is charged for
    # first and the recent window absorbs whatever budget is left
    allowance = COMPACT_AT_TOKENS - estimate_tokens(messages=system_message + [summary_message],tools=tools)
    trimmed = _trim_to_allowance(recent_messages,allowance)

    messages[:] = system_message + [summary_message] + trimmed

    print(f"[yellow]NOTE:[/]compacted {len(older_messages)} older messages,kept {len(trimmed)} recent.")

    return estimate_tokens(messages=messages,tools=tools) < before
