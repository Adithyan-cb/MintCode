from dotenv import load_dotenv
from groq import Groq,APIConnectionError,APIStatusError,RateLimitError
import time
import subprocess
from rich import print
from system_prompt import SYSTEM_PROMPT
from groq.types.chat import ChatCompletionMessage


def call_model(client,**kwargs):
    MAX_RETRIES = 5
    BASE_BACKOFF_SECONDS = 1.0

    for attempt in range(MAX_RETRIES):
        try:
            return client.chat.completions.create(**kwargs)
        except RateLimitError:
            if attempt == MAX_RETRIES:
                raise RuntimeError("limit reached,wait and try again")
        except (APIConnectionError,APIStatusError) as error:
            if attempt == MAX_RETRIES:
                raise RuntimeError(f"API call failed:{error}") from None

        delay = BASE_BACKOFF_SECONDS * (2**attempt)
        print(f"[yellow]API error,retrying in {delay:.0f}s (attempt:{attempt+1})[/]")
        time.sleep(delay)

    raise RuntimeError("Unreachable")

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


COMMAND_HELP = """
-> /exit : exit the from MintCode
-> /clear : Clear conversation history
"""

def handle_slash_commands(user_input:str,messages:list[dict])->bool:

    if user_input == "/exit":
        return False

    if user_input == "/clear":
        messages[:] = [{"role":"system","content":SYSTEM_PROMPT}]
        #print("[italic yellow] conversation history is cleared[/]")
        return True


    if user_input == "/help":
        print(f"[yello]{COMMAND_HELP}[/]")
        return False

    return True
