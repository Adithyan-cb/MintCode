import os
import sys
from pathlib import Path

from dotenv import dotenv_values, set_key
from groq import (
    APIConnectionError,
    APIStatusError,
    APITimeoutError,
    AuthenticationError,
    Groq,
    RateLimitError,
)
from rich.console import Console
from tavily import TavilyClient
from tavily.errors import (
    BadRequestError,
    ForbiddenError,
    InvalidAPIKeyError,
    TavilyKeylessLimitError,
    UsageLimitExceededError,
)
from tavily.errors import TimeoutError as TavilyTimeoutError

ENV_PATH = Path(__file__).resolve().parent / ".env"

GROQ_KEY_URL = "https://console.groq.com/keys"
TAVILY_KEY_URL = "https://app.tavily.com/home"

console = Console()


def mask_secret(value:str) -> str:
    """Show just enough of a key to confirm it is the right one."""
    if len(value) < 12:
        return "*" * len(value)
    return f"{value[:4]}...{value[-4:]}"


def get_configured_keys() -> dict:
    """Read the project .env without mutating the process environment."""
    if not ENV_PATH.exists():
        return {}
    return {key: value for key, value in dotenv_values(ENV_PATH).items() if value}


def current_key(name:str, configured:dict) -> str | None:
    """A key already exported in the shell wins over the one in .env."""
    return os.environ.get(name) or configured.get(name)


def validate_groq_key(key:str) -> bool:
    """Confirm the key works. models.list() is free and needs no tokens."""
    try:
        Groq(api_key=key).models.list()
    except AuthenticationError:
        console.print("[italic red]invalid api key[/ italic red]")
        return False
    except (APIConnectionError, APITimeoutError, RateLimitError, APIStatusError) as error:
        console.print(f"[yellow]could not reach groq ({type(error).__name__}),saving the key anyway[/]")
    return True


def validate_tavily_key(key:str) -> bool:
    try:
        TavilyClient(api_key=key).search(query="ping", max_results=1)
    except InvalidAPIKeyError:
        console.print("[italic red]invalid api key[/ italic red]")
        return False
    except (
        BadRequestError,
        ForbiddenError,
        OSError,
        TavilyKeylessLimitError,
        TavilyTimeoutError,
        UsageLimitExceededError,
    ) as error:
        console.print(f"[yellow]could not reach tavily ({type(error).__name__}),saving the key anyway[/]")
    return True


def save_key(name:str, value:str):
    """Persist to .env and to this process, so the client works without a restart."""
    set_key(str(ENV_PATH), name, value)
    os.environ.setdefault(name, value)


def abort_setup():
    console.print("\n[dim]no api key was provided,exiting.[/]")
    sys.exit(1)


def read_answer(prompt:str) -> str:
    """Read a non-secret answer, so a y/N is never echoed as asterisks."""
    try:
        return console.input(prompt).strip()
    except (EOFError, KeyboardInterrupt):
        abort_setup()


def read_secret(prompt:str) -> str:
    try:
        return console.input(prompt, password=True).strip()
    except (EOFError, KeyboardInterrupt):
        abort_setup()


def collect_key(name:str, validator, allow_skip:bool) -> str | None:
    while True:
        key = read_secret(f"[bold #56E39F]{name} >[/] ")
        if not key:
            if allow_skip:
                return None
            console.print("[yellow]a key is required,try again[/]")
            continue
        if validator(key):
            return key


def prompt_for_groq_key() -> str:
    console.print("[bold #0E79B2]A groq api key[/] is required to run MintCode.")
    console.print(f"[dim]get one at {GROQ_KEY_URL}[/]")
    return collect_key("GROQ_API_KEY", validate_groq_key, allow_skip=False)


def prompt_for_tavily_key() -> str | None:
    console.print("[bold #0E79B2]A tavily api key[/] is optional: it powers the web_search tool")
    console.print(f"[dim]get one at {TAVILY_KEY_URL}[/]")

    answer = read_answer("[bold #56E39F]add TAVILY_API_KEY now?[/] [y/N] ").lower()
    if answer not in ("y", "yes"):
        return None

    return collect_key("TAVILY_API_KEY", validate_tavily_key, allow_skip=True)


def ensure_api_keys():
    """Make sure a usable key exists in .env, asking for one only when it is missing."""
    first_run = not ENV_PATH.exists()
    configured = get_configured_keys()

    groq_key = current_key("GROQ_API_KEY", configured)
    if groq_key:
        os.environ.setdefault("GROQ_API_KEY", groq_key)
        console.print(f"[dim]using GROQ_API_KEY {mask_secret(groq_key)}[/]")
    else:
        groq_key = prompt_for_groq_key()
        save_key("GROQ_API_KEY", groq_key)
        console.print(f"[dim #56E39F]saved GROQ_API_KEY {mask_secret(groq_key)} to {ENV_PATH.name}[/]")

    tavily_key = current_key("TAVILY_API_KEY", configured)
    if tavily_key:
        os.environ.setdefault("TAVILY_API_KEY", tavily_key)
        console.print(f"[dim]using TAVILY_API_KEY {mask_secret(tavily_key)}[/]")
        return

    # only offered once, so declining does not mean answering again on every launch
    if not first_run:
        return

    tavily_key = prompt_for_tavily_key()
    if tavily_key is None:
        console.print("[dim]skipping TAVILY_API_KEY,web_search will be unavailable[/]")
        return

    save_key("TAVILY_API_KEY", tavily_key)
    console.print(f"[dim #56E39F]saved TAVILY_API_KEY {mask_secret(tavily_key)} to {ENV_PATH.name}[/]")
