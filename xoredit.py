import argparse
import string
from typing import Any, Coroutine, List, Tuple
from textual.app import App, ComposeResult
from textual.document._document import EditResult, Location
from textual.document._edit import Edit
from textual.events import Event, Key, Resize
from textual.widgets import Header, Footer
from textual.containers import ScrollableContainer
from textual.widgets import Button, Footer, Header, Static, TextArea

PRINTABLE_BYTES = string.printable.replace("\x0b", "").replace("\x0c", "")
PRINTABLE_BYTES = PRINTABLE_BYTES.encode("utf-8")


class EditArea(TextArea):
    data: List
    listeners: List

    def __init__(self, data):
        self.data = [None] * len(data)
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


class InterleaveArea(TextArea):
    areas: List[EditArea]

    def __init__(self, *areas):
        super().__init__()
        self.read_only = True
        self.areas = areas

    def update(self) -> None:
        maxlen = max(len(area.text) for area in self.areas)
        w = self.size.width - 1
        text = []
        for i in range(0, maxlen, w):
            for area in self.areas:
                text.append(area.text[i : i + w])
            text.append("")
        self.text = "\n".join(text)

    def on_event(self, event: Event) -> Coroutine[Any, Any, None]:
        if isinstance(event, Resize):
            self.update()
        return super().on_event(event)


class XOREditApp(App):
    top_area: EditArea
    bot_area: EditArea
    interleave_area: InterleaveArea
    keystream: List

    def compose(self) -> ComposeResult:
        yield Header()
        yield self.top_area
        yield self.bot_area
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
