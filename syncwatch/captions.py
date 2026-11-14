"""Caption appearance (per viewer) and its translation into mpv options.

mpv applies these ``sub-*`` options to text captions (YouTube's VTT/SRT);
subtitle files that carry their own styling (ASS) keep theirs.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, fields, replace

_HEX = re.compile(r"^#[0-9A-Fa-f]{6}$")


@dataclass(frozen=True)
class CaptionStyle:
    font: str = ""               # "" = mpv's default sans-serif
    size: int = 38               # mpv units: font height at 720p video height
    bold: bool = False
    text_color: str = "#FFFFFF"
    text_opacity: int = 100      # percent
    outline: float = 1.6         # outline thickness; box padding when box is on
    outline_color: str = "#000000"
    shadow: float = 0.0
    box: bool = False            # one box behind the caption
    box_color: str = "#000000"
    box_opacity: int = 75        # percent
    margin: int = 22             # distance from the bottom edge

    @classmethod
    def from_dict(cls, d: object) -> CaptionStyle:
        """Build from saved settings, ignoring anything unknown or invalid."""
        base = cls()
        if not isinstance(d, dict):
            return base
        out = {}
        for f in fields(cls):
            v = d.get(f.name)
            default = getattr(base, f.name)
            try:
                if isinstance(default, bool):
                    v = bool(v) if v is not None else default
                elif isinstance(default, int):
                    v = int(v)
                elif isinstance(default, float):
                    v = float(v)
                elif f.name.endswith("color"):
                    v = str(v) if _HEX.match(str(v)) else default
                else:
                    v = str(v)[:100] if v is not None else default
            except (TypeError, ValueError):
                v = default
            out[f.name] = v
        return cls(**out).clamped()

    def clamped(self) -> CaptionStyle:
        def lim(v, lo, hi):
            return max(lo, min(hi, v))
        return replace(self, size=lim(self.size, 10, 150),
                       text_opacity=lim(self.text_opacity, 0, 100),
                       box_opacity=lim(self.box_opacity, 0, 100),
                       outline=lim(self.outline, 0.0, 10.0),
                       shadow=lim(self.shadow, 0.0, 10.0),
                       margin=lim(self.margin, 0, 400))

    def to_dict(self) -> dict:
        return asdict(self)


def mpv_color(rgb: str, opacity: int) -> str:
    """'#RRGGBB' + opacity % -> mpv's '#AARRGGBB' (AA = FF is opaque)."""
    alpha = round(max(0, min(100, opacity)) * 255 / 100)
    return f"#{alpha:02X}{rgb[1:].upper()}"


def to_mpv(s: CaptionStyle) -> dict[str, str]:
    """mpv property values for a style."""
    return {
        "sub-font": s.font or "sans-serif",
        "sub-font-size": str(s.size),
        "sub-bold": "yes" if s.bold else "no",
        "sub-color": mpv_color(s.text_color, s.text_opacity),
        "sub-outline-size": f"{s.outline:g}",
        "sub-outline-color": mpv_color(s.outline_color, s.text_opacity),
        "sub-shadow-offset": f"{s.shadow:g}",
        "sub-border-style": "background-box" if s.box else "outline-and-shadow",
        # in background-box mode this is the box; otherwise the shadow colour
        "sub-back-color": (mpv_color(s.box_color, s.box_opacity) if s.box
                           else mpv_color("#000000", 50)),
        "sub-margin-y": str(s.margin),
    }


PRESETS: dict[str, CaptionStyle] = {
    "Default (white, outlined)": CaptionStyle(),
    "TV style (white on black box)": CaptionStyle(box=True, box_opacity=80, outline=1.0),
    "Translucent box": CaptionStyle(box=True, box_opacity=45, outline=1.0),
    "Yellow, outlined": CaptionStyle(text_color="#FFE14D", outline=2.2),
    "Large, bold": CaptionStyle(size=52, bold=True, outline=2.5),
}
