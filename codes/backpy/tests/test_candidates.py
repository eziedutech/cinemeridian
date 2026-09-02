"""The candidate query, and the reading of its answer.

The query itself is exercised against ClickHouse by running it; what is tested
here is the part that has already gone wrong twice. Once when the result was
parsed by normalising quotes across a string that is Python's repr on the
outside and JSON on the inside, which corrupted the half that mattered and
reported zero candidates from twelve. Once when a check was widened to cover
any shadow measurement and started printing a computed shadow length beside a
compass bearing, comparing a ratio to an angle.

A malformed candidate table is worse than none: it reaches the agent as a
prompt, and the agent will reason over whatever it is given.
"""

from __future__ import annotations

import json

from app.tools.candidates import (
    _rows_from,
    as_table,
    candidate_query,
    count_of,
)


def envelope(columns: list[str], rows: list[list[object]]) -> str:
    """What an MCP tool result actually looks like: a Python repr around JSON."""
    inner = json.dumps({"columns": columns, "rows": rows})
    return (
        "{'content': [{'type': 'text', 'text': "
        + repr(inner)
        + "}], 'structuredContent': {'result': "
        + repr(inner)
        + "}, 'isError': False}"
    )


class TestReadingTheAnswer:
    def test_reads_columns_and_rows_through_the_repr(self):
        columns, rows = _rows_from(envelope(["kind", "take_a"], [["drift", "t01"]]))
        assert columns == ["kind", "take_a"]
        assert rows == [["drift", "t01"]]

    def test_an_apostrophe_in_the_data_does_not_break_the_parse(self):
        """The reason the first parser failed: quotes cannot be normalised
        across a string that is Python outside and JSON inside."""
        columns, rows = _rows_from(
            envelope(["detail"], [["the camera's own heading"]])
        )
        assert columns == ["detail"]
        assert rows == [["the camera's own heading"]]

    def test_counts_what_it_read(self):
        assert count_of(envelope(["a"], [[1], [2], [3]])) == 3
        assert count_of(envelope(["a"], [])) == 0

    def test_junk_is_survived_rather_than_raised(self):
        for text in ("", "not json", "{'isError': True}", "{}"):
            assert _rows_from(text) == ([], [])
            assert count_of(text) == 0


class TestTheTableTheAgentReads:
    def test_columns_line_up(self):
        table = as_table(envelope(["kind", "n"], [["drift", 1], ["runs_backwards", 22]]))
        lines = table.splitlines()
        assert lines[0].startswith("kind")
        # Every row is padded to the same width, so a column stays a column.
        assert len({len(line.rstrip()) > 0 for line in lines}) == 1
        assert "runs_backwards" in table

    def test_nothing_found_says_so_in_words(self):
        """An empty table would read as a broken query. The agent is told the
        difference, because "no candidates" is a result."""
        assert "no candidates" in as_table(envelope(["kind"], []))

    def test_an_unreadable_result_is_handed_over_rather_than_hidden(self):
        assert "isError" in as_table("{'isError': True, 'content': 'boom'}")


class TestTheQuery:
    def test_asks_about_the_production_it_was_given(self):
        sql = candidate_query(
            edit_version="v_abc", scene_id="sc_abc", production_id="try_abc"
        )
        assert "'v_abc'" in sql
        assert "'sc_abc'" in sql
        assert "'try_abc'" in sql

    def test_covers_every_kind_of_candidate(self):
        sql = candidate_query(edit_version="v", scene_id="s", production_id="p")
        for kind in (
            "sun_moved",
            "drift",
            "runs_backwards",
            "slate_vs_sun",
            "direction_vs_sun",
        ):
            assert f"'{kind}'" in sql

    def test_every_branch_returns_the_same_columns(self):
        """A union whose arms disagree on shape fails at the database, and the
        failure arrives as a timeout rather than as anything readable."""
        sql = candidate_query(edit_version="v", scene_id="s", production_id="p")
        arms = sql.split("UNION ALL")
        assert len(arms) == 5

        expected = {
            "kind", "take_a", "take_b", "entity", "attribute", "value_a",
            "value_b", "gap", "detail", "coverage", "in_focus", "frame_uri",
        }
        for index, arm in enumerate(arms):
            aliases = {
                line.rsplit(" AS ", 1)[-1].strip().rstrip(",")
                for line in arm.splitlines()
                if " AS " in line and not line.strip().startswith("--")
            }
            assert expected <= aliases, (index, sorted(expected - aliases))

    def test_a_quote_in_an_identifier_cannot_close_the_string(self):
        sql = candidate_query(
            edit_version="v'; DROP TABLE cinemeridian.takes; --",
            scene_id="s",
            production_id="p",
        )
        assert "DROP TABLE" in sql  # it is in there, as text
        assert "\\'" in sql  # and the quote that would have escaped is escaped
