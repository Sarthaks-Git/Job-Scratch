"""Terminal UI used by the agents (rich). Tests swap in a scripted fake with the same methods."""

from __future__ import annotations

import sys
from typing import Protocol

from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from rich.prompt import Confirm, Prompt


class UI(Protocol):
    def begin_thinking(self, label: str = "Thinking") -> None: ...
    def stop_thinking(self) -> None: ...
    def agent_text(self, chunk: str) -> None: ...
    def end_agent_text(self) -> None: ...
    def tool_event(self, text: str) -> None: ...
    def info(self, msg: str) -> None: ...
    def warn(self, msg: str) -> None: ...
    def ask(self, question: str) -> str: ...
    def confirm(self, prompt: str, default: bool = False) -> bool: ...
    def review_submission(self, title: str, summary: str) -> tuple[bool, str]: ...
    def wait_for_enter(self, msg: str) -> None: ...


class ConsoleUI:
    def __init__(self, console: Console | None = None):
        self.console = console or Console()
        self._status = None
        self._mid_text = False

    def begin_thinking(self, label: str = "Thinking") -> None:
        self.stop_thinking()
        self._status = self.console.status(f"[dim]{label}…[/dim]", spinner="dots")
        self._status.start()

    def stop_thinking(self) -> None:
        if self._status is not None:
            self._status.stop()
            self._status = None

    def agent_text(self, chunk: str) -> None:
        self.stop_thinking()
        sys.stdout.write(chunk)
        sys.stdout.flush()
        self._mid_text = True

    def end_agent_text(self) -> None:
        self.stop_thinking()
        if self._mid_text:
            sys.stdout.write("\n")
            sys.stdout.flush()
            self._mid_text = False

    def tool_event(self, text: str) -> None:
        self.end_agent_text()
        self.console.print(f"[dim]  → {text}[/dim]")

    def info(self, msg: str) -> None:
        self.end_agent_text()
        self.console.print(msg)

    def warn(self, msg: str) -> None:
        self.end_agent_text()
        self.console.print(f"[yellow]{msg}[/yellow]")

    def markdown(self, text: str) -> None:
        self.end_agent_text()
        self.console.print(Markdown(text))

    def ask(self, question: str) -> str:
        self.end_agent_text()
        self.console.print(Panel(question, title="The agent needs your input", border_style="cyan"))
        return Prompt.ask("[cyan]Your answer[/cyan] (or 'skip' to abandon this application)")

    def confirm(self, prompt: str, default: bool = False) -> bool:
        self.end_agent_text()
        return Confirm.ask(prompt, default=default)

    def review_submission(self, title: str, summary: str) -> tuple[bool, str]:
        self.end_agent_text()
        self.console.print(Panel(summary, title=f"Ready to submit: {title}", border_style="green"))
        self.console.print("Check the browser window too. Type [bold]y[/bold] to submit, "
                           "[bold]n[/bold] to cancel, or type a correction for the agent to make first.")
        answer = Prompt.ask("[green]Submit?[/green]").strip()
        if answer.lower() in {"y", "yes", "submit", "ok"}:
            return True, ""
        if answer.lower() in {"n", "no", "cancel", "skip"}:
            return False, "User declined to submit this application."
        return False, f"User wants changes before submitting: {answer}"

    def wait_for_enter(self, msg: str) -> None:
        self.end_agent_text()
        Prompt.ask(f"[cyan]{msg}[/cyan] Press Enter when done", default="", show_default=False)
