import argparse
import string
from typing import Any, Coroutine, List, Tuple

from textual.app import App, ComposeResult
from textual.containers import Vertical
from textual.document._document import EditResult, Location, Selection
from textual.document._edit import Edit
from textual.events import Event, Resize
from textual.widgets import Footer, Static, TextArea
from textual.binding import Binding

PRINTABLE_BYTES = string.printable.replace("\x0b", "").replace("\x0c", "")
PRINTABLE_BYTES = PRINTABLE_BYTES.encode("utf-8")
OFFSET_DELTA = 5
PLACEHOLDER = "_"


class EditArea(TextArea):
    data: List
    position_footer: Static = None

    def __init__(self, data):
        self.data = [None] * len(data)
        self.position_footer = Static()
        super().__init__()
        self.text = PLACEHOLDER * len(data)

    def fixup_and_edit(self, edit: Edit, emit=True):
        """Update the backing data, insert appropriate placeholder."""
        yf, xf = edit.from_location
        yt, xt = edit.to_location
        assert yf == 0 and yt == 0, "Assuming we're working on line 0"
        padding = xt - xf - len(edit.text)
        # rebuild the backing data array, ensuring it stays the same length
        assert xf + len(edit.text) + padding + (len(self.data) - xt) == len(self.data)
        self.data = (
            self.data[:xf]
            + list(map(ord, edit.text))
            + [None] * padding
            + self.data[xt:]
        )[: len(self.data)]
        # add the appropriate padding to the edit.text
        if emit:
            app.spread_edit(self, edit)
        edit.text = edit.text + padding * PLACEHOLDER
        edit.text = EditArea.clean_whitespace(edit.text)
        return self.edit(edit)

    def insert(
        self,
        text: str,
        location: Tuple[int] | None = None,
        *,
        maintain_selection_offset: bool = True,
    ) -> EditResult:
        if location is None:
            location = self.cursor_location
        end = location[0], location[1] + len(text)
        return self.replace(
            text, location, end, maintain_selection_offset=maintain_selection_offset
        )

    def delete(
        self,
        start: Tuple[int, int],
        end: Tuple[int, int],
        *,
        maintain_selection_offset: bool = True,
    ) -> EditResult:
        """
        Override delete operation;
        - replace the contents rather than deleting
        - maintain the correct cursor movement
        """
        # if the selection was done right-to-left, flip it
        if start[1] > end[1]:
            start, end = end, start
        e = self.fixup_and_edit(Edit("", start, end, maintain_selection_offset))
        self.move_cursor(start)
        return e

    def replace(
        self,
        insert: str,
        start: Tuple[int],
        end: Tuple[int],
        *,
        maintain_selection_offset: bool = True,
    ) -> EditResult:
        """Override replace so that it is always of fixed length."""
        # if the selection was done right-to-left, flip it
        if start[1] > end[1]:
            start, end = end, start
        # compute selection length
        delta = end[1] - start[1]
        # pad text to the length of the selection
        pad_length = max(0, delta - len(insert))
        # possibly increase `end`, if replacement is longer than selection
        new_end = end[0], start[1] + len(insert) + pad_length
        e = self.fixup_and_edit(Edit(insert, start, new_end, maintain_selection_offset))
        # move cursor to end of insert, not end of selection
        # TODO somehow this doesn't work correctly when replacing a long selection with a short insert;
        #  the cursor will move to the end of th selection either way
        self.move_cursor((start[0], start[1] + len(insert)))
        return e

    def redo(self, emit=True) -> None:
        pass

    def undo(self, emit=True) -> None:
        pass

    @staticmethod
    def render_symbol(c):
        if c is None:
            return PLACEHOLDER
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

    @staticmethod
    def clean_whitespace(s):
        """Replace newlines by unicode symbols, etc."""
        return "".join(EditArea.render_symbol(ord(c)) for c in s)

    def edit(self, edit: Edit) -> EditResult:
        # prevent exceeding length of data
        y_f, x_f = edit.from_location
        y_t, x_t = edit.to_location
        bound = len(self.data)
        if x_t > bound:
            edit.to_location = (y_t, bound)
        if x_f + len(edit.text) > bound:
            edit.text = edit.text[: bound - x_f]
        return super().edit(edit)

    def watch_selection(
        self, previous_selection: Selection, selection: Selection
    ) -> None:
        """Update footer whenever selection property changes.
        The selection is updated whenever the cursor is moved."""
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

    def populate(self) -> None:
        maxlen = max(len(area.text) for area in self.areas)
        # account for scroll bar
        w = self.size.width - 3
        text = []
        for i in range(0, maxlen, w):
            if self.show_offsets:
                pad_len = (OFFSET_DELTA - (i % OFFSET_DELTA)) % OFFSET_DELTA
                offsets = " " * pad_len
                for j in range(0, w, OFFSET_DELTA):
                    # don't show the last offset on a line, as it'll overflow
                    if len(offsets) < w - 5:
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
            self.populate()
        return super().on_event(event)

    def toggle_pipes(self):
        """
        Toggle whether to show pipes where the XOR result has the 32 and 64 bit set.
        This is likely indicative of punctuation opposing a capital letter.
        """
        self.show_pipes = not self.show_pipes
        self.populate()

    def toggle_offsets(self):
        """
        Toggle whether to show pipes where the XOR result has the 32 and 64 bit set.
        This is likely indicative of punctuation opposing a capital letter.
        """
        self.show_offsets = not self.show_offsets
        self.populate()


class XOREditApp(App):
    top_area: EditArea
    bot_area: EditArea
    interleave_area: InterleaveArea
    keystream: List

    BINDINGS = [
        ("ctrl+t", "toggle_pipes", "Toggle word boundary heuristic."),
        ("ctrl+n", "toggle_offsets", "Toggle offset indicators."),
        Binding(
            "ctrl+x",
            "exchange_selection",
            "Swap selection between areas.",
            priority=True,
        ),
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

    def other_area(self, area):
        if area is self.bot_area:
            return self.top_area
        return self.bot_area

    def spread_edit(self, source: EditArea, edit: Edit):
        dest = self.other_area(source)
        keybytes = self.keystream[edit.from_location[1] : edit.to_location[1]]
        newbytes = "".join([chr(ord(a) ^ k) for a, k in zip(edit.text, keybytes)])

        e = Edit(
            newbytes,
            from_location=edit.from_location,
            to_location=edit.to_location,
            maintain_selection_offset=edit.maintain_selection_offset,
        )
        dest.fixup_and_edit(e, emit=False)
        self.interleave_area.populate()

    def action_exchange_selection(self):
        source = self.focused
        if not isinstance(source, EditArea):
            return
        dest = self.other_area(source)
        start, end = source.selection.start, source.selection.end
        assert start[0] == 0 and end[0] == 0, "Assuming we're working on line 0"
        # if the selection was done right-to-left, flip it
        if start[1] > end[1]:
            start, end = end, start
        # distinguish between cases: do not move the cursor when swapping single characters
        cursor_end = end
        if start == end:
            end = end[0], end[1] + 1
        # ensure the selection fits for both areas
        end = end[0], min(end[1], len(dest.text))
        # swap the displayed text
        source_edit = Edit(
            dest.get_text_range(start, end),
            from_location=start,
            to_location=end,
            maintain_selection_offset=False,
        )
        dest_edit = Edit(
            source.get_text_range(start, end),
            from_location=start,
            to_location=end,
            maintain_selection_offset=False,
        )
        source.edit(source_edit)
        dest.edit(dest_edit)
        # swap the backing bytes
        t = source.data[start[1] : end[1]]
        source.data = (
            source.data[: start[1]]
            + dest.data[start[1] : end[1]]
            + source.data[end[1] :]
        )
        dest.data = dest.data[: start[1]] + t + dest.data[end[1] :]
        # move the cursor appropriately; in particular when swapping without selection
        source.move_cursor(cursor_end)

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
