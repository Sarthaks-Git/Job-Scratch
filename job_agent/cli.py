"""`job-agent`: chat with your job-search agent in the terminal."""

from __future__ import annotations

import argparse
import logging
import os
import shlex
import sys

from rich.console import Console
from rich.table import Table

from . import __version__
from .agent import JobAgent, RefusalError
from .browser import LOGIN_URLS, Browser, BrowserError
from .config import ALL_SOURCES, Settings, data_dir
from .db import Store
from .llm import LLM, LLMError, friendly_api_error
from .ui import ConsoleUI

HELP = """[bold]Just type to chat with the agent.[/bold] For example:
  "My resume is at ~/Documents/resume.pdf"
  "Find me data analyst jobs in Pune or remote, fresher level"
  "Show details for 12" · "Apply to 12 and 15"

[bold]Commands[/bold]
  /jobs \\[n]          top-ranked jobs (no AI call)
  /job <id>           one job's details
  /open <id>          open a job in the browser
  /apps               application history
  /profile, /prefs    what the agent knows about you
  /answers            saved answers for application forms
  /companies          company career pages searched every time
  /login <site|url>   log in once in the agent's browser ({sites})
  /settings           show settings · /set <key> <value> to change one
  /reset              start a fresh conversation (your data is kept)
  /quit               exit"""


class App:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.console = Console()
        self.ui = ConsoleUI(self.console)
        self.store = Store(data_dir() / "jobs.db")
        self.llm = LLM(settings.model)
        self._browser: Browser | None = None
        self.agent = JobAgent(settings=settings, store=self.store, llm=self.llm, ui=self.ui,
                              browser_factory=self.browser)

    def browser(self) -> Browser:
        if self._browser is None:
            self._browser = Browser(
                data_dir() / "browser-profile",
                headless=self.settings.headless,
                slow_mo_ms=self.settings.slow_mo_ms,
                channel=self.settings.browser_channel,
                executable_path=self.settings.browser_executable,
            )
        self._browser.start()
        return self._browser

    def close(self) -> None:
        if self._browser is not None:
            try:
                self._browser.close()
            except Exception:
                pass

    # ------------------------------------------------------------- chat ---
    def chat(self, text: str) -> None:
        try:
            self.agent.send(text)
        except RefusalError:
            pass
        except KeyboardInterrupt:
            self.ui.warn("\nInterrupted. (The last message was discarded.)")
        except LLMError as e:
            self.ui.warn(str(e))
        except BrowserError as e:
            self.ui.warn(f"Browser problem: {e}")
        except Exception as e:  # API errors and anything unexpected: show, keep the session alive
            logging.getLogger(__name__).exception("turn failed")
            self.ui.warn(friendly_api_error(e))

    def run(self) -> None:
        self.console.print(f"[bold cyan]Job Agent[/bold cyan] v{__version__} · India job search · "
                           f"data in {data_dir()} · type /help")
        if not self.settings.auto_submit:
            self.console.print("[dim]You will be asked to approve every application before it is submitted.[/dim]")
        self.chat("[Session started. Greet me briefly, check what you already know about me, and guide me to the next step.]")
        while True:
            try:
                line = self.console.input("\n[bold green]you ›[/bold green] ").strip()
            except (EOFError, KeyboardInterrupt):
                break
            if not line:
                continue
            if line.startswith("/"):
                if self.command(line) is False:
                    break
            else:
                self.chat(line)
        self.close()

    # --------------------------------------------------------- commands ---
    def command(self, line: str) -> bool | None:
        try:
            parts = shlex.split(line)
        except ValueError:
            parts = line.split()
        cmd, args = parts[0].lower(), parts[1:]
        try:
            if cmd in ("/quit", "/exit", "/q"):
                return False
            if cmd == "/help":
                self.console.print(HELP.format(sites=", ".join(LOGIN_URLS)))
            elif cmd == "/jobs":
                self.show_jobs(int(args[0]) if args else 20)
            elif cmd == "/job" and args:
                self.show_job(int(args[0]))
            elif cmd == "/open" and args:
                job = self.store.get_job(int(args[0]))
                if not job:
                    self.ui.warn("No such job.")
                else:
                    self.browser().goto(job.get("apply_url") or job["url"])
            elif cmd == "/apps":
                self.show_apps()
            elif cmd == "/profile":
                p = self.store.get_profile()
                self.console.print_json(p.model_dump_json(exclude_none=True) if p else "null")
                self.console.print(f"Resume file: {self.store.get_kv('resume_path')}")
            elif cmd == "/prefs":
                self.console.print_json(self.store.get_preferences().model_dump_json())
            elif cmd == "/answers":
                t = Table("Question", "Answer")
                for a in self.store.list_answers():
                    t.add_row(a["question"], a["answer"])
                self.console.print(t)
            elif cmd == "/companies":
                t = Table("Company", "Board", "Slug")
                for c in self.store.list_companies():
                    t.add_row(c["name"], c["ats"], c["slug"])
                self.console.print(t)
            elif cmd == "/login":
                target = args[0] if args else "linkedin"
                url = LOGIN_URLS.get(target.lower(), target if target.startswith("http") else None)
                if not url:
                    self.ui.warn(f"Unknown site. Use one of: {', '.join(LOGIN_URLS)} or a full URL.")
                else:
                    self.browser().goto(url)
                    self.ui.wait_for_enter("Log in in the browser window. Your login is remembered for next time.")
            elif cmd == "/settings":
                t = Table("Setting", "Value")
                for k, v in vars(self.settings).items():
                    t.add_row(k, str(v))
                self.console.print(t)
                self.console.print(f"[dim]Sources available: {', '.join(ALL_SOURCES)}[/dim]")
            elif cmd == "/set" and len(args) >= 2:
                self.settings.set_from_string(args[0], " ".join(args[1:]))
                if args[0] == "model":
                    self.llm.model = self.settings.model
                if args[0] in ("headless", "slow_mo_ms", "browser_channel", "browser_executable") and self._browser:
                    self._browser.close()
                    self._browser = None
                self.console.print(f"Set {args[0]} = {getattr(self.settings, args[0])}")
            elif cmd == "/reset":
                self.agent.reset()
                self.console.print("Started a fresh conversation.")
            else:
                self.ui.warn("Unknown command. Type /help.")
        except (KeyError, ValueError) as e:
            self.ui.warn(str(e))
        except BrowserError as e:
            self.ui.warn(f"Browser problem: {e}")
        return None

    def show_jobs(self, n: int) -> None:
        t = Table("ID", "Score", "Title", "Company", "Location", "Source", "Status")
        for j in self.store.list_jobs(limit=n, statuses=["new", "shortlisted", "applying", "applied", "failed"]):
            score = "" if j.get("match_score") is None else str(j["match_score"])
            t.add_row(str(j["id"]), score, j["title"][:45], (j.get("company") or "")[:25],
                      (j.get("location") or "")[:25], j["source"], j["status"])
        self.console.print(t)

    def show_job(self, job_id: int) -> None:
        j = self.store.get_job(job_id)
        if not j:
            self.ui.warn("No such job.")
            return
        self.console.print(f"[bold]#{j['id']} {j['title']}[/bold] @ {j.get('company')} — {j.get('location')}")
        self.console.print(f"Source: {j['source']} · {j['url']}")
        for label, key in (("Salary", "salary_text"), ("Experience", "experience_text"), ("Posted", "date_posted"),
                           ("Score", "match_score"), ("Why", "match_reasons"), ("Missing", "missing_skills"),
                           ("Red flags", "red_flags"), ("Status", "status")):
            if j.get(key) not in (None, "", []):
                self.console.print(f"{label}: {j[key]}")
        if j.get("description"):
            self.console.print("\n" + j["description"][:3000])

    def show_apps(self) -> None:
        t = Table("App", "Job", "Title", "Company", "Status", "Summary")
        for a in self.store.list_applications():
            t.add_row(str(a["id"]), str(a["job_id"]), a["title"][:35], (a.get("company") or "")[:20],
                      a["status"], (a.get("summary") or "")[:60])
        self.console.print(t)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="job-agent", description="AI job-search agent for India.")
    parser.add_argument("--home", help="Data folder (default ~/.job-agent or $JOB_AGENT_HOME)")
    parser.add_argument("--version", action="version", version=__version__)
    args = parser.parse_args(argv)
    if args.home:
        os.environ["JOB_AGENT_HOME"] = args.home

    logging.basicConfig(
        filename=str(data_dir() / "agent.log"), level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    for noisy in ("jobspy", "JobSpy", "httpx", "httpx2", "urllib3"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    if not (os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN")):
        Console().print("[yellow]ANTHROPIC_API_KEY is not set. Get a key at https://console.anthropic.com and run:\n"
                        "  export ANTHROPIC_API_KEY=sk-ant-...   (Windows: setx ANTHROPIC_API_KEY sk-ant-...)[/yellow]")
        sys.exit(1)

    app = App(Settings.load())
    try:
        app.run()
    finally:
        app.close()


if __name__ == "__main__":
    main()
