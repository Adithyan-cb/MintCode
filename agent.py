from dotenv import load_dotenv
from untils import stream_model,message_to_dict,handle_slash_commands,TokenCounter,Status,check_context_budget,compact_messages
from rich import print
from rich.console import Console
from groq import Groq
from system_prompt import SYSTEM_PROMPT
from tools import execute_tool_call, tools_schema

load_dotenv()


client = Groq()
MODEL = "qwen/qwen3.8-27b"
MAX_ITERATIONS = 10

messages = [{"role": "system", "content": SYSTEM_PROMPT}]

console = Console()
token_counter = TokenCounter()


class StreamPrinter:
    """Prints deltas as they arrive,adding the '>> AI:' prefix only once text shows up."""

    def __init__(self,console):
        self.console = console
        self.started = False

    def __call__(self,delta):
        if not self.started:
            self.console.print("[#FF715B]>> AI:[/]",end="")
            self.started = True
        # markup/highlight off: a delta containing [brackets] is literal text,not a rich tag
        self.console.print(delta,end="",markup=False,highlight=False)

    def newline(self):
        if self.started:
            self.console.print()
            self.started = False

while True:
    reached_answer = False
    api_failed = False

    try:
        console.print("[bold blue]>> user:[/]")
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
            print(f"[red]error:{error}[/]")
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
                print(f"[#FF715B]>> AI:[/][dim](empty reply,finish_reason:{finish_reason})[/]")
            reached_answer = True
            if finish_reason == "length":
                print("[yellow]NOTE:[/]the answer hit the token limit and is probably incomplete,ask me to continue.")
            break


        for tool_call in message.tool_calls:
            print(f"=>[italic cyan]tool:{tool_call.function.name} [/italic cyan]")
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
        print(f"[yellow] Stopped after {MAX_ITERATIONS} tool iterations wihtout a final answer.Rephrase or break the task into smaller steps[/]")

# for conv in  messages:
#     print("=="*10)
#     print(conv)
#     print("=="*10)
