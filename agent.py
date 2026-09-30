import os
import sys

from dotenv import load_dotenv
from utils import stream_model,message_to_dict,handle_slash_commands,TokenCounter,Status,check_context_budget,compact_messages
from rich import print
from rich.console import Console
from groq import Groq
from system_prompt import SYSTEM_PROMPT
from setup_env import ensure_api_keys
from tools import execute_tool_call, tools_schema
from rich.markdown import Markdown
from rich.markup import escape
from rich.live import Live

load_dotenv()

ensure_api_keys()

client = Groq()
MODEL = "qwen/qwen3.8-27b"
MAX_ITERATIONS = 10

messages = [{"role": "system", "content": SYSTEM_PROMPT}]

console = Console()
token_counter = TokenCounter()

LOGO = (
    "███╗   ███╗██╗███╗   ██╗████████╗ ██████╗ ██████╗ ██████╗ ███████╗",
    "████╗ ████║██║████╗  ██║╚══██╔══╝██╔════╝██╔═══██╗██╔══██╗██╔════╝",
    "██╔████╔██║██║██╔██╗ ██║   ██║   ██║     ██║   ██║██║  ██║█████╗",
    "██║╚██╔╝██║██║██║╚██╗██║   ██║   ██║     ██║   ██║██║  ██║██╔══╝",
    "██║ ╚═╝ ██║██║██║ ╚████║   ██║   ╚██████╗╚██████╔╝██████╔╝███████╗",
    "╚═╝     ╚═╝╚═╝╚═╝  ╚═══╝   ╚═╝    ╚═════╝ ╚═════╝ ╚═════╝ ╚══════╝",
)


def clear_screen():
    # erase everything + home the cursor; old conhost ignores VT,so shell out there
    if os.name == "nt":
        os.system("cls")
    else:
        sys.stdout.write("\033[2J\033[H")
        sys.stdout.flush()


def print_logo():
    # rich would re-wrap the art to the console width,so write straight to stdout
    sys.stdout.write("\n".join(LOGO) + "\n\n")


def print_cwd():
    # the tools all resolve relative paths against the cwd,so make it visible
    console.print(f"[dim]working dir:[/] {escape(os.getcwd())}\n")

clear_screen()
print_logo()
print_cwd()

class StreamPrinter:
    """Buffers the deltas and renders them as Markdown, refreshed in place as text arrives.

    Rendering each delta on its own would re-parse partial Markdown on every token,so headings,
    lists and code fences come out broken. Buffering first keeps the output valid Markdown.
    """

    def __init__(self,console):
        self.console = console
        self.buffer = ""
        self.live = None

    def __call__(self,delta):
        self.buffer += delta
        if self.live is None:
            self.live = Live(Markdown(self.buffer),console=self.console,refresh_per_second=12)
            self.console.print("[bold #56E39F]>> AI:[/]")
            self.live.start()
        else:
            self.live.update(Markdown(self.buffer))

    def newline(self):
        if self.live is not None:
            self.live.stop()
            self.live = None
            self.console.print()
        self.buffer = ""

while True:
    reached_answer = False
    api_failed = False

    try:
        console.print("[bold #0E79B2]>> user:[/]")
        usr_input = input()
    except (EOFError,KeyboardInterrupt):
        print("\n[dim]bye![/]")
        break
    if not usr_input:
        continue

    status = handle_slash_commands(user_input=usr_input,messages=messages,token_counter_obj=token_counter)

    if status == Status.QUIT:
        break

    if status == Status.HANDLED:
        continue

    messages.append({"role":"user","content":usr_input})

    if check_context_budget(messages=messages):
         compact_messages(messages=messages,client=client,model=MODEL)

    for _ in range(MAX_ITERATIONS):
        on_text = StreamPrinter(console)

        try:
            message,finish_reason,usage = stream_model(client=client,on_text=on_text,model=MODEL,messages=messages,tools=tools_schema,reasoning_format="hidden",max_tokens=4096,top_p=0.80,parallel_tool_calls=True,tool_choice="auto")
            token_counter.add(usage)
        except RuntimeError as error:
            on_text.newline()
            print(f"[italic red]error:{error}[/ italic red]")
            api_failed = True
            break

        on_text.newline()
        messages.append(message_to_dict(message))

        if finish_reason == "length" and message.tool_calls:
            messages.pop()
            messages.append(
                {
                    "role":"user",
                    "content":"your reply hit the output token limit before the tool call was complete.DO it again,but keep the tool call small"
                }
            )
            # the tool call args are truncated,so drop it and let the model retry
            print("[yellow]NOTE:[/]tool call was cut off by the token limit,retrying with a smaller one.")
            continue

        if not message.tool_calls:
            if not message.content:
                console.print("[bold #56E39F]>> AI:[/]")
                print(f"[#FF715B]{finish_reason})[/]")
            reached_answer = True
            if finish_reason == "length":
                print("[yellow]NOTE:[/]the answer hit the token limit and is probably incomplete,ask me to continue.")
            break

        for tool_call in message.tool_calls:
            if tool_call.function.name == "glob" or tool_call.function.name == "grep":
                print(f"[italic #56E39F]♦ tool:{tool_call.function.name} [/italic #56E39F]\n")
            else:
                print(f"[italic #56E39F]◘ tool:{tool_call.function.name} [/italic #56E39F]\n")
            function_response = execute_tool_call(tool_call)

            messages.append({
                "role":"tool",
                "tool_call_id":tool_call.id,
                "name":tool_call.function.name,
                "content":str(function_response)
            })

        if check_context_budget(messages=messages):
            compact_messages(messages=messages,client=client,model=MODEL)

    if not reached_answer and not api_failed:
        print(f"[italic yellow] Stopped after {MAX_ITERATIONS} tool iterations wihtout a final answer.Rephrase or break the task into smaller steps[/ italic yellow]")
