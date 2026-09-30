import json
import fnmatch
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
DEFAULT_READ_LINES = 400
MAX_READ_LINES = 2000

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
    "type": "function",
    "function": {
        "name": "read_file",
        "description": (
            "Read a slice of a text file, with line numbers. Use offset and limit to read "
            "only the part you need instead of the whole file. Read a file before editing it."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "filename_with_path": {"type": "string", "description": "Path to the file."},
                "offset": {
                    "type": "integer",
                    "description": "Line number to start from (1-indexed). Defaults to 1.",
                },
                "limit": {
                    "type": "integer",
                    "description": f"How many lines to read. Defaults to {DEFAULT_READ_LINES}.",
                },
            },
            "required": ["filename_with_path"],
        },
    },
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

{
    "type": "function",
    "function": {
        "name": "edit_file",
        "description": (
            "Replace an exact snippet in an existing file. This is the correct tool for "
            "changing code in a file that already exists - prefer it over write_file, "
            "because it only sends the text being changed instead of the whole file. "
            "The old_string must match the file exactly, including indentation, and must "
            "be unique unless replace_all is true. Always read the file before editing it."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "filename_with_path": {
                    "type": "string",
                    "description": "Path to the file to edit.",
                },
                "old_string": {
                    "type": "string",
                    "description": (
                        "The exact existing text to replace, copied verbatim from the file "
                        "including indentation. Must be unique in the file."
                    ),
                },
                "new_string": {
                    "type": "string",
                    "description": "The text to put in its place.",
                },
                "replace_all": {
                    "type": "boolean",
                    "description": (
                        "Replace every occurrence instead of just one. Defaults to false."
                    ),
                },
            },
            "required": ["filename_with_path", "old_string", "new_string"],
        },
    },
},

{
    "type": "function",
    "function": {
        "name": "glob",
        "description": (
            "Find files by name pattern, for example '*.py' or 'test_*.md'. "
            "Use this before guessing a path, so you read the file that actually exists."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "pattern": {"type": "string", "description": "Name pattern, e.g. '*.py'."},
                "path": {
                    "type": "string",
                    "description": "Directory to search. Defaults to the current directory.",
                },
            },
            "required": ["pattern"],
        },
    },
},


{
    "type": "function",
    "function": {
        "name": "grep",
        "description": (
            "Search inside files for a text pattern and return matching lines with file "
            "names and line numbers. Use this to find where something is defined or used."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "pattern": {"type": "string", "description": "Text to search for."},
                "path": {
                    "type": "string",
                    "description": "Directory to search. Defaults to the current directory.",
                },
                "glob_pattern": {
                    "type": "string",
                    "description": "Only search files matching this name pattern. e.g. '*.py'. Defaults to '*'.",
                },
                "max_results": {
                    "type": "integer",
                    "description": "Stop after this many matches. Defaults to 60.",
                },
            },
            "required": ["pattern"],
        },
    },
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


def read_file(filename_with_path: str,offset: int = 1,limit: int = DEFAULT_READ_LINES,) -> str:
    """Read a slice of a text file, with line numbers. 1-indexed."""
    if offset < 1:
        return "Error: offset is 1-indexed and must be 1 or greater."

    limit = max(1, min(limit, MAX_READ_LINES))

    try:
        with open(filename_with_path, "r", encoding="utf-8") as file:
            lines = file.readlines()
    except FileNotFoundError:
        return f"Error: file '{filename_with_path}' does not exist."
    except IsADirectoryError:
        return f"Error: '{filename_with_path}' is a directory, not a file."
    except PermissionError:
        return f"Error: permission denied reading '{filename_with_path}'."
    except UnicodeDecodeError:
        return f"Error: '{filename_with_path}' is not a UTF-8 text file."
    except OSError as error:
        return f"Error: could not read '{filename_with_path}': {error}"

    total = len(lines)
    if offset > total:
        return f"Error: offset {offset} is past the end of '{filename_with_path}' ({total} lines)."

    selected = lines[offset - 1 : offset - 1 + limit]
    body = "".join(
        f"{number:>6}\t{line}"
        for number, line in enumerate(selected, start=offset)
    )

    end = offset - 1 + len(selected)
    header = f"{filename_with_path} (lines {offset}-{end} of {total})"
    footer = "" if end >= total else f"\n... {total - end} more lines. Read again with offset={end + 1}."

    return f"{header}\n{body.rstrip()}\n{footer}".rstrip()

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


def edit_file(filename_with_path: str, old_string: str, new_string: str, replace_all: bool = False):
    """Replace an exact snippet in a file.

    Use this instead of write_file for changes to existing files. It only
    sends the part being changed, which is far cheaper than rewriting the
    whole file.
    """
    if old_string == new_string:
        return "Error: old_string and new_string are identical. Nothing to do."

    if not old_string:
        return (
            "Error: old_string is empty. To create a new file use write_file. "
            "To append, include the last line of the file in old_string."
        )

    try:
        with open(filename_with_path, "r", encoding="utf-8") as file:
            content = file.read()
    except FileNotFoundError:
        return f"Error: file '{filename_with_path}' does not exist. Use write_file to create it."
    except UnicodeDecodeError:
        return f"Error: '{filename_with_path}' is not a UTF-8 text file."
    except OSError as error:
        return f"Error: could not read '{filename_with_path}': {error}"

    occurrences = content.count(old_string)
    if occurrences == 0:
        return (
            f"Error: old_string not found in '{filename_with_path}'. "
            "Read the file again and copy the exact text, including indentation."
        )

    if occurrences > 1 and not replace_all:
        return (
            f"Error: old_string appears {occurrences} times in '{filename_with_path}'. "
            "Include more surrounding context to make it unique, or set replace_all=true."
        )

    updated = (
        content.replace(old_string, new_string, -1)
        if replace_all
        else content.replace(old_string, new_string, 1)
    )

    try:
        with open(filename_with_path, "w", encoding="utf-8") as file:
            file.write(updated)
    except OSError as error:
        return f"Error: could not write to '{filename_with_path}': {error}"

    change = "replacements" if (replace_all and occurrences > 1) else "replacement"
    return f"Applied {occurrences} {change} in {filename_with_path}."

def glob(pattern:str,path:str = '.'):
    """Find files by name pattern, like *.py or test_*.md."""
    MAX_RESULTS = 100

    matches = []

    root = os.path.abspath(os.path.expanduser(path))
    SKIP = {".git", "__pycache__", "node_modules", ".venv", ".ruff_cache", "dist", "build"}
    for dirpath,dirnames,filenames in os.walk(root):

        dirnames[:] = [dirname for dirname in dirnames if dirname not in SKIP]

        for filename in filenames:
            if fnmatch.fnmatch(filename,pattern):
                full_path = os.path.join(dirpath,filename)
                matches.append(os.path.relpath(full_path))

                if len(matches) >= MAX_RESULTS:
                    result = "\n".join(matches)
                    return f"{result} | results limit reached({MAX_RESULTS}),narrow the pattern"

    if not matches:
        return f"no match found fot pattern:{pattern} under {path}"

    return "\n".join(sorted(matches))

def grep(pattern: str, path: str = ".", glob_pattern: str = "*", max_results: int = 60) -> str:
    """Search file contents for a text pattern. Returns matching lines with line numbers."""
    root = os.path.abspath(os.path.expanduser(path))

    if not os.path.isdir(root):
        return f"Error: '{path}' is not a directory."

    hits = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [
            d
            for d in dirnames
            if d not in {".git", "__pycache__", "node_modules", ".venv", ".ruff_cache", "dist", "build"}
        ]
        for filename in sorted(filenames):
            if not fnmatch.fnmatch(filename, glob_pattern):
                continue
            full = os.path.join(dirpath, filename)
            try:
                with open(full, "r", encoding="utf-8") as file:
                    for number, line in enumerate(file, start=1):
                        if pattern in line:
                            relative = os.path.relpath(full, root)
                            hits.append(f"{relative}:{number}: {line.rstrip()}")
                            if len(hits) >= max_results:
                                return "\n".join(hits) + f"\n(stopped at {max_results} matches)"
            except (UnicodeDecodeError, OSError):
                continue  # skip binary and unreadable files silently

    if not hits:
        return f"No matches for '{pattern}' under '{path}'."

    return "\n".join(hits)



available_tools = {
    "web_search": web_search,
    "read_file": read_file,
    "write_file": write_file,
    "edit_file": edit_file,
    "run_command": run_command,
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
