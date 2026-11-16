"""Iron-Man-HUD im Terminal (mit rich)."""

from __future__ import annotations

import sys
import time

from rich.console import Console
from rich.panel import Panel
from rich.text import Text

console = Console(highlight=False)

CYAN = "bold #00d4ff"
GOLD = "#ffb300"
DIM = "#5f87af"

LOGO = r"""
     ██╗    █████╗    ██████╗   ██╗   ██╗  ██╗  ███████╗
     ██║   ██╔══██╗   ██╔══██╗  ██║   ██║  ██║  ██╔════╝
     ██║   ███████║   ██████╔╝  ██║   ██║  ██║  ███████╗
██   ██║   ██╔══██║   ██╔══██╗  ╚██╗ ██╔╝  ██║  ╚════██║
╚█████╔╝██╗██║  ██║██╗██║  ██║██╗╚████╔╝██╗██║██╗███████║
 ╚════╝ ╚═╝╚═╝  ╚═╝╚═╝╚═╝  ╚═╝╚═╝ ╚═══╝ ╚═╝╚═╝╚═╝╚══════╝"""


def boot(checks: list[tuple[str, str, bool]]) -> None:
    """Boot-Sequenz. checks = [(Name, Detail, ok), ...]"""
    console.clear()
    console.print(Text(LOGO, style=CYAN))
    console.print(Text("        Just A Rather Very Intelligent System", style=GOLD))
    console.print()
    for name, detail, ok in checks:
        console.print(f"  [{DIM}]⟫ Initialisiere {name}…[/]", end="")
        sys.stdout.flush()
        time.sleep(0.25)
        mark = "[bold green]ONLINE[/]" if ok else "[bold yellow]EINGESCHRÄNKT[/]"
        console.print(f"  {mark}  [{DIM}]{detail}[/]")
    console.print()


def help_panel(has_mic: bool, conversation: bool) -> None:
    lines = []
    if conversation and has_mic:
        lines.append("🎙  Gesprächsmodus: einfach sprechen. JARVIS hört nach jeder Antwort automatisch wieder zu.")
    elif has_mic:
        lines.append("🎙  [bold]Enter[/] drücken und sprechen – oder Nachricht eintippen und Enter.")
    else:
        lines.append("⌨  Nachricht eintippen und Enter drücken.")
    lines.append("⏹  Beliebige Taste während JARVIS spricht = unterbrechen (Windows).")
    lines.append("↺  „neues Gespräch“ = Gedächtnis löschen   ✕  „beenden“ / Strg+C = Ende")
    console.print(Panel("\n".join(lines), border_style="#00d4ff", title="[bold #ffb300]Bedienung[/]",
                        title_align="left"))


def status(msg: str) -> None:
    console.print(f"  [{DIM}]⟳ {msg}…[/]")


def listening() -> None:
    console.print(f"  [{GOLD}]◉ Ich höre zu…[/]")


def user_said(text: str) -> None:
    console.print(f"\n[bold white]Du ›[/] {text}")


def jarvis_prefix() -> None:
    console.print(f"[{CYAN}]JARVIS ›[/] ", end="")


def jarvis_chunk(text: str) -> None:
    console.print(Text(text, style="#cfefff"), end="")


def newline() -> None:
    console.print()


def warn(msg: str) -> None:
    console.print(f"  [bold yellow]⚠ {msg}[/]")


def prompt() -> str:
    return console.input(f"\n[{GOLD}]›[/] ")
