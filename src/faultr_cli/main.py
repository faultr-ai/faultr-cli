import json
import os
import time
from pathlib import Path
from typing import Optional, List

import httpx
import typer
from rich.console import Console
from rich.table import Table
from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn, TimeElapsedColumn
from rich import print as rprint

app = typer.Typer(
    help="Faultr CLI: Agentic Stress Testing Automation",
    no_args_is_help=True,
    add_completion=False,
)

console = Console()

CONFIG_DIR = Path.home() / ".faultr"
CONFIG_FILE = CONFIG_DIR / "config.json"
DEFAULT_BASE_URL = "https://app.faultr.ai"


def load_config() -> dict:
    if CONFIG_FILE.exists():
        try:
            with open(CONFIG_FILE, "r") as f:
                return json.load(f)
        except json.JSONDecodeError:
            pass
    return {}


def save_config(config: dict):
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    with open(CONFIG_FILE, "w") as f:
        json.dump(config, f, indent=4)


def get_client() -> httpx.Client:
    config = load_config()
    api_key = config.get("api_key")
    if not api_key:
        console.print("[bold red]Error:[/] Not authenticated. Run [cyan]faultr auth <api-key>[/cyan] first.")
        raise typer.Exit(1)
    
    base_url = config.get("base_url", DEFAULT_BASE_URL)
    return httpx.Client(
        base_url=base_url,
        headers={"Authorization": f"Bearer {api_key}"},
        timeout=30.0
    )


@app.command()
def auth(api_key: str):
    """
    Authenticate the CLI with your Faultr API Key.
    """
    config = load_config()
    config["api_key"] = api_key
    if "base_url" not in config:
        config["base_url"] = DEFAULT_BASE_URL
        
    save_config(config)
    console.print("[bold green]✓[/] Successfully authenticated and saved configuration.")


@app.command()
def scenarios():
    """
    List all available adversarial scenarios in the registry.
    """
    with console.status("[bold cyan]Fetching scenarios..."):
        try:
            with get_client() as client:
                res = client.get("/v1/scenarios")
                res.raise_for_status()
                data = res.json()
        except httpx.HTTPError as e:
            console.print(f"[bold red]Failed to fetch scenarios:[/] {e}")
            raise typer.Exit(1)

    if not data:
        console.print("[yellow]No scenarios found.[/]")
        return

    table = Table(title="Faultr Scenario Library")
    table.add_column("ID", style="cyan", no_wrap=True)
    table.add_column("Name")
    table.add_column("Severity")
    table.add_column("Taxonomy", style="blue")

    for s in data:
        sev = s.get("severity", "LOW")
        sev_style = "red" if sev == "CRITICAL" else "yellow" if sev == "HIGH" else "green" if sev == "LOW" else "cyan"
        table.add_row(
            s.get("id", "N/A"),
            s.get("name", "Unknown"),
            f"[{sev_style}]{sev}[/]",
            s.get("failure_taxonomy", "N/A")
        )

    console.print(table)


@app.command()
def run(
    scenario: Optional[str] = typer.Option(None, "--scenario", "-s", help="The Scenario ID to evaluate, or 'all' for batch execution."),
    scenarios_opt: Optional[str] = typer.Option(None, "--scenarios", help="Same as --scenario, provided for convenience ('all')."),
    agent_response: Path = typer.Option(..., "--agent-response", "-r", exists=True, file_okay=True, dir_okay=False, help="Path to the JSON file containing the LLM interaction log.")
):
    """
    Run an evaluation against a specific scenario or all scenarios using a provided agent execution trace.
    """
    target_scenario = scenario or scenarios_opt
    if not target_scenario:
        console.print("[bold red]Error:[/] Must provide --scenario (e.g., S001 or 'all')")
        raise typer.Exit(1)
        
    try:
        with open(agent_response, "r") as f:
            payload_data = json.load(f)
    except Exception as e:
        console.print(f"[bold red]Error parsing agent response JSON:[/] {e}")
        raise typer.Exit(1)

    client = get_client()
    
    if target_scenario.lower() == "all":
        _run_batch(client, payload_data)
    else:
        _run_single(client, target_scenario, payload_data)


def _run_single(client: httpx.Client, scenario_id: str, payload_data: dict):
    payload = {
        "scenario": scenario_id,
        "input_data": payload_data
    }
    
    with console.status(f"[bold cyan]Evaluating {scenario_id}..."):
        try:
            res = client.post("/v1/evaluate", json=payload)
            res.raise_for_status()
            result = res.json()
        except httpx.HTTPError as e:
            console.print(f"[bold red]Evaluation failed:[/] {e}")
            raise typer.Exit(1)
            
    _print_eval_result(result)


def _run_batch(client: httpx.Client, payload_data: dict):
    with console.status("[bold cyan]Fetching scenario library..."):
        try:
            res = client.get("/v1/scenarios")
            res.raise_for_status()
            all_scenarios = res.json()
        except httpx.HTTPError as e:
            console.print(f"[bold red]Failed to fetch scenarios:[/] {e}")
            raise typer.Exit(1)

    if not all_scenarios:
        console.print("[yellow]No scenarios available to run.[/]")
        return
        
    results = []
    
    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
        TimeElapsedColumn(),
        console=console
    ) as progress:
        task = progress.add_task("[cyan]Running batch evaluation...", total=len(all_scenarios))
        
        for s in all_scenarios:
            sid = s.get("id")
            progress.update(task, description=f"[cyan]Evaluating {sid}...")
            
            payload = {
                "scenario": sid,
                "input_data": payload_data
            }
            
            try:
                # Add a tiny delay so the progress bar is actually visible and satisfying
                time.sleep(0.5)
                res = client.post("/v1/evaluate", json=payload)
                if res.status_code == 200:
                    results.append(res.json())
                else:
                    results.append({"scenario_id": sid, "overall_status": "ERROR", "message": str(res.status_code)})
            except Exception as e:
                 results.append({"scenario_id": sid, "overall_status": "ERROR", "message": str(e)})
                 
            progress.advance(task)
            
    console.print("\n[bold]Batch Execution Summary[/]")
    table = Table()
    table.add_column("Scenario ID", style="cyan")
    table.add_column("Result")
    table.add_column("Evaluation ID", style="dim")
    
    passes = 0
    fails = 0
    
    for r in results:
        status = r.get("overall_status", "UNKNOWN")
        if status == "PASS":
            passes += 1
            status_fmt = "[bold green]PASS[/]"
        elif status == "FAIL":
            fails += 1
            status_fmt = "[bold red]FAIL[/]"
        elif status == "ERROR":
            status_fmt = "[bold yellow]ERROR[/]"
        else:
            status_fmt = f"[{'yellow'}]{status}[/]"
            
        table.add_row(
            r.get("scenario_id", "Unknown"),
            status_fmt,
            r.get("evaluation_id", "N/A")
        )
        
    console.print(table)
    console.print(f"Total: {len(results)} | [green]Pass: {passes}[/] | [red]Fail: {fails}[/]")

def _print_eval_result(result: dict):
    status = result.get("overall_status", "UNKNOWN")
    score = result.get("total_score", 0)
    eval_id = result.get("evaluation_id", "Unknown")
    
    console.print("\n[bold underline]Evaluation Complete[/]")
    console.print(f"Evaluation ID: [dim]{eval_id}[/]")
    console.print(f"Scenario: [cyan]{result.get('scenario_id')}[/]")
    console.print(f"Score: {score}/100")
    
    if status == "PASS":
        console.print(f"Result: [bold green white on green]  PASS  [/]")
    elif status == "FAIL":
        console.print(f"Result: [bold white on red]  FAIL  [/]")
        reason = result.get("failure_category") or result.get("reasoning")
        if reason:
            console.print(f"\n[red]Reasoning:[/]\n{reason}")
    else:
        console.print(f"Result: [bold yellow]{status}[/]")


@app.command()
def report(evaluation_id: str):
    """
    Download a detailed HTML report for a specific evaluation.
    """
    console.print(f"[bold cyan]Downloading report for evaluation: {evaluation_id}...[/]")
    
    try:
        with get_client() as client:
            # We assume there is/will be a GET endpoint to fetch the report.
            res = client.get(f"/v1/evaluations/{evaluation_id}/report")
            res.raise_for_status()
            report_html = res.text
            
            filename = f"faultr_report_{evaluation_id}.html"
            with open(filename, "w") as f:
                f.write(report_html)
                
            console.print(f"[bold green]✓ Report downloaded successfully:[/] {filename}")
            
    except httpx.HTTPError as e:
        console.print(f"[bold yellow]Could not fetch report via API:[/] {e}")
        console.print(f"You can view the full details in your browser at: [blue underline]https://app.faultr.ai/evaluations/{evaluation_id}[/]")


if __name__ == "__main__":
    app()
