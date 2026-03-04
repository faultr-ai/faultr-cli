# Faultr CLI

The official command-line interface for the Faultr agentic stress testing platform.

## Installation

```bash
pip install -e .
```

## Usage

Authenticate your CLI:
```bash
faultr auth <your-api-key>
```

List available scenarios:
```bash
faultr scenarios
```

Run an evaluation:
```bash
faultr run --scenario S001 --agent-response response.json
```
