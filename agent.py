from dotenv import load_dotenv
from untils import call_model,message_to_dict,handle_slash_commands
from rich import print
from groq import Groq
from system_prompt import SYSTEM_PROMPT
from tools import execute_tool_call, tools_schema

load_dotenv()


client = Groq()
MODEL = "qwen/qwen3.8-27b"
MAX_ITERATIONS = 10

messages = [{"role": "system", "content": SYSTEM_PROMPT}]


while True:
    reached_answer = False

    try:
        usr_input = input(">> user:")
    except (EOFError,KeyboardInterrupt):
        print("\n[dim]bye![/]")
        break
    if not usr_input:
        continue

    if not handle_slash_commands(user_input=usr_input,messages=messages):
        break
    messages.append({"role":"user","content":usr_input})

    for _ in range(MAX_ITERATIONS):
        try:
            response = call_model(client=client,model=MODEL,messages=messages,tools=tools_schema,reasoning_format="hidden",max_tokens=4096)
        except RuntimeError as error:
            print(f"[red]error:{e}[/red]")
            break

        choice = response.choices[0]
        message = choice.message
        messages.append(message_to_dict(message))

        if choice.finish_reason == "length" and message.tool_calls:
            messages.pop()
            messages.append(
                {
                    "role":"user",
                    "content":"your reply hit the output token limit before the tool call was complete.DO it again,but keep the tool call small"
                }
            )

        if not message.tool_calls:
            print(f"[#FF715B]>> AI:[/]{message.content}")
            reached_answer = True
            if choice.finish_reason == "length":
                print("[yello]NOTE:[/]the answer hit the token limit and is probably incomplete,ask me to continue.")
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
    if not reached_answer:
        print(f"[yellow] Stopped after {MAX_ITERATIONS} tool iterations wihtout a final answer.Rephrase or break the task into smaller steps[/]")

# for conv in  messages:
#     print("=="*10)
#     print(conv)
#     print("=="*10)
