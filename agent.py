import os
from dotenv import load_dotenv
from google import genai
from google.genai import types
from rich.console import Console
from rich.panel import Panel
from rich.markdown import Markdown

# Import the 4 hands we built in tools.py
from tools import list_files, read_file, write_file, run_command

load_dotenv()
console = Console()

# Fast failover client (avoids 30-second freezes if a model has a 503 spike)
client = genai.Client(
    api_key=os.getenv("GEMINI_API_KEY"),
    http_options=types.HttpOptions(retry_options=types.HttpRetryOptions(attempts=1))
)

MODELS = ["gemini-3.5-flash-lite", "gemini-3.8-flash", "gemini-3.7-flash", "gemini-3.6-flash"]

# Map tool names (strings) to actual Python functions
TOOL_MAP = {
    "list_files": list_files,
    "read_file": read_file,
    "write_file": write_file,
    "run_command": run_command,
}

SYSTEM_PROMPT = """You are an autonomous AI coding agent working inside a sandboxed 'workspace' directory.
You have tools to list files, read files, write files, and run terminal commands.
Follow this workflow strictly:
1. Always inspect existing files first if needed.
2. Write clean, modular, well-documented code.
3. Always test your code using `run_command` to verify there are no syntax or runtime errors.
4. If a command returns a STDERR traceback, read the error, fix the code using `write_file`, and re-test until it passes.
5. Once verified, summarize what you built clearly for the user."""

def call_gemini(messages):
    """Calls Gemini with automatic model fallback and manual ReAct loop control."""
    config = types.GenerateContentConfig(
        system_instruction=SYSTEM_PROMPT,
        tools=[list_files, read_file, write_file, run_command],
        # Disable SDK's hidden AFC so our explicit ReAct loop controls & logs every step
        automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
        temperature=0.2,
    )
    for model_name in MODELS:
        try:
            return client.models.generate_content(
                model=model_name,
                contents=messages,
                config=config,
            )
        except Exception:
            console.print(f"[yellow]Model {model_name} busy, switching to backup...[/yellow]")
    raise RuntimeError("All Gemini models are currently unavailable. Please try again in a minute.")

def run_agent(user_task: str, messages: list, max_steps: int = 15):
    """The core ReAct (Reason + Act) autonomous execution loop."""
    messages.append(types.Content(role="user", parts=[types.Part.from_text(text=user_task)]))

    for step in range(1, max_steps + 1):
        console.print(f"\n[bold cyan]--- Step {step}/{max_steps}: Agent Thinking ---[/bold cyan]")
        response = call_gemini(messages)

        # Save the model's response (text or tool call request) into memory
        model_content = response.candidates[0].content
        messages.append(model_content)

        # Check if Gemini wants to call any tools
        if response.function_calls:
            tool_response_parts = []

            for call in response.function_calls:
                func_name = call.name
                func_args = dict(call.args)
                console.print(f"[bold magenta]🔧 Tool Call:[/bold magenta] [bold]{func_name}[/bold]({func_args})")

                # Execute the matching Python function from tools.py
                if func_name in TOOL_MAP:
                    result = TOOL_MAP[func_name](**func_args)
                else:
                    result = f"Error: Unknown tool '{func_name}'"

                # Show a short preview of what the tool returned
                preview = result if len(result) < 300 else result[:300] + "\n... [truncated]"
                console.print(Panel(preview, title=f"Result from {func_name}", border_style="dim"))

                # Package the result so Gemini can read it on the next loop
                tool_response_parts.append(
                    types.Part.from_function_response(
                        name=func_name,
                        response={"result": result},
                    )
                )

            # Feed all tool outputs back into conversation memory
            messages.append(types.Content(role="user", parts=tool_response_parts))
        else:
            # No more tool calls! The agent is finished and has a final answer.
            console.print(Panel(Markdown(response.text), title="✅ Agent Finished", border_style="green"))
            return

    console.print("[bold red]⚠️ Max step limit reached! Stopping loop for safety.[/bold red]")


def main():
    console.print(Panel.fit(
        "[bold green]AIVAR Personal AI Coding Agent[/bold green]\n"
        "Type your coding task below (or type [bold]exit[/bold] to quit).",
        border_style="green"
    ))

    # Conversation memory persists across multiple prompts in one session
    messages = []

    while True:
        user_input = console.input("\n[bold blue]You > [/bold blue]").strip()
        if not user_input:
            continue
        if user_input.lower() in {"exit", "quit"}:
            console.print("[dim]Goodbye![/dim]")
            break

        run_agent(user_input, messages)


if __name__ == "__main__":
    main()