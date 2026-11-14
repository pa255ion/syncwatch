"""Window for adjusting caption appearance; changes apply live."""

from __future__ import annotations

from dataclasses import replace

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QFontDatabase
from PySide6.QtWidgets import (QCheckBox, QColorDialog, QComboBox, QDialog,
                               QDialogButtonBox, QDoubleSpinBox, QFormLayout,
                               QHBoxLayout, QLabel, QPushButton, QSlider,
                               QSpinBox, QVBoxLayout, QWidget)

from .captions import PRESETS, CaptionStyle

DEFAULT_FONT = "(default)"
PREVIEW_TEXT = "Captions will look like this"


class ColorButton(QPushButton):
    changed = Signal(str)

    def __init__(self, color: str, title: str):
        super().__init__()
        self._title = title
        self.setFixedWidth(64)
        self.set_color(color)
        self.clicked.connect(self._pick)

    def set_color(self, color: str) -> None:
        self.color = color
        self.setStyleSheet(f"background-color: {color}; border: 1px solid #888;")

    def _pick(self) -> None:
        c = QColorDialog.getColor(QColor(self.color), self, self._title)
        if c.isValid():
            self.set_color(c.name().upper())
            self.changed.emit(self.color)


def _percent_slider() -> tuple[QSlider, QLabel, QWidget]:
    slider = QSlider(Qt.Orientation.Horizontal)
    slider.setRange(0, 100)
    label = QLabel()
    label.setMinimumWidth(40)
    slider.valueChanged.connect(lambda v: label.setText(f"{v}%"))
    box = QWidget()
    lay = QHBoxLayout(box)
    lay.setContentsMargins(0, 0, 0, 0)
    lay.addWidget(slider, 1)
    lay.addWidget(label)
    return slider, label, box


class CaptionStyleDialog(QDialog):
    style_changed = Signal(object)  # CaptionStyle

    def __init__(self, style: CaptionStyle, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Caption style")
        self._loading = False

        self.preset = QComboBox()
        self.preset.addItem("Choose a preset …")
        self.preset.addItems(list(PRESETS))

        self.font = QComboBox()
        self.font.setEditable(True)
        self.font.addItem(DEFAULT_FONT)
        self.font.addItems(QFontDatabase.families())
        self.size = QSpinBox(minimum=10, maximum=150)
        self.bold = QCheckBox("Bold")

        self.text_color = ColorButton("#FFFFFF", "Text colour")
        self.text_opacity, _, text_op_box = _percent_slider()

        self.outline = QDoubleSpinBox(minimum=0, maximum=10, singleStep=0.5, decimals=1)
        self.outline_color = ColorButton("#000000", "Outline colour")
        self.shadow = QDoubleSpinBox(minimum=0, maximum=10, singleStep=0.5, decimals=1)

        self.box = QCheckBox("Box behind the text")
        self.box_color = ColorButton("#000000", "Box colour")
        self.box_opacity, _, box_op_box = _percent_slider()

        self.margin = QSpinBox(minimum=0, maximum=400, suffix=" px")

        def row(*ws):
            w = QWidget()
            lay = QHBoxLayout(w)
            lay.setContentsMargins(0, 0, 0, 0)
            for x in ws:
                lay.addWidget(x)
            lay.addStretch(1)
            return w

        form = QFormLayout()
        form.addRow("Preset", self.preset)
        form.addRow("Font", self.font)
        form.addRow("Size", row(self.size, self.bold))
        form.addRow("Text colour", self.text_color)
        form.addRow("Text opacity", text_op_box)
        form.addRow("Outline", row(self.outline, self.outline_color))
        form.addRow("Shadow", self.shadow)
        form.addRow("Background", row(self.box, self.box_color))
        form.addRow("Box opacity", box_op_box)
        form.addRow("From bottom", self.margin)

        # rough preview for when nothing is playing; the video shows the real thing
        self.preview = QLabel(PREVIEW_TEXT)
        self.preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        backdrop = QWidget()
        backdrop.setObjectName("backdrop")
        backdrop.setStyleSheet("#backdrop { background: qlineargradient(x1:0, y1:0, x2:1, y2:1,"
                               " stop:0 #4a6a8a, stop:1 #c8a46a); }")
        backdrop.setMinimumHeight(90)
        bl = QVBoxLayout(backdrop)
        bl.addStretch(1)
        bl.addWidget(self.preview, 0, Qt.AlignmentFlag.AlignHCenter)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close
                                   | QDialogButtonBox.StandardButton.RestoreDefaults)
        buttons.rejected.connect(self.close)
        buttons.button(QDialogButtonBox.StandardButton.RestoreDefaults).clicked.connect(
            lambda: self.set_style(CaptionStyle(), emit=True))

        lay = QVBoxLayout(self)
        lay.addLayout(form)
        lay.addWidget(backdrop)
        lay.addWidget(QLabel("Changes apply immediately and only for you."))
        lay.addWidget(buttons)

        for w in (self.size, self.margin, self.text_opacity, self.box_opacity):
            w.valueChanged.connect(self._changed)
        for w in (self.outline, self.shadow):
            w.valueChanged.connect(self._changed)
        for w in (self.bold, self.box):
            w.toggled.connect(self._changed)
        for w in (self.text_color, self.outline_color, self.box_color):
            w.changed.connect(self._changed)
        self.font.currentTextChanged.connect(self._changed)
        self.preset.activated.connect(self._apply_preset)

        self.set_style(style)

    def set_style(self, s: CaptionStyle, emit: bool = False) -> None:
        self._loading = True
        self.font.setCurrentText(s.font or DEFAULT_FONT)
        self.size.setValue(s.size)
        self.bold.setChecked(s.bold)
        self.text_color.set_color(s.text_color)
        self.text_opacity.setValue(s.text_opacity)
        self.outline.setValue(s.outline)
        self.outline_color.set_color(s.outline_color)
        self.shadow.setValue(s.shadow)
        self.box.setChecked(s.box)
        self.box_color.set_color(s.box_color)
        self.box_opacity.setValue(s.box_opacity)
        self.margin.setValue(s.margin)
        self._loading = False
        self._update_enabled()
        self._update_preview()
        if emit:
            self.style_changed.emit(self.current())

    def current(self) -> CaptionStyle:
        font = self.font.currentText().strip()
        return CaptionStyle(
            font="" if font in ("", DEFAULT_FONT) else font,
            size=self.size.value(), bold=self.bold.isChecked(),
            text_color=self.text_color.color, text_opacity=self.text_opacity.value(),
            outline=self.outline.value(), outline_color=self.outline_color.color,
            shadow=self.shadow.value(), box=self.box.isChecked(),
            box_color=self.box_color.color, box_opacity=self.box_opacity.value(),
            margin=self.margin.value()).clamped()

    def _apply_preset(self, index: int) -> None:
        name = self.preset.itemText(index)
        if name in PRESETS:
            # keep the chosen font across presets
            self.set_style(replace(PRESETS[name], font=self.current().font), emit=True)
        self.preset.setCurrentIndex(0)

    def _changed(self, *_):
        if self._loading:
            return
        self._update_enabled()
        self._update_preview()
        self.style_changed.emit(self.current())

    def _update_enabled(self) -> None:
        on = self.box.isChecked()
        self.box_color.setEnabled(on)
        self.box_opacity.setEnabled(on)

    def _update_preview(self) -> None:
        s = self.current()

        def rgba(hex_, pct):
            c = QColor(hex_)
            return f"rgba({c.red()},{c.green()},{c.blue()},{pct / 100:.2f})"

        bg = rgba(s.box_color, s.box_opacity) if s.box else "transparent"
        family = f"font-family: '{s.font}';" if s.font else ""
        # QSS can't draw text outlines, so the preview omits them
        self.preview.setStyleSheet(
            f"color: {rgba(s.text_color, s.text_opacity)}; background: {bg};"
            f"{family} font-size: {max(10, round(s.size * 0.55))}px;"
            f"font-weight: {'bold' if s.bold else 'normal'}; padding: 2px 8px;")
