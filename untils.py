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


def call_model(client,**kwargs):
    MAX_RETRIES = 5
    BASE_BACKOFF_SECONDS = 1.0

    for attempt in range(MAX_RETRIES):
        try:
            return client.chat.completions.create(**kwargs)
        except RateLimitError:
            if attempt == MAX_RETRIES - 1:
                raise RuntimeError("limit reached,wait and try again")
        except (APIConnectionError,APIStatusError) as error:
            if attempt == MAX_RETRIES - 1:
                raise RuntimeError(f"API call failed:{error}") from None

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
        return f"API calls:{self.turns}||input:{self.input_token}||output:{self.output_token}"

COMMAND_HELP = """
-> /exit : exit the from MintCode
-> /clear : Clear conversation history
"""
class Status(Enum):
    QUIT = 1
    HANDLED = 2
    PROCEED = 3

def handle_slash_commands(user_input:str,messages:list[dict],token_counter_obj)->Status:

    if user_input == "/exit":
        return Status.QUIT

    if user_input == "/clear":
        messages[:] = [{"role":"system","content":SYSTEM_PROMPT}]
        print("[dim] conversation history cleared[/]")
        return Status.HANDLED


    if user_input == "/help":
        print(f"[yellow]{COMMAND_HELP}[/]")
        return Status.HANDLED

    if user_input == "/tokens":
        print(token_counter_obj.report())
        return Status.HANDLED



    return Status.PROCEED


CHARS_PER_TOKEN = 3.6
CONTEXT_LIMIT_TOKENS = 131000
WARN_AT_FRACTION = 0.80

def estimate_tokens(messages:list[dict])->int:

    tokens = 0
    for message in messages:
        tokens += len(str(message.get("content")or "")) // 4
        for call in message.get("tool_calls") or []:
            function = call.get("function",{})
            tokens += len(function.get("name","")) + len(function.get("arguments","") or "")
    return tokens

def check_context_budget(messages:list[dict]):
     tokens = estimate_tokens(messages=messages)
     fraction = tokens / CONTEXT_LIMIT_TOKENS

     return fraction >= WARN_AT_FRACTION


KEEP_RECENT_MESSAGES = 12

def compact_messages(messages:list[dict],client,model):

    system_message = [message for message in messages if message["role"] == "system"]
    recent_messages = messages[-KEEP_RECENT_MESSAGES:]
    while recent_messages and recent_messages[0]["role"] == "tool":
           recent_messages = recent_messages[1:]
    older_messages = messages[len(system_message):-KEEP_RECENT_MESSAGES]

    if not older_messages:
        return messages

    transcript_parts = []
    for message in older_messages:
        line = f"{message['role']}: {str(message.get('content') or '')[:2000]}"
        for call in message.get("tool_calls") or []:
            function = call.get("function",{})
            name = function.get("name","")
            arguments = str(function.get("arguments","") or "")[:2000]
            line += f" [called {name} {arguments}]"
        transcript_parts.append(line)

    transcript = "\n".join(transcript_parts)

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

    messages[:] = (
           system_message
           + [
               {
                   "role": "user",
                   "content": f"[Summary of earlier conversation]\n{summary}",
               }
           ]
           + recent_messages
       )

    return messages
