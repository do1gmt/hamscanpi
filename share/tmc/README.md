# TMC data

Used by `bin/tmc.py` to turn TMC traffic messages from RDS into plain text.

| File | Content | Source |
|---|---|---|
| `lcl_de.json.gz` | German Location Code List (LTN 1): points, roads, segments, areas with names, junction numbers, neighbours and coordinates | Bundesanstalt für Straßenwesen (BASt), *Location Code List* LCL 22.0 (2023) |
| `events_de.json.gz` | Event texts (German and English) and supplementary information | BASt, *Event Code List* ECL 4.01 |

Both are © Bundesanstalt für Straßenwesen (BASt), licensed under
[Creative Commons Attribution 4.0 International (CC BY 4.0)](https://creativecommons.org/licenses/by/4.0/),
published at https://www.bast.de. The original files were converted to compact JSON and reduced to the
fields HamScanPi needs (`devtools/build_tmc_data.py`); the content was not changed. BASt notes that the
LCL is no longer maintained since 2022 and gives no warranty for correctness or completeness.
