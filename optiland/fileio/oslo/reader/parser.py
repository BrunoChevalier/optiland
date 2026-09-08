"""OSLO Data Parser

Parses an OSLO .len file into an OsloDataModel.

Kramer Harrison, 2026
"""

from __future__ import annotations

import math
import re
import warnings
from pathlib import Path
from typing import Any

from optiland.fileio.oslo.model import OsloDataModel, OsloDiagnostic


class OsloDataParser:
    """Parses an OSLO .len file into an OsloDataModel.

    Args:
        filename: Path to the .len file to parse.
    """

    def __init__(self, filename: str, *, strict: bool = False) -> None:
        self.filename = filename
        self.strict = strict
        self.data_model = OsloDataModel()
        self._current_surf_idx = 0
        self._current_surf_data: dict[str, Any] = {}
        self._wavelength_values: list[float] = []
        self._wavelength_weights: list[float] = []
        self._line = 0
        self._ended = False

        # Command dispatch table
        self._dispatch_table = {
            "LEN": self._read_len,
            "EBR": self._read_ebr,
            "OBH": self._read_obh,
            "ANG": self._read_ang,
            "UNI": self._read_uni,
            "AIR": self._read_medium,
            "RFL": self._read_medium,
            "RFH": self._read_medium,
            "AIF": self._read_medium,
            "GLA": self._read_glass,
            "GLF": self._read_glass,
            "RD": self._read_rd,
            "RDF": self._read_rd,
            "CV": self._read_cv,
            "CVF": self._read_cv,
            "CVX": self._read_coeff,
            "RDX": self._read_rdx,
            "TH": self._read_th,
            "THF": self._read_th,
            "AP": self._read_ap,
            "APF": self._read_ap,
            "ASP": self._read_asp,
            "AST": self._read_ast,
            "CC": self._read_cc,
            "AD": self._read_coeff,
            "AE": self._read_coeff,
            "AF": self._read_coeff,
            "AG": self._read_coeff,
            "DCX": self._read_decenter,
            "DCY": self._read_decenter,
            "DCZ": self._read_decenter,
            "TLA": self._read_tilt,
            "TLB": self._read_tilt,
            "TLC": self._read_tilt,
            "WV": self._read_wv,
            "WV2": self._read_wv,
            "WV3": self._read_wv,
            "WW": self._read_ww,
            "NXT": self._read_nxt,
            "GTO": self._read_gto,
            "END": self._read_end,
            "PY": self._read_solve,
            "PK": self._read_pickup,
            "FNO": self._read_fno,
            "NAO": self._read_nao,
            "NAP": self._read_nap,
            "TELE": self._read_tele,
            "DES": self._read_des,
            "PFL": self._read_paraxial,
        }

    def parse(self) -> OsloDataModel:
        """Parse the OSLO file.

        Returns:
            A populated OsloDataModel.
        """
        self.__init__(self.filename, strict=self.strict)
        raw = Path(self.filename).read_bytes()
        try:
            source = raw.decode("utf-8-sig")
        except UnicodeDecodeError:
            source = raw.decode("cp1252")
        for self._line, line in enumerate(source.splitlines(), 1):
            if self._ended:
                break  # Analysis/variable/CCL blocks are not lens prescriptions.
            try:
                for statement in self._statements(line):
                    if self._ended:
                        break
                    tokens = self._tokenize(statement)
                    if not tokens:
                        continue
                    cmd = tokens[0].upper()
                    tokens[0] = cmd
                    if re.fullmatch(r"SNO\d+", cmd):
                        self._read_sno(tokens)
                    elif re.fullmatch(r"W[VW][1-9]\d*", cmd):
                        self._validate_numbers(tokens)
                        self._read_spectrum(tokens)
                    elif re.fullmatch(r"AS\d+", cmd):
                        self._validate_numbers(tokens)
                        self._read_coeff(tokens)
                    elif cmd in self._dispatch_table:
                        self._validate_numbers(tokens)
                        self._dispatch_table[cmd](tokens)
                    elif cmd in {"DRW", "LDP", "CBK", "ELMDF1", "ELMDF2"}:
                        continue  # Drawing-only data, without optical effects.
                    else:
                        self._unsupported(cmd)
            except (ValueError, IndexError) as exc:
                raise ValueError(f"{self.filename}:{self._line}: {exc}") from exc
        if not self._ended:
            self._read_end(["END"])

        values = self._wavelength_values or [0.58756, 0.48613, 0.65627]
        weights = self._wavelength_weights + [1.0] * len(values)
        self.data_model.wavelengths["values"] = values
        self.data_model.wavelengths["weights"] = weights[: len(values)]

        return self.data_model

    def _unsupported(self, command: str, message: str = "unsupported command") -> None:
        diagnostic = OsloDiagnostic(
            command, self._line, self._current_surf_idx, message
        )
        self.data_model.diagnostics.append(diagnostic)
        detail = (
            f"{self.filename}:{self._line}: OSLO {command} at surface "
            f"{self._current_surf_idx}: {message}; import may be incomplete"
        )
        if self.strict:
            raise ValueError(detail)
        warnings.warn(detail, UserWarning, stacklevel=3)

    @staticmethod
    def _validate_numbers(tokens: list[str]) -> None:
        for token in tokens[1:]:
            try:
                value = float(token)
            except ValueError:
                continue
            if not math.isfinite(value):
                raise ValueError(f"{tokens[0]} requires finite numeric input")

    @staticmethod
    def _statements(line: str) -> list[str]:
        """Split commands and comments outside quoted, backslash-escaped text."""
        statements = []
        start = 0
        quoted = escaped = False
        for i, char in enumerate(line):
            if escaped:
                escaped = False
                continue
            if char == "\\" and quoted:
                escaped = True
            elif char == '"':
                quoted = not quoted
            elif not quoted:
                if line[i : i + 2] == "//":
                    line = line[:i]
                    break
                if char == ";":
                    statements.append(line[start:i])
                    start = i + 1
        if quoted:
            raise ValueError("unterminated quoted string")
        statements.append(line[start:])
        return statements

    def _tokenize(self, line: str) -> list[str]:
        """Tokenize a line, respecting double quotes."""
        # This regex finds either strings in quotes or non-whitespace sequences
        return re.findall(r'"(?:\\.|[^"\\])*"|[^\s,]+', line)

    def _read_len(self, tokens: list[str]) -> None:
        # LEN NEW "lens_name" <scaling> <total_surfaces>
        if len(tokens) >= 5 and tokens[1].upper() == "NEW":
            self.data_model.name = tokens[2].strip('"')
            self.data_model.scaling = float(tokens[3])
            self.data_model.num_surfaces = int(tokens[4])

    def _read_ebr(self, tokens: list[str]) -> None:
        # EBR <float> (Entrance Beam Radius)
        self.data_model.aperture = {"EPD": 2.0 * float(tokens[1])}

    def _read_fno(self, tokens: list[str]) -> None:
        # FNO <float> (F-Number)
        self.data_model.aperture = {"FNO": float(tokens[1])}

    def _read_nao(self, tokens: list[str]) -> None:
        # NAO <float> (Object NA)
        self.data_model.aperture = {"NAO": float(tokens[1])}

    def _read_nap(self, tokens: list[str]) -> None:
        self.data_model.aperture = {"NAP": float(tokens[1])}

    def _read_tele(self, tokens: list[str]) -> None:
        if tokens[1].upper() not in {"ON", "OFF", "0", "1"}:
            raise ValueError("TELE expects ON or OFF")
        self.data_model.settings["telecentric"] = tokens[1].upper() in {"ON", "1"}

    def _read_obh(self, tokens: list[str]) -> None:
        # OBH <float> (Object Height)
        self.data_model.fields = {"type": "object_height", "y": [float(tokens[1])]}

    def _read_ang(self, tokens: list[str]) -> None:
        # ANG <float> (Field Angle)
        self.data_model.fields = {"type": "angle", "y": [float(tokens[1])]}

    def _read_uni(self, tokens: list[str]) -> None:
        self.data_model.units = float(tokens[1])
        if self.data_model.units <= 0:
            raise ValueError("UNI must be positive (millimeters per lens unit)")

    def _read_des(self, tokens: list[str]) -> None:
        self.data_model.notes["DES"] = " ".join(tokens[1:]).strip('"')

    def _read_sno(self, tokens: list[str]) -> None:
        cmd = tokens[0].upper()
        content = " ".join(tokens[1:]).strip('"')
        self.data_model.notes[cmd] = content

    def _read_medium(self, tokens: list[str]) -> None:
        # AIR or RFL
        cmd = tokens[0].upper()
        self._current_surf_data["material"] = {"AIF": "AIR", "RFH": "RFL"}.get(cmd, cmd)

    def _read_glass(self, tokens: list[str]) -> None:
        # GLA <glass_def>
        # GLA BK7
        # GLA 1.573 1.573 1.573
        # GLA MOD G1 1.6489 1.662...
        self._current_surf_data["material"] = "GLA " + " ".join(tokens[1:])

    def _read_paraxial(self, tokens: list[str]) -> None:
        self._current_surf_data["PFL"] = float(tokens[1])

    def _read_rd(self, tokens: list[str]) -> None:
        self._current_surf_data["RD"] = float(tokens[1]) or math.inf

    def _read_cv(self, tokens: list[str]) -> None:
        curvature = float(tokens[1])
        self._current_surf_data["RD"] = 1 / curvature if curvature else math.inf

    def _read_rdx(self, tokens: list[str]) -> None:
        radius = float(tokens[1])
        self._current_surf_data["CVX"] = 1 / radius if radius else 0.0

    def _read_asp(self, tokens: list[str]) -> None:
        kind = {"0": "ADO", "1": "ASR", "2": "ASX"}.get(tokens[1], tokens[1].upper())
        self._current_surf_data["ASP"] = kind
        if kind not in {"ADO", "ASR", "ASX", "ARA"}:
            self._unsupported("ASP", f"asphere type {kind} is not mapped")

    def _read_th(self, tokens: list[str]) -> None:
        self._current_surf_data["TH"] = float(tokens[1])

    def _read_ap(self, tokens: list[str]) -> None:
        self._current_surf_data["AP"] = float(tokens[1])

    def _read_ast(self, tokens: list[str]) -> None:
        self._current_surf_data["AST"] = True

    def _read_cc(self, tokens: list[str]) -> None:
        self._current_surf_data["CC"] = float(tokens[1])

    def _read_coeff(self, tokens: list[str]) -> None:
        cmd = tokens[0].upper()
        self._current_surf_data[cmd] = float(tokens[1])

    def _read_decenter(self, tokens: list[str]) -> None:
        cmd = tokens[0].upper()
        self._current_surf_data[cmd] = float(tokens[1])

    def _read_tilt(self, tokens: list[str]) -> None:
        cmd = tokens[0].upper()
        self._current_surf_data[cmd] = float(tokens[1])

    def _read_wv(self, tokens: list[str]) -> None:
        self._read_spectrum(tokens)

    def _read_ww(self, tokens: list[str]) -> None:
        self._read_spectrum(tokens)

    def _read_spectrum(self, tokens: list[str]) -> None:
        cmd = tokens[0]
        values = [float(t) for t in tokens[1:]]
        wavelength = cmd.startswith("WV")
        if not values or any(v <= 0 if wavelength else v < 0 for v in values):
            raise ValueError(f"{cmd} requires positive wavelengths/nonnegative weights")
        attr = "_wavelength_values" if wavelength else "_wavelength_weights"
        if len(cmd) == 2:
            setattr(self, attr, values)
            return
        index = int(cmd[2:]) - 1
        if index > 1000 or len(values) != 1:
            raise ValueError(f"{cmd} requires one value and a bounded wavelength index")
        target = getattr(self, attr)
        defaults = [0.58756, 0.48613, 0.65627] if wavelength else [1.0] * (index + 1)
        while len(target) <= index:
            if len(target) >= len(defaults):
                if len(target) != index:
                    raise ValueError(f"{cmd} leaves undefined wavelength slots")
                target.append(values[0])
            else:
                target.append(defaults[len(target)])
        target[index] = values[0]

    def _read_nxt(self, tokens: list[str]) -> None:
        # Save current surface and increment index
        self.data_model.surfaces[self._current_surf_idx] = self._current_surf_data
        self._current_surf_idx += 1
        self._current_surf_data = self.data_model.surfaces.get(
            self._current_surf_idx, {}
        )

    def _read_gto(self, tokens: list[str]) -> None:
        self.data_model.surfaces[self._current_surf_idx] = self._current_surf_data
        index = int(tokens[1])
        if not 0 <= index <= self.data_model.num_surfaces:
            raise ValueError("GTO surface is outside the declared lens")
        self._current_surf_idx = index
        self._current_surf_data = self.data_model.surfaces.get(index, {})

    def _read_end(self, tokens: list[str]) -> None:
        self.data_model.surfaces[self._current_surf_idx] = self._current_surf_data
        self._ended = True

    def _read_solve(self, tokens: list[str]) -> None:
        # TODO: Support more solves
        if tokens[0].upper() == "PY":
            self._current_surf_data["PY"] = float(tokens[1])

    def _read_pickup(self, tokens: list[str]) -> None:
        # TODO: Support standard pickups
        # PK <surf> <cmd>
        self._current_surf_data["PK"] = tokens[1:]
