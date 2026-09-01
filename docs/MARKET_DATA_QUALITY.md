# Market Data Quality Contract

`autonomous_kernel.market_data_quality.classify_market_data` is the first independently qualified adaptation prompted by predecessor research. It copies no runtime integration or calibrated constants from EPI/Epinnox.

The caller supplies a provider identity, source-event timestamp, local receive timestamp, decision-observation timestamp, and explicit age limits. The pure function returns schema version 1 with one status:

- `VALID`: complete ordered timestamps within both limits; an action may proceed to other gates.
- `DEGRADED`: transport latency exceeds its declared limit; action is blocked.
- `STALE`: the event is older than its declared limit; action is blocked.
- `UNAVAILABLE`: provenance is missing or the timestamp chain is impossible; action is blocked.

This contract is venue-neutral and read-only. It performs no network request, write, experiment advancement, decision resolution, wallet access, signing, order submission, or state mutation. Limits are consumers' preregistered parameters rather than hidden defaults.

EXP-MKT-002 is intentionally not wired to this contract mid-experiment. Changing its observation schema or gate while it is running would contaminate the preregistered forward test. Future experiments may preregister this contract before their first observation.
