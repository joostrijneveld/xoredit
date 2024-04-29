import argparse
import string
from typing import Any, Coroutine, List

from textual.app import App, ComposeResult
from textual.containers import Vertical
from textual.document._document import EditResult, Selection
from textual.document._edit import Edit
from textual.events import Event, Resize
from textual.widgets import Footer, Static, TextArea

PRINTABLE_BYTES = string.printable.replace("\x0b", "").replace("\x0c", "")
PRINTABLE_BYTES = PRINTABLE_BYTES.encode("utf-8")
OFFSET_DELTA = 5


class EditArea(TextArea):
    data: List
    listeners: List
    position_footer: Static = None

    def __init__(self, data):
        self.data = [None] * len(data)
        self.position_footer = Static()
        super().__init__()
        self.update()

    @staticmethod
    def render_symbol(c):
        if c is None:
            return "_"  # TODO make this gray to differentiate
        if c == ord("\r"):
            return chr(0x21A9)
        if c == ord("\n"):
            return chr(0x21B5)
        if c == ord("\t"):
            return chr(0x21E5)
        if c == ord(" "):
            return chr(0x2423)
        if c not in PRINTABLE_BYTES:
            return chr(0x25A2)
        return chr(c)

    def update(self):
        # TODO if we're using this function to flatten a newline, the cursor location will be destroyed
        old_location = self.cursor_location
        self.text = "".join(map(EditArea.render_symbol, self.data))
        self.move_cursor(old_location)

    def action_delete_left(self) -> None:
        self.overwrite(self.cursor_location[1] - 1, None)
        self.move_cursor(self.get_cursor_left_location())
        self.update()

    def action_delete_right(self) -> None:
        self.overwrite(self.cursor_location[1], None)

    def overwrite(self, index, v, emit=True):
        # prevent out of bound overwrites
        if index >= len(self.data):
            return
        self.data[index] = v
        self.update()
        if emit:
            app.update(self, index, v)

    def edit(self, edit: Edit) -> EditResult:
        _, x_f = edit.from_location
        y_t, x_t = edit.to_location
        # prevent attempts to edit out of bounds
        if x_t > len(edit.text):
            edit.to_location = (y_t, x_t)
        for i, c in enumerate(edit.text):
            self.overwrite(x_f + i, ord(c))
        edit.to_location = (_, x_t + 1)
        super().edit(edit)
        # to fix, e.g., inserted newlines
        self.update()

    def watch_selection(
        self, previous_selection: Selection, selection: Selection
    ) -> None:
        if self.has_focus:
            self.update_position_footer(selection.start[1])

    def update_position_footer(self, position):
        self.position_footer.update(f"Cursor offset: {position}")

    def on_focus(self):
        self.update_position_footer(self.cursor_location[1])

    def on_blur(self):
        self.position_footer.update("")


class InterleaveArea(TextArea):
    areas: List[EditArea]
    show_pipes: bool = True
    show_offsets: bool = True

    def __init__(self, *areas):
        super().__init__()
        self.read_only = True
        self.areas = areas

    def update(self) -> None:
        maxlen = max(len(area.text) for area in self.areas)
        # TODO account for scroll bar
        w = self.size.width - 1
        text = []
        for i in range(0, maxlen, w):
            if self.show_offsets:
                pad_len = (OFFSET_DELTA - (i % OFFSET_DELTA)) % OFFSET_DELTA
                offsets = " " * pad_len
                # don't show the last offset on a line, as it'll overflow
                for j in range(0, w - OFFSET_DELTA, OFFSET_DELTA):
                    offsets += f"{pad_len + i + j:<5}"
                text.append(offsets)
            for area in self.areas:
                text.append(area.text[i : i + w])
            if self.show_pipes:
                pipeline = ""
                for b in self.app.keystream[i : i + w]:
                    if b & (64 + 32) == (64 + 32):
                        pipeline += "|"
                    else:
                        pipeline += " "
                text.append(pipeline)
            text.append("")
            text.append("")
        self.text = "\n".join(text)

    def on_event(self, event: Event) -> Coroutine[Any, Any, None]:
        if isinstance(event, Resize):
            self.update()
        return super().on_event(event)

    def toggle_pipes(self):
        """
        Toggle whether to show pipes where the XOR result has the 32 and 64 bit set.
        This is likely indicative of punctuation opposing a capital letter.
        """
        self.show_pipes = not self.show_pipes
        self.update()

    def toggle_offsets(self):
        """
        Toggle whether to show pipes where the XOR result has the 32 and 64 bit set.
        This is likely indicative of punctuation opposing a capital letter.
        """
        self.show_offsets = not self.show_offsets
        self.update()


class XOREditApp(App):
    top_area: EditArea
    bot_area: EditArea
    interleave_area: InterleaveArea
    keystream: List

    BINDINGS = [
        ("ctrl+t", "toggle_pipes", "Toggle word boundary heuristic."),
        ("ctrl+n", "toggle_offsets", "Toggle offset indicators."),
    ]

    def compose(self) -> ComposeResult:
        with Vertical():
            yield self.top_area
            yield self.top_area.position_footer
        with Vertical():
            yield self.bot_area
            yield self.bot_area.position_footer
        yield self.interleave_area
        yield Footer()

    def load_data(self, c1, c2):
        self.top_area = EditArea(c1)
        self.bot_area = EditArea(c2)
        self.interleave_area = InterleaveArea(self.top_area, self.bot_area)
        self.keystream = [a ^ b for a, b in zip(c1, c2)]

    def update(self, source, index, v):
        if source is self.bot_area:
            dest = self.top_area
        else:
            dest = self.bot_area
        new_val = None
        if v is not None and index < len(self.keystream):
            new_val = self.keystream[index] ^ v
        dest.overwrite(index, new_val, emit=False)
        dest.update()
        self.interleave_area.update()

    def action_toggle_pipes(self):
        self.interleave_area.toggle_pipes()

    def action_toggle_offsets(self):
        self.interleave_area.toggle_offsets()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Assist in cryptanalysis of ciphertexts that result from an XOR encryption using a repeated key stream.",
    )

    parser.add_argument(
        "files",
        metavar="FILE",
        nargs=2,
        help="Binary file containing one of the two ciphertexts.",
    )
    args = parser.parse_args()

    ciphertexts = []
    for fname in args.files:
        with open(fname, "rb") as f:
            ciphertexts.append(f.read())

    app = XOREditApp()
    app.load_data(*ciphertexts)
    app.run()
