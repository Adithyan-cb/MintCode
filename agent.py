from dotenv import load_dotenv
from groq import Groq
from rich import print
from system_prompt import SYSTEM_PROMPT
from tools import execute_tool_call, tools_schema

load_dotenv()

client = Groq()

MODEL = "qwen/qwen3.8-27b"
MAX_ITERATIONS = 10

messages = [{"role": "system", "content": SYSTEM_PROMPT}]

while True:
    usr_input = input("user:")
    if usr_input == "/exit":
        break
    messages.append({"role":"user","content":usr_input})

    for _ in range(MAX_ITERATIONS):
        response = client.chat.completions.create(
            model=MODEL,
            messages=messages,
            tools=tools_schema
        )

        message = response.choices[0].message
        messages.append(message)

        if not message.tool_calls:
            print(f"[#FF715B]AI:[/]{message.content}")
            break

        for tool_call in message.tool_calls:
            print(f"[italic cyan]tool:{tool_call.function.name} [/italic cyan]")
            function_response = execute_tool_call(tool_call)

            messages.append({
                "role":"tool",
                "tool_call_id":tool_call.id,
                "name":tool_call.function.name,
                "content":str(function_response)
            })
    else:
        print(f"AI:Stopped after {MAX_ITERATIONS} iterations.")


# for conv in  messages:
#     print("=="*10)
#     print(conv)
#     print("=="*10)
