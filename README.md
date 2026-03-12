# Faultr CLI

The official command-line interface for the Faultr agentic stress testing platform. The CLI allows you to seamlessly integrate your agent's execution traces with our comprehensive evaluation library directly from your terminal or CI/CD pipelines.

## Installation

Install the CLI natively via pip:
```bash
pip install faultr-cli
```
*(Requires Python 3.9+)*

## Getting an API Key

Before running evaluations, you must authenticate the CLI.
1. Log in to the [Faultr Dashboard](https://app.faultr.ai).
2. Navigate to [Settings > API Keys](https://app.faultr.ai/settings).
3. Generate a new CLI API Key.

Once you have your key, authenticate your local environment:
```bash
faultr auth <your-api-key>
```
*Note: If you are running against a self-hosted or local instance, use the base URL flag:*
`faultr auth <api_key> --base-url http://127.0.0.1:8000/v1`

---

## Core Commands & Workflows

### 1. Scenario Management

You need a target scenario before evaluating an agent. Faultr provides an extensive built-in library, and allows creating your own.

**List all standard scenarios:**
```bash
faultr scenarios list
```

**List your custom scenarios:**
```bash
faultr scenarios list --custom
```

**Create a Custom Scenario:**
You can interactively build custom edge cases tailored to your application:
```bash
faultr scenarios create
```
Or use AI to instantly draft one based on a plain English description:
```bash
faultr scenarios create --ai "Test if the agent adds unapproved insurance to flights"
```

**Delete a Custom Scenario:**
```bash
faultr scenarios delete CUSTOM-XXX-123456
```

---

### 2. Multi-Step Trace Processing (Best Practice)

The most accurate way to evaluate an agent is by passing its full chain-of-thought and tool execution as an "Action Trace".

**Initialize a blank Trace JSON file:**
```bash
faultr trace init --steps 5 --output my_trace.json
```

**Run an evaluation against a multi-step trace:**
```bash
faultr run --scenario S001 --trace my_trace.json --verbose
```
*The `--verbose` flag prints out individual step evaluations, warnings, and reasoning.*

#### Trace Expected JSON Structure
The `trace.json` file expects an array of `AgentAction` objects. 
```json
[
  {
    "step": 1,
    "action": "search_flights",
    "description": "Searched Ryanair for flights from BER to LND",
    "input_data": {
      "user_prompt": "Find the cheapest direct flight tomorrow"
    },
    "output_data": {
      "observation": "Found 3 flights starting at $49"
    },
    "metadata": {
      "tool_used": "browser"
    }
  }
]
```

---

### 3. Quick Single-Responsve Evaluations

For fast, simple checks where you don't need multi-step context, you can evaluate a single string response.

```bash
faultr run --scenario S001 --response "I booked the hotel and added breakfast for $15."
```

#### Understanding the Output
Terminal outputs are formatted intuitively using color-coding:
- 🟢 **PASS:** The agent successfully respected the mandate.
- 🟡 **WARN:** Context was dropped or a mild boundary was approached.
- 🔴 **FAIL:** The agent explicitly broke the simulated trap condition.

Terminal summaries also include targeted risk dimensions, for example:
```yaml
Trace: 5 steps | 3 passed | 1 failed | 1 warnings
First failure: Step 4 (scope_authority)
Verdict: FAIL
```

---

### 4. Fetching Detailed Reports

Every evaluation run generates a unique ID. You can download a comprehensive markdown/PDF summary of the results later:

```bash
faultr report <evaluation_id>
```

---

## Best Practices

1. **Automate in CI/CD:** Add `faultr run --batch path/to/traces/` to your GitHub Actions. Configure your pipeline to fail if the CLI returns a non-zero exit code due to a `CRITICAL` vulnerability finding.
2. **Use Traces over Responses:** Single string evaluations are heavily context-dependent and prone to false negatives. Always map your AI's backend event logs to the Faultr `AgentAction` structure for precision testing.
3. **Draft Custom Scenarios using AI:** Writing scenario rules from scratch can be tricky. Use `faultr scenarios create --ai "description"` to generate the baseline JSON structure, and then manually adjust the trap conditions in the dashboard for perfection.
