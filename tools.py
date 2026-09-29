import json
import subprocess
from untils import format_process_result
import os
from dotenv import load_dotenv
from tavily import TavilyClient
from tavily.errors import (
    BadRequestError,
    ForbiddenError,
    InvalidAPIKeyError,
    TavilyKeylessLimitError,
    UsageLimitExceededError,
)
from tavily.errors import TimeoutError as TavilyTimeoutError

load_dotenv()

COMMAND_TIMEOUT_SECONDS = 30

######### TOOLS SCHEMA ##############
tools_schema = [
{
    "type":"function",
    "function":{
        "name":"web_search",
        "description":"Search the public web for current, recent, time-sensitive, or externally verifiable information. Use for breaking news, current versions or status, changing documentation, security advisories, and questions where built-in model knowledge may be stale. Do not use for basic reasoning or stable knowledge that does not require current sources.",
        "parameters":{
            "type":"object",
            "properties":{
                "query":{
                    "type":"string",
                    "description":"A focused search query. Include the relevant date, location, product, or version when needed to disambiguate the request."
                }
            },
            "required":["query"]
        }
    }
},

{
    "type":"function",
    "function":{
        "name":"write_file",
        "description":"used to write contents to file in the specified location",
        "parameters":{
            "type":"object",
            "properties":{
                "filename_with_path":{
                    "type":"string",
                    "description":"path of the file along with file name separated by '\'"
                },
                "content":{
                    "type":"string",
                    "description":"the content that is to be written in the file"
                }

            },
            "required":["filename_with_path","content"]
        }
    }
},

{
    "type":"function",
    "function":{
        "name":"read_file",
        "description":"used to read contents from the specified file",
        "parameters":{
            "type":"object",
            "properties":{
                "filename_with_path":{
                    "type":"string",
                    "description":"path of the file along with file name separated by '\'"
                }

            },
            "required":["filename_with_path"]
        }
    }
},

{
    "type":"function",
    "function":{
        "name":"run_command",
        "description":"Run a program as an argument list without a shell. Use for CLI tools, scripts, and tests. Interactive programs receive no terminal input, so provide their expected input through stdin. Commands time out after 30 seconds. Do not use shell syntax such as pipes, redirection, or command chaining.",
        "parameters":{
            "type":"object",
            "properties":{
                "command":{
                    "type":"array",
                    "items":{"type":"string"},
                    "minItems":1,
                    "description":"The command to run as an argument list. The first item is the program and the remaining items are its arguments, for example: ['ls', '-la']."
                },
                "stdin":{
                    "type":"string",
                    "description":"Optional text piped to the program's standard input. Use newline characters to provide multiple input lines, for example: '2\\n3'."
                }

            },
            "required":["command"]
        }
    }
},

]



######### TOOLS ##########

def web_search(query:str):
    client = TavilyClient()
    try:
        response = client.search(
            query=query,
            search_depth="basic"
        )
        return response
    except (BadRequestError,ForbiddenError,InvalidAPIKeyError,OSError,TavilyKeylessLimitError,TavilyTimeoutError,UsageLimitExceededError,) as error:
        return f"Error: {error}"

def write_file(filename_with_path:str,content:str):
    path = os.path.abspath(os.path.expanduser(filename_with_path))
    try:
        parent = os.path.dirname(path)
        if parent:
            os.makedirs(parent,exist_ok=True)
        with open(path,"w",encoding="utf-8") as file:
            file.write(content)
    except PermissionError:
        return f"Permission denied:Cannot write to the location:{filename_with_path}"
    except OSError as e:
       return f"failed to write to file:{e}"

    line_count = content.count("\n") + 1 if content else 0
    return f"wrote {len(content)} , {line_count} lines to {path}"

def read_file(filename_with_path:str):
    try:
        with open(filename_with_path,"r",encoding="utf-8") as file:
            return file.read()
    except FileNotFoundError:
        return f"file:{filename_with_path}, doesn't exist"
    except IsADirectoryError:
        return f"Error:{filename_with_path} is a directory,not a file"
    except PermissionError:
        return f"Don't have permission to read the file:{filename_with_path}"
    except UnicodeDecodeError:
        return f"Error:{filename_with_path} is not a text file,it's probably a binary file,Do not try to read it"
    except OSError as e:
        return f"An unexpected OS error occured:{e}"

def run_command(command:list[str],stdin:str | None = None):
    if (
        not isinstance(command, list)
        or not command
        or not all(isinstance(argument, str) and argument for argument in command)
    ):
        return "Error: command must be a non-empty list of non-empty strings."

    if stdin is not None and not isinstance(stdin, str):
        return "Error: stdin must be a string."

    try:
        result = subprocess.run(
            command,
            input="" if stdin is None else stdin,
            capture_output=True,
            check=True,
            shell=False,
            text=True,
            timeout=COMMAND_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired:
        return f"Error: command timed out after {COMMAND_TIMEOUT_SECONDS} seconds."
    except subprocess.CalledProcessError as error:
        details = error.stderr.strip() or error.stdout.strip() or "no output"
        return f"Command failed with exit code {error.returncode}: {details}"
    except FileNotFoundError:
        return f"Error: command not found: {command[0]}"
    except OSError as error:
        return f"Error running command: {error}"

    return format_process_result(result)

available_tools = {
    "web_search":web_search,
    "write_file":write_file,
    "read_file":read_file,
    "run_command":run_command
}


########### TOOL EXECUTION CODE ################
def execute_tool_call(tool_call)-> str:
    """parse and execute a single tool"""
    function_name = tool_call.function.name
    function_to_call = available_tools.get(function_name)

    if function_to_call is None:
        return f"Error:unknown tool[{function_name}], available tools:{','.join(available_tools)}"


    try:
        function_args = json.loads(tool_call.function.arguments or "{}")
    except json.JSONDecodeError as error:
        return f"Error:arguments for function:{function_name} is not vaild JSON({error}), please retry with vaild JSON object"

    if not isinstance(function_args,dict):
        return f"Error:arguments for {function_name} should be a JSON object"

    try:
        result = function_to_call(**function_args)
    except TypeError as error:
        return f"Error:wrong argument for {function_name} , error:{error}"
    except Exception as error:
        return f"Error:an error occured while running {function_name} , error type:{type(error).__name__} , error:{error}"

    return str(result)
