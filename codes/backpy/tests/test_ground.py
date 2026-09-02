"""The ground check, without a model behind it.

What the model says is measured separately and by running it. What is tested
here is everything that happens to its answer afterwards: which cells are
allowed to exist, which findings survive disagreement between readings, and
where on the frame a box ends up. A mistake in the last of those puts a red
rectangle over clean sand and tells somebody their footage is wrong, which is a
worse failure than missing the mark altogether.
"""

from __future__ import annotations

import json

from app.tools.ground import AGREEMENT, agree, parse_reading


def reading(*items) -> str:
    return json.dumps({"differences": list(items), "verdict": "..."})


def difference(cell="C3", side="incoming", x=0.5, y=0.5, what="a dark smudge"):
    return {
        "cell": cell,
        "present_in": side,
        "what": what,
        "x_in_cell": x,
        "y_in_cell": y,
        "width_in_cell": 0.2,
        "height_in_cell": 0.2,
    }


class TestParsing:
    def test_reads_a_well_formed_answer(self):
        found = parse_reading(reading(difference()), columns=4, rows=3)
        assert len(found) == 1
        assert found[0]["cell"] == "C3"
        assert found[0]["column"] == 2
        assert found[0]["row"] == 2

    def test_left_and_right_become_outgoing_and_incoming(self):
        """The image says LEFT and RIGHT, so the model sometimes answers that way."""
        found = parse_reading(
            reading(difference(side="left"), difference(cell="B2", side="right")),
            columns=4,
            rows=3,
        )
        assert [item["present_in"] for item in found] == ["outgoing", "incoming"]

    def test_a_cell_outside_the_grid_is_dropped(self):
        """E1 does not exist on a four column grid, and a box drawn from it
        would land somewhere arbitrary."""
        found = parse_reading(
            reading(difference(cell="E1"), difference(cell="Z9")), columns=4, rows=3
        )
        assert found == []

    def test_a_missing_position_falls_back_to_the_middle_of_the_cell(self):
        """Better the centre of the right cell than nothing at all: the cell is
        the finding, and the position within it is the refinement."""
        found = parse_reading(
            json.dumps({"differences": [{"cell": "A1", "present_in": "incoming"}]}),
            columns=4,
            rows=3,
        )
        assert found[0]["x_in_cell"] == 0.5
        assert found[0]["y_in_cell"] == 0.5

    def test_junk_is_survived(self):
        for text in ("", "not json", "[]", json.dumps({"differences": "nope"})):
            assert parse_reading(text, columns=4, rows=3) == []


class TestAgreement:
    def test_a_finding_one_reading_saw_is_not_a_finding(self):
        readings = [
            parse_reading(reading(difference()), 4, 3),
            parse_reading(reading(), 4, 3),
            parse_reading(reading(), 4, 3),
        ]
        assert agree(readings, 4, 3) == []

    def test_two_readings_out_of_three_is_enough(self):
        """Three would throw away a real finding whenever one read wandered."""
        readings = [
            parse_reading(reading(difference()), 4, 3),
            parse_reading(reading(difference()), 4, 3),
            parse_reading(reading(), 4, 3),
        ]
        agreed = agree(readings, 4, 3)
        assert len(agreed) == 1
        assert agreed[0].seen_in_reads == AGREEMENT

    def test_the_same_mark_described_differently_is_one_finding(self):
        """It comes back as a smudge, a spot and a blemish. That is one mark."""
        readings = [
            parse_reading(reading(difference(what="a dark smudge")), 4, 3),
            parse_reading(reading(difference(what="a dark spot on the sand")), 4, 3),
            parse_reading(reading(difference(what="a blemish")), 4, 3),
        ]
        agreed = agree(readings, 4, 3)
        assert len(agreed) == 1
        assert agreed[0].what == "a dark spot on the sand"

    def test_one_reading_cannot_outvote_the_others_by_repeating_itself(self):
        readings = [
            parse_reading(reading(difference(), difference(), difference()), 4, 3),
            parse_reading(reading(), 4, 3),
            parse_reading(reading(), 4, 3),
        ]
        assert agree(readings, 4, 3) == []

    def test_the_same_cell_on_each_side_is_two_findings(self):
        """Something gone from the left and something new on the right are
        different events, even in the same cell."""
        readings = [
            parse_reading(reading(difference(side="outgoing"), difference()), 4, 3),
            parse_reading(reading(difference(side="outgoing"), difference()), 4, 3),
        ]
        assert len(agree(readings, 4, 3)) == 2


class TestWhereTheBoxLands:
    def test_a_cell_maps_to_its_own_corner_of_the_frame(self):
        """C3 on a four by three grid is the third column and the bottom row,
        so the box belongs between 0.5 and 0.75 across and below two thirds
        down. Getting this wrong points at clean sand."""
        readings = [parse_reading(reading(difference(x=0.5, y=0.5)), 4, 3)] * 2
        box = agree(readings, 4, 3)[0]
        centre_x = box.x + box.width / 2
        centre_y = box.y + box.height / 2
        assert 0.5 < centre_x < 0.75
        assert 0.666 < centre_y < 1.0

    def test_the_position_inside_the_cell_is_honoured(self):
        """The model volunteers "the lower right of C3" unprompted, and that
        detail is the difference between pointing at a mark and pointing at a
        ninth of the picture."""
        low = agree([parse_reading(reading(difference(x=0.1, y=0.1)), 4, 3)] * 2, 4, 3)[0]
        high = agree([parse_reading(reading(difference(x=0.9, y=0.9)), 4, 3)] * 2, 4, 3)[0]
        assert low.x < high.x
        assert low.y < high.y

    def test_a_wandering_reading_moves_the_box_but_does_not_carry_it(self):
        readings = [
            parse_reading(reading(difference(x=0.5)), 4, 3),
            parse_reading(reading(difference(x=0.5)), 4, 3),
            parse_reading(reading(difference(x=0.0)), 4, 3),
        ]
        box = agree(readings, 4, 3)[0]
        centre_x = (box.x + box.width / 2 - 0.5) * 4  # back into cell fractions
        assert 0.45 < centre_x < 0.55

    def test_a_box_never_starts_outside_the_frame(self):
        readings = [parse_reading(reading(difference(cell="A1", x=0.0, y=0.0)), 4, 3)] * 2
        box = agree(readings, 4, 3)[0]
        assert box.x >= 0.0
        assert box.y >= 0.0
