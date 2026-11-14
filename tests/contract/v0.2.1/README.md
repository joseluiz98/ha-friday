Frozen copy of the satellite agent of release v0.2.1 (`git archive v0.2.1 agents/satellite/friday_satellite_agent
agents/common/friday_agent_core`). The contract that never breaks (spec 2026-11-10 §16): an agent of an old image
receives the new agent through `apply_bundle kind=update`. Never edit; `tests/contract/test_bootstrap_contract.py`
runs it against the package the current server builds.
