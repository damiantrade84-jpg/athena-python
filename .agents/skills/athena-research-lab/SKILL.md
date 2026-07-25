---
name: athena-research-lab
description: Manual-only bounded Athena Research Lab or vectorbt experiment. Invoke explicitly as $athena-research-lab; never auto-run backtests or change production behavior.
---

# Athena research lab

Use only when explicitly invoked for a named research experiment, dataset, config, and output. Research does not authorize live threshold or execution changes.

- Keep runs bounded to the requested symbols, periods, and parameters.
- Do not launch a full matrix, regenerate existing artifacts, or repeat a completed run unless requested.
- Reuse current outputs when their provenance and freshness are sufficient.
- Separate measured research results from any proposed production change.
- Do not modify live scoring, risk, execution, or production config without explicit approval.

Report the exact command, artifact, measured result, and any live parity not verified.
