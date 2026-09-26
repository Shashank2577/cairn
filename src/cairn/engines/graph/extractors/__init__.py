"""Per-language extractors, split out of extract.py.

Dispatch still flows through cairn.engines.graph.extract (the facade re-exports every
moved name), so importing from cairn.engines.graph.extract keeps working unchanged.
LANGUAGE_EXTRACTORS is the registry seed; wiring dispatch through it is a
later, separate step.
"""
from __future__ import annotations

from pathlib import Path
from typing import Callable

from cairn.engines.graph.extractors.apex import extract_apex
from cairn.engines.graph.extractors.bash import extract_bash
from cairn.engines.graph.extractors.blade import extract_blade
from cairn.engines.graph.extractors.cobol import extract_cobol
from cairn.engines.graph.extractors.commonlisp import extract_commonlisp
from cairn.engines.graph.extractors.dart import extract_dart
from cairn.engines.graph.extractors.dm import extract_dm, extract_dmf, extract_dmi, extract_dmm
from cairn.engines.graph.extractors.elixir import extract_elixir
from cairn.engines.graph.extractors.erlang import extract_erlang
from cairn.engines.graph.extractors.fortran import extract_fortran
from cairn.engines.graph.extractors.go import extract_go
from cairn.engines.graph.extractors.json_config import extract_json
from cairn.engines.graph.extractors.julia import extract_julia
from cairn.engines.graph.extractors.markdown import extract_markdown
from cairn.engines.graph.extractors.objc import extract_objc
from cairn.engines.graph.extractors.pascal import extract_pascal
from cairn.engines.graph.extractors.pascal_forms import extract_delphi_form, extract_lazarus_form
from cairn.engines.graph.extractors.powershell import extract_powershell, extract_powershell_manifest
from cairn.engines.graph.extractors.r import extract_r
from cairn.engines.graph.extractors.razor import extract_razor
from cairn.engines.graph.extractors.rust import extract_rust
from cairn.engines.graph.extractors.sln import extract_sln
from cairn.engines.graph.extractors.solidity import extract_solidity
from cairn.engines.graph.extractors.sql import extract_sql
from cairn.engines.graph.extractors.terraform import extract_terraform
from cairn.engines.graph.extractors.verilog import extract_verilog
from cairn.engines.graph.extractors.vbnet import extract_vbnet
from cairn.engines.graph.extractors.zig import extract_zig

LANGUAGE_EXTRACTORS: dict[str, Callable[[Path], dict]] = {
    "apex": extract_apex,
    "bash": extract_bash,
    "blade": extract_blade,
    "cobol": extract_cobol,
    "commonlisp": extract_commonlisp,
    "dart": extract_dart,
    "delphi_form": extract_delphi_form,
    "dm": extract_dm,
    "dmf": extract_dmf,
    "dmi": extract_dmi,
    "dmm": extract_dmm,
    "elixir": extract_elixir,
    "erlang": extract_erlang,
    "fortran": extract_fortran,
    "go": extract_go,
    "json": extract_json,
    "julia": extract_julia,
    "lazarus_form": extract_lazarus_form,
    "markdown": extract_markdown,
    "objc": extract_objc,
    "pascal": extract_pascal,
    "powershell": extract_powershell,
    "powershell_manifest": extract_powershell_manifest,
    "r": extract_r,
    "razor": extract_razor,
    "rust": extract_rust,
    "sln": extract_sln,
    "solidity": extract_solidity,
    "sql": extract_sql,
    "terraform": extract_terraform,
    "verilog": extract_verilog,
    "vbnet": extract_vbnet,
    "zig": extract_zig,
}
