# SoC Hardware Description Files

Per-SoC YAML files of public silicon facts: PM device IDs, cluster
templates, TCM/OCM map, GIC layout, etc. Loaded by the device-inventory
extractor (`lopper/assists/_devices_core.py`) and selected by a root
`compatible` match against the input device tree.

## Sourcing rule

Every fact in these files must trace to a **public** source — a kernel
binding header, vendor TRM, datasheet, or upstream Zephyr DT. Anything
sourced from a vendor-generated SDT, an internal XSA, or an
internal-only header is forbidden — using internal information would
defeat the purpose of generating SDTs from public inputs.

## File format (minimal)

```yaml
soc:
  family: <SoC family identifier>           # informational
  matches:                                  # root compatibles that activate this file
    - <full-compatible-string>
    - <full-compatible-string>

pm_devices:                                 # PM device ID → canonical name
  0x18224021: PM_DEV_UART_0
  # …
```

Future schema additions (per design doc): `cluster_templates`,
`tcm`/`ocm` memory maps, `ipi` configuration, `gic` topology.

## Adding a new SoC

1. Copy an existing file as a template.
2. Update `family` and `matches` to identify the SoC.
3. Populate `pm_devices` (and other tables) from PUBLIC sources only.
4. Cite the source in a YAML comment block at the top of the file.
5. Add a smoke test under `tests/test_devices_core.py`.

## Currently shipped

| File | Family | Public source |
|---|---|---|
| `versal.yaml` | AMD Versal | `xlnx-versal-power.h` (Linux kernel, GPL-2.0) |
