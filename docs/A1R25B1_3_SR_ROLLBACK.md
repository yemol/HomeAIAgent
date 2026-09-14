# A1R25B1.3 SR Rollback Stable

Purpose: restore the last known-working wake-recognition baseline before the
A1R25B2/B2.1 low-level probability-export experiment.

The build script removes only these persisted `.pio-local` additions when found:

- `HOMEAI_A1R25B2_PROB_EXPORT`
- `HOMEAI_A1R25B2_PROB_ASSIGN`

It does not delete or replace `.pio-local`.

Preserved:
- Chinese MultiNet command-only wake path
- `你好逐光`
- A1R25A.1 `[WAKE-MN]` observe-only logging
- Wake PGA 6 dB
- Glass2 brightness 96
- Glass2 local voice controls
- latest Gateway tree from the uploaded project
