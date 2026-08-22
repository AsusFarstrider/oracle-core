# Local UI Font And Icon Assets

These browser assets replace remote Google Fonts requests. They are served by
Oracle from `/ui/assets/fonts/` and are reusable distribution material.

## Fonts

Manrope and Instrument Serif were acquired from the official Google Fonts
service on 2026-07-28. License provenance is pinned to `google/fonts` commit
`7ff85c87f93ea6cca5f41c69f2e4edcb90240f26`. Both families use the SIL Open
Font License 1.1; the upstream texts are retained under `licenses/` with
trailing whitespace normalized.

| File | Included variant | SHA-256 |
| --- | --- | --- |
| `manrope-latin.woff2` | Latin variable normal, weights 400 through 800 | `a30ddcd349703aff7464c34bef3fffdff405ee50c113440d7c8693c02d210972` |
| `instrument-serif-latin.woff2` | Latin normal 400 | `5eb09b5ac0e28b67c2f041c8ba6d244604ca0c0980d65912ab2d47fed84ddc31` |
| `instrument-serif-italic-latin.woff2` | Latin italic 400 | `5a51946dfffa82972bc98745359c46761515641fda557c25116459a9f83da4a7` |

The files are the Latin WOFF2 responses for Google Fonts Manrope v20 and
Instrument Serif v5. System sans-serif and serif fallbacks remain in CSS.

## Material Symbols

`material-symbols-outlined-oracle.woff2` is a Google Fonts subset requested on
2026-07-28 for exactly the names in `material-symbols-icons.txt`, using the
Outlined variable axes required by Oracle. Its SHA-256 is
`9999650a0e326963065b2e25ac5d87936ca94a98f2ccce3d9bd6a63f721b3b9e`.

Material Symbols are distributed by Google under Apache License 2.0. License
provenance is pinned to `google/material-design-icons` commit
`528cb964c01fb2b09bc3b9208f82b6d8f8c1c1e2`; the upstream license text is
retained under `licenses/`. Oracle's root Apache-2.0 license does not imply
that Google authored or licensed the rest of Oracle.

There was no upstream NOTICE file requiring preservation for these acquired
assets. If Oracle begins using another symbol, update the explicit inventory,
acquire a new subset, record its checksum, and pass the local-asset test before
promotion.
