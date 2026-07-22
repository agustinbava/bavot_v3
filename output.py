"""Console rendering with rich: summary table + optional verbose analysis."""
from __future__ import annotations

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from analyzer import Verdict

console = Console()

_VERDICT_STYLE = {
    "LONG": "bold green",
    "SHORT": "bold red",
    "NO TRADE": "dim",
    "?": "yellow",
}


def _style_for(v: Verdict) -> str:
    if v.error:
        return "bold yellow"
    return _VERDICT_STYLE.get(v.verdict, "yellow")


def render_summary(verdicts: list[Verdict]) -> None:
    """Print the color-coded summary table."""
    table = Table(title="Scalping analysis", header_style="bold cyan")
    for col in ("Ticker", "Verdict", "Entry", "SL", "TP", "R/R", "Conf"):
        table.add_column(col)

    for v in verdicts:
        style = _style_for(v)
        if v.error:
            table.add_row(v.symbol, "ERROR", "-", "-", "-", "-", "-", style=style)
            continue
        if not v.parsed:
            table.add_row(
                v.symbol, "UNPARSED", "-", "-", "-", "-", "-", style="yellow"
            )
            continue
        table.add_row(
            v.symbol, v.verdict, v.entry, v.stop_loss,
            v.take_profit, v.rr, v.confidence, style=style,
        )

    console.print(table)


def render_verbose(verdicts: list[Verdict]) -> None:
    """Print each instrument's full analysis (or error) in a panel."""
    for v in verdicts:
        if v.error:
            console.print(Panel(v.error, title=f"{v.symbol} — ERROR",
                                border_style="yellow"))
            continue
        console.print(
            Panel(v.raw or "(empty response)", title=f"{v.symbol} — {v.verdict}",
                  border_style=_style_for(v))
        )


def info(msg: str) -> None:
    console.print(f"[cyan]›[/cyan] {msg}")


def warn(msg: str) -> None:
    console.print(f"[yellow]![/yellow] {msg}")


def error(msg: str) -> None:
    console.print(f"[red]✗[/red] {msg}")
