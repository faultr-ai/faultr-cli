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
    help="""
Faultr CLI: Agentic Stress Testing Automation

The faultr CLI provides native environment testing tools to run agent action traces against our comprehensive evaluation library.

🔹 CORE COMMANDS

1. Authentication
─────────────────────────────────────────────────────────────
  Authenticate your local environment with Faultr.
  $ faultr auth <api_key>
  $ faultr auth <api_key> --base-url http://127.0.0.1:8000/v1


2. Scenario Management
─────────────────────────────────────────────────────────────
  List all standard scenarios available to target:
  $ faultr scenarios list

  List your manually created custom scenarios:
  $ faultr scenarios list --custom

  Create a custom scenario (Interactive CLI builder):
  $ faultr scenarios create

  Create a custom scenario (AI Assisted fast-draft):
  $ faultr scenarios create --ai "Test if the agent adds unapproved insurance to flights"

  Delete a custom scenario:
  $ faultr scenarios delete CUSTOM-XXX-123456


3. Evaluation Execution
─────────────────────────────────────────────────────────────
  Run a single agent string response against a scenario:
  $ faultr run --scenario S001 --response "I booked the hotel and added breakfast."

  Run a multi-step AgentAction trace (The standard way):
  $ faultr run --scenario S001 --trace path/to/trace.json --verbose

  Run an entire suite of scenarios against a directory of traces:
  $ faultr run --batch path/to/eval_run.json


4. Trace Utilities
─────────────────────────────────────────────────────────────
  Generate a clean JSON template to fill out your agent's steps:
  $ faultr trace init --steps 5 --output my_new_trace.json


5. Reporting
─────────────────────────────────────────────────────────────
  Pull down a detailed PDF/Markdown report of a specific evaluation run:
  $ faultr report <evaluation_id>
""",
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
        timeout=120.0
    )


@app.command()
def auth(
    api_key: str,
    base_url: Optional[str] = typer.Option(None, "--base-url", help="Override the default API base URL.")
):
    """
    Authenticate the CLI with your Faultr API Key.
    """
    config = load_config()
    config["api_key"] = api_key
    
    if base_url:
        config["base_url"] = base_url
    elif "base_url" not in config:
        config["base_url"] = DEFAULT_BASE_URL
        
    save_config(config)
    console.print("[bold green]✓[/] Successfully authenticated and saved configuration.")


scenarios_app = typer.Typer(help="Manage and list scenarios.")
app.add_typer(scenarios_app, name="scenarios")

trace_app = typer.Typer(help="Tools for managing action trace files.")
app.add_typer(trace_app, name="trace")

@scenarios_app.command("list")
def list_scenarios(
    custom: bool = typer.Option(False, "--custom", help="List user's custom scenarios instead of the built-in library.")
):
    """
    List all available adversarial scenarios in the registry, or custom scenarios if --custom is provided.
    """
    with console.status("[bold cyan]Fetching scenarios..."):
        try:
            with get_client() as client:
                query = "?source=custom" if custom else ""
                res = client.get(f"/v1/scenarios{query}")
                res.raise_for_status()
                data = res.json()
                if custom:
                    # Depending on backend return, filter to ensure only custom returned if list_scenarios returns all
                    # Based on existing implementation, GET /v1/scenarios?source=custom specifically returns ONLY custom.
                    pass
        except httpx.HTTPError as e:
            console.print(f"[bold red]Failed to fetch scenarios:[/] {e}")
            raise typer.Exit(1)

    if not data:
        console.print("[yellow]No scenarios found.[/]")
        return

    table = Table(title="Custom Scenarios" if custom else "Faultr Scenario Library")
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


@scenarios_app.command("delete")
def delete_scenario(scenario_id: str):
    """
    Delete a custom scenario you own.
    """
    if not scenario_id.startswith("CUSTOM-"):
        console.print("[bold red]Error:[/] You can only delete custom scenarios (ID must start with CUSTOM-).")
        raise typer.Exit(1)
        
    with console.status(f"[bold red]Deleting {scenario_id}..."):
        try:
            with get_client() as client:
                res = client.delete(f"/v1/scenarios/custom/{scenario_id}")
                res.raise_for_status()
        except httpx.HTTPError as e:
            console.print(f"[bold red]Failed to delete scenario:[/] {e}")
            raise typer.Exit(1)
            
    console.print(f"[bold green]✓ Deleted {scenario_id} successfully.[/]")


@scenarios_app.command("create")
def create_scenario(
    ai: Optional[str] = typer.Option(None, "--ai", help="Describe what to test and generate it automatically.")
):
    """
    Create a new custom scenario interactively, or dynamically draft one using AI.
    """
    client = get_client()
    
    if ai:
        with console.status(f"[bold cyan]Drafting scenario with AI: '{ai}'..."):
            try:
                res = client.post("/v1/scenarios/custom/generate", json={
                    "description": ai,
                    "domain": "travel" # Default fallback for pure CLI unless we want to prompt
                })
                res.raise_for_status()
                ai_data = res.json()
            except httpx.HTTPError as e:
                console.print(f"[bold red]AI Generation failed:[/] {e}")
                raise typer.Exit(1)
                
        scenario_draft = ai_data.get("scenario", {})
        console.print("\n[bold]Drafted Scenario Preview:[/]")
        console.print(json.dumps(scenario_draft, indent=2))
        
        confirm = typer.confirm("Save this scenario?")
        if not confirm:
            console.print("Aborted.")
            return
            
        payload = scenario_draft
        
    else:
        # Interactive mode
        console.print("[bold underline]Manual Scenario Builder[/]")
        name = typer.prompt("Name (e.g., Hotel Overbooking)")
        description = typer.prompt("Description")
        domain = typer.prompt("Domain", default="travel")
        difficulty = typer.prompt("Difficulty (easy, medium, hard, expert)", default="medium")
        trap_desc = typer.prompt("Trap Condition (What triggers the failure?)")
        
        console.print("\n[dim]Evaluation Criteria (Minimum 1 Required)[/]")
        dimension = typer.prompt("Dimension", default="logic_flaw")
        rule = typer.prompt("Rule (e.g., Must not accept double booking)")
        severity = typer.prompt("Severity (CRITICAL, HIGH, MEDIUM, LOW)", default="HIGH")
        
        console.print("\n[dim]Test Pattern Configuration[/]")
        trap_pattern = typer.prompt("Test Pattern (What exactly should we look for?)")
        
        payload = {
            "name": name,
            "description": description,
            "domain": domain,
            "difficulty": difficulty,
            "trap_conditions": {"description": trap_desc, "simulated_environment": {}},
            "evaluation_criteria": [{"dimension": dimension, "rule": rule, "severity": severity, "description": rule}],
            "manual_mode_config": {
                "test_dimension": dimension,
                "trap_pattern": trap_pattern,
                "constraints_to_extract": [],
                "evaluation_focus": "",
                "severity_if_violated": severity,
                "applicable_when": ""
            }
        }
        
    with console.status("[bold cyan]Saving to registry..."):
        try:
            res = client.post("/v1/scenarios/custom", json=payload)
            res.raise_for_status()
            saved_data = res.json()
        except httpx.HTTPError as e:
            console.print(f"[bold red]Failed to save scenario:[/] {e}")
            raise typer.Exit(1)
            
    console.print(f"[bold green]✓ Scenario saved![/] ID: [cyan]{saved_data.get('id', 'Unknown')}[/]")


@trace_app.command("init")
def trace_init(
    steps: int = typer.Option(..., "--steps", help="Number of steps in the template."),
    output: Path = typer.Option(..., "--output", help="Output file path.")
):
    """
    Generate a template multi-step trace.json file with N empty AgentAction objects.
    """
    template = []
    for i in range(1, steps + 1):
        template.append({
            "step": i,
            "action": "ACTION_NAME_HERE",
            "description": "Describe what the agent did in this step.",
            "input_data": {"user_prompt": "Optional explicit sub-instruction"},
            "output_data": {"observation": "What the system returned"},
            "metadata": {"tool_used": "e.g., browser"}
        })
        
    try:
        with open(output, "w") as f:
            json.dump(template, f, indent=4)
        console.print(f"[bold green]✓ Initialized trace template[/] with {steps} steps at {output}.")
    except Exception as e:
        console.print(f"[bold red]Failed to write trace file:[/] {e}")
        raise typer.Exit(1)


@app.command()
def run(
    scenario: Optional[str] = typer.Option(None, "--scenario", "-s", help="The Scenario ID to evaluate, or 'all' for batch execution."),
    scenarios_opt: Optional[str] = typer.Option(None, "--scenarios", help="Same as --scenario, provided for convenience ('all')."),
    agent_response: Optional[Path] = typer.Option(None, "--agent-response", "-r", exists=True, file_okay=True, dir_okay=False, help="Path to the JSON file containing the LLM interaction log."),
    trace: Optional[Path] = typer.Option(None, "--trace", "-t", exists=True, file_okay=True, dir_okay=False, help="Path to the JSON file containing the multi-step action trace."),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Show verbose output (e.g., all step details)."),
    output: str = typer.Option("text", "--output", help="Output format: 'text' (default) or 'json'.")
):
    """
    Run an evaluation against a specific scenario or all scenarios using a provided agent execution trace.
    """
    target_scenario = scenario or scenarios_opt
    if not target_scenario:
        console.print("[bold red]Error:[/] Must provide --scenario (e.g., S001 or 'all')")
        raise typer.Exit(1)
        
    if bool(agent_response) == bool(trace):
        console.print("[bold red]Error:[/] Must provide exactly one of --agent-response or --trace")
        raise typer.Exit(1)

    input_file = agent_response or trace
    try:
        with open(input_file, "r") as f:
            payload_data = json.load(f)
    except Exception as e:
        console.print(f"[bold red]Error parsing JSON:[/] {e}")
        raise typer.Exit(1)

    is_trace = bool(trace)
    client = get_client()
    
    if target_scenario.lower() == "all":
        _run_batch(client, payload_data, is_trace, output, verbose)
    else:
        _run_single(client, target_scenario, payload_data, is_trace, output, verbose)


def _run_single(client: httpx.Client, scenario_id: str, payload_data: dict, is_trace: bool, output_format: str = "text", verbose: bool = False):
    payload = {
        "scenario": scenario_id
    }
    if is_trace:
        payload["action_trace"] = payload_data
    else:
        payload["input_data"] = payload_data
    
    if output_format != "json":
        status_ctx = console.status(f"[bold cyan]Evaluating {scenario_id}...")
        status_ctx.start()
        
    try:
        res = client.post("/v1/evaluations", json=payload)
        res.raise_for_status()
        result = res.json()
    except httpx.HTTPError as e:
        if output_format != "json":
            status_ctx.stop()
            console.print(f"[bold red]Evaluation failed:[/] {e}")
        else:
            print(json.dumps({"error": str(e)}))
        raise typer.Exit(1)
        
    if output_format != "json":
        status_ctx.stop()
        _print_eval_result(result, is_trace, verbose)
    else:
        print(json.dumps([result])) # Output as single item array for consistency


def _run_batch(client: httpx.Client, payload_data: dict, is_trace: bool, output_format: str = "text", verbose: bool = False):
    if output_format != "json":
        status_ctx = console.status("[bold cyan]Fetching scenario library...")
        status_ctx.start()
        
    try:
        res = client.get("/v1/scenarios")
        res.raise_for_status()
        all_scenarios = res.json()
    except httpx.HTTPError as e:
        if output_format != "json":
            status_ctx.stop()
            console.print(f"[bold red]Failed to fetch scenarios:[/] {e}")
        else:
            print(json.dumps({"error": f"Failed to fetch scenarios: {e}"}))
        raise typer.Exit(1)
        
    if output_format != "json":
        status_ctx.stop()

    if not all_scenarios:
        if output_format != "json":
            console.print("[yellow]No scenarios available to run.[/]")
        else:
            print(json.dumps([]))
        return
        
    results = []
    
    if output_format != "json":
        progress = Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
            TimeElapsedColumn(),
            console=console
        )
        progress.start()
        task = progress.add_task("[cyan]Running batch evaluation...", total=len(all_scenarios))
    else:
        progress = None
        
    for s in all_scenarios:
        sid = s.get("id")
        if progress:
            progress.update(task, description=f"[cyan]Evaluating {sid}...")
            
        payload = {
            "scenario": sid
        }
        if is_trace:
            payload["action_trace"] = payload_data
        else:
            payload["input_data"] = payload_data
        
        try:
            if progress:
                time.sleep(0.5)
            res = client.post("/v1/evaluations", json=payload)
            if res.status_code == 200:
                results.append(res.json())
            else:
                results.append({"scenario_id": sid, "overall_status": "ERROR", "message": str(res.status_code)})
        except Exception as e:
             results.append({"scenario_id": sid, "overall_status": "ERROR", "message": str(e)})
             
        if progress:
            progress.advance(task)
            
    if progress:
        progress.stop()
        
    if output_format == "json":
        print(json.dumps(results))
        return
        
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

def _print_dimension_summary(name: str, dim: dict):
    status = dim.get("status")
    reason = dim.get("actual_behavior") or dim.get("remediation") or ""
    
    if status == "PASS":
        console.print(f"{name+':':<17} [bold green]✓ PASS[/]     {reason}")
    elif status == "FAIL":
        console.print(f"{name+':':<17} [bold red]✗ FAIL[/]     {reason}")
    else:
        console.print(f"{name+':':<17} [bold yellow]⚠ WARNING[/]  {reason}")


def _print_trace_result(result: dict, verbose: bool):
    eval_id = result.get("evaluation_id", "Unknown")
    metadata = result.get("metadata", {})
    trace_summary = metadata.get("trace_summary", {})
    step_evals = metadata.get("step_evaluations", [])
    
    console.print(f"\n[bold underline]Trace Evaluation Complete[/]")
    console.print(f"Evaluation ID: [dim]{eval_id}[/]")
    console.print(f"Scenario: [cyan]{result.get('scenario_id')}[/]\n")
    
    for step in step_evals:
        s_num = step.get("step")
        s_act = step.get("action", "")
        s_desc = step.get("description", "")
        s_stat = step.get("status", "UNKNOWN")
        
        prefix = f"Step {s_num} [{s_act}]"
        
        if s_stat == "PASS":
            stat_fmt = "[bold green]✓ PASS[/]"
        elif s_stat == "FAIL":
            stat_fmt = "[bold red]✗ FAIL[/]"
        elif s_stat == "WARNING":
            stat_fmt = "[bold yellow]⚠ WARN[/]"
        else:
            stat_fmt = f"[{'blue'}]{s_stat}[/]"
            
        console.print(f"{prefix:<20} {stat_fmt:<18} {s_desc}")
        
        if verbose and step.get("findings"):
            for f in step["findings"]:
                f_dim = f.get("dimension", "unknown")
                f_sev = f.get("severity", "INFO")
                f_msg = f.get("message", "")
                sev_color = "red" if f_sev == "CRITICAL" else "yellow" if f_sev == "HIGH" else "blue"
                console.print(f"  └─ [{sev_color}]{f_sev}[/] ({f_dim}): {f_msg}")
    
    console.print(f"\nTrace: {trace_summary.get('total_steps', 0)} steps | {trace_summary.get('steps_passed', 0)} passed | {trace_summary.get('steps_failed', 0)} failed | {trace_summary.get('steps_warned', 0)} warnings")
    
    first_fail_step = trace_summary.get("first_failure_step")
    if first_fail_step:
        # extract dimension
        first_fail_dim = "unknown"
        for step in step_evals:
            if step.get("step") == first_fail_step:
                for f in step.get("findings", []):
                    if f.get("severity") in ("CRITICAL", "HIGH"):
                        first_fail_dim = f.get("dimension", "unknown")
                        break
                break
        console.print(f"First failure: Step {first_fail_step} ({first_fail_dim})")
    
    overall_status = result.get("overall_status", "UNKNOWN")
    if overall_status == "PASS":
        console.print("Verdict: [bold green]PASS[/]")
    elif overall_status == "FAIL":
        console.print("Verdict: [bold red]FAIL[/]")
    else:
        console.print(f"Verdict: [bold yellow]{overall_status}[/]")


def _print_eval_result(result: dict, is_trace: bool = False, verbose: bool = False):
    status = result.get("overall_status", "UNKNOWN")
    score = result.get("total_score", 0)
    eval_id = result.get("evaluation_id", "Unknown")
    
    if is_trace and "trace_summary" in result.get("metadata", {}):
        _print_trace_result(result, verbose)
        return
        
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
        
    # Inject Safety / Authority rendering per user requirements
    results_data = result.get("results", [])
    data_safety = next((r for r in results_data if r.get("dimension") == "data_safety"), None)
    scope_auth = next((r for r in results_data if r.get("dimension") == "scope_authority"), None)
    
    if data_safety or scope_auth:
        console.print("")
    if data_safety:
        _print_dimension_summary("Data Safety", data_safety)
    if scope_auth:
        _print_dimension_summary("Scope Authority", scope_auth)


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
