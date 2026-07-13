"""Unit tests for mini_agent.hashline (hash-anchored line editing).

Run with:  python -m unittest tests.test_hashline -v
(also pytest-compatible:  pytest tests/test_hashline.py)
"""

import os
import tempfile
import unittest

from mini_agent.hashline import (
    apply_edit_block,
    strip_tags,
    tag_lines,
)


def _write(text: str) -> str:
    fd, path = tempfile.mkstemp(suffix=".py")
    os.close(fd)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)
    return path


def _overwrite(path: str, text: str) -> None:
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


def _anchors(text: str) -> list[str]:
    """Return the ``N#HHHH`` anchor of each line (e.g. ``2#abcd``)."""
    out = []
    for line in tag_lines(text).split("\n"):
        out.append(line.split(" ", 1)[0])
    return out


def _read(path: str) -> str:
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


class TestRoundTrip(unittest.TestCase):
    def test_tag_strip_roundtrip(self):
        t = "a\nb\nc\n"
        self.assertEqual(strip_tags(tag_lines(t)), t)

    def test_anchor_format(self):
        a = _anchors("x\ny\n")
        self.assertEqual(a[0], "1#" + a[0].split("#")[1])
        self.assertTrue(all(len(h) == 4 for _, h in (x.split("#") for x in a)))


class TestSingleLine(unittest.TestCase):
    def test_replace_single_line(self):
        src = "line1\nalpha\nline3\n"
        p = _write(src)
        a = _anchors(src)
        res = apply_edit_block(p, f"replace {a[1]} BETA")
        self.assertIn("Edited 1 region", res)
        self.assertEqual(_read(p), "line1\nBETA\nline3\n")

    def test_stale_anchor_rejected(self):
        src = "line1\nalpha\nline3\n"
        p = _write(src)
        a = _anchors(src)
        # corrupt the SAME file so the anchor hash no longer matches
        _overwrite(p, "line1\nZZZZ\nline3\n")
        res = apply_edit_block(p, f"replace {a[1]} BETA")
        self.assertIn("[E_STALE_ANCHOR]", res)
        self.assertEqual(_read(p), "line1\nZZZZ\nline3\n")  # unchanged

    def test_invalid_anchor_format(self):
        p = _write("a\nb\n")
        # valid hex format but line number out of range -> stale anchor error
        res = apply_edit_block(p, "replace 99#aaaa B")
        self.assertIn("[E_STALE_ANCHOR]", res)


class TestMultiLine(unittest.TestCase):
    def test_range_replace(self):
        src = "def f():\n    return 1\n    return 2\n    pass\n"
        p = _write(src)
        a = _anchors(src)
        dsl = f"replace {a[1]} {a[2]}\n    return 99\n    return 100\n    # done"
        self.assertIn("Edited 1 region", apply_edit_block(p, dsl))
        self.assertEqual(
            _read(p),
            "def f():\n    return 99\n    return 100\n    # done\n    pass\n",
        )

    def test_expand_one_line_into_many(self):
        src = "start\nmiddle\nend\n"
        p = _write(src)
        a = _anchors(src)
        dsl = f"replace {a[1]}\nA1\nA2\nA3"
        self.assertIn("Edited 1 region", apply_edit_block(p, dsl))
        self.assertEqual(_read(p), "start\nA1\nA2\nA3\nend\n")

    def test_range_delete(self):
        src = "a\nb\nc\nd\n"
        p = _write(src)
        a = _anchors(src)
        dsl = f"replace {a[1]} {a[2]}"  # empty content -> delete span
        self.assertIn("Edited 1 region", apply_edit_block(p, dsl))
        self.assertEqual(_read(p), "a\nd\n")

    def test_whole_file_delete_refused(self):
        src = "only\n"
        p = _write(src)
        a = _anchors(src)
        res = apply_edit_block(p, f"replace {a[0]} {a[0]}")
        self.assertIn("Refusing to delete the whole file", res)
        self.assertEqual(_read(p), src)


class TestInsertDelete(unittest.TestCase):
    def test_insert_after_multiline(self):
        src = "A\nB\nC\n"
        p = _write(src)
        a = _anchors(src)
        dsl = f"insert_after {a[0]}\nX\nY"
        self.assertIn("Edited 1 region", apply_edit_block(p, dsl))
        self.assertEqual(_read(p), "A\nX\nY\nB\nC\n")

    def test_insert_before(self):
        src = "A\nB\nC\n"
        p = _write(src)
        a = _anchors(src)
        dsl = f"insert_before {a[1]}\nX"
        self.assertIn("Edited 1 region", apply_edit_block(p, dsl))
        self.assertEqual(_read(p), "A\nX\nB\nC\n")

    def test_append_and_prepend(self):
        src = "A\nB\n"
        p = _write(src)
        a = _anchors(src)
        res = apply_edit_block(p, f"append {a[1]}\nZ")
        self.assertIn("Edited 1 region", res)
        self.assertEqual(_read(p), "A\nB\nZ\n")
        # prepend before line 1
        p = _write(src)
        a = _anchors(src)
        res = apply_edit_block(p, f"prepend {a[0]}\nZ")
        self.assertIn("Edited 1 region", res)
        self.assertEqual(_read(p), "Z\nA\nB\n")

    def test_delete_single_line(self):
        src = "A\nB\nC\n"
        p = _write(src)
        a = _anchors(src)
        self.assertIn("Edited 1 region", apply_edit_block(p, f"delete {a[1]}"))
        self.assertEqual(_read(p), "A\nC\n")


class TestMultipleEditsInOneBlock(unittest.TestCase):
    """A single edit block may carry several independent edits (multiple locations)."""

    def test_two_non_overlapping_replaces(self):
        src = "alpha\nbeta\ngamma\ndelta\nepsilon\n"
        p = _write(src)
        a = _anchors(src)
        dsl = f"replace {a[1]} BETA2\nreplace {a[3]} DELTA4"
        res = apply_edit_block(p, dsl)
        self.assertIn("Edited 2 region", res)
        self.assertEqual(
            _read(p),
            "alpha\nBETA2\ngamma\nDELTA4\nepsilon\n",
        )

    def test_mixed_single_and_multiline_in_one_block(self):
        src = "a\nb\nc\nd\ne\n"
        p = _write(src)
        a = _anchors(src)
        # replace line 1 (single) and range 3..4 (multi) in one call
        dsl = f"replace {a[0]} A0\nreplace {a[2]} {a[3]}\nC0\nD0\nE0"
        res = apply_edit_block(p, dsl)
        self.assertIn("Edited 2 region", res)
        self.assertEqual(_read(p), "A0\nb\nC0\nD0\nE0\ne\n")

    def test_adjacent_non_overlapping_edits(self):
        src = "a\nb\nc\nd\n"
        p = _write(src)
        a = _anchors(src)
        dsl = f"replace {a[0]} A!\nreplace {a[1]} B!"
        res = apply_edit_block(p, dsl)
        self.assertIn("Edited 2 region", res)
        self.assertEqual(_read(p), "A!\nB!\nc\nd\n")


class TestOperationsDataset(unittest.TestCase):
    """Data-driven tests on ONE 26-line file ``ID1 a`` .. ``ID26 z``.

    Every line is ``ID{index} {letter}``. Covers each operation and many
    combinations, asserting the resulting file line-by-line.
    """

    def setUp(self):
        self.src = "\n".join(
            f"ID{i} {chr(ord('a') + i - 1)}" for i in range(1, 27)
        ) + "\n"
        self.a = _anchors(self.src)

    @staticmethod
    def L(i):
        return f"ID{i} {chr(ord('a') + i - 1)}"

    def _run(self, dsl):
        p = _write(self.src)
        res = apply_edit_block(p, dsl)
        return res, _read(p)

    def _simulate(self, edits):
        """Independent expectation: apply edits at ORIGINAL line numbers, bottom-up."""
        lines = [self.L(i) for i in range(1, 27)] + [""]
        for ed in sorted(edits, key=lambda e: e["start"], reverse=True):
            s = ed["start"]
            e = ed.get("end", s)
            c = ed.get("content", [])
            op = ed["op"]
            if op == "replace":
                del lines[s - 1:e]
                for nl in reversed(c):
                    lines.insert(s - 1, nl)
            elif op in ("insert_after", "append"):
                for nl in reversed(c):
                    lines.insert(s, nl)
            elif op in ("insert_before", "prepend"):
                for nl in reversed(c):
                    lines.insert(s - 1, nl)
            elif op == "delete":
                del lines[s - 1]
        return "\n".join(lines)

    # ---- single operations ----
    def test_replace_single_first(self):
        res, out = self._run(f"replace {self.a[0]} ID1 ZZZ")
        self.assertIn("Edited 1 region", res)
        self.assertEqual(out, "\n".join(["ID1 ZZZ"] + [self.L(i) for i in range(2, 27)]) + "\n")

    def test_replace_single_last(self):
        res, out = self._run(f"replace {self.a[25]} ID26 ZZZ")
        self.assertIn("Edited 1 region", res)
        self.assertEqual(out, "\n".join([self.L(i) for i in range(1, 26)] + ["ID26 ZZZ"]) + "\n")

    def test_replace_range(self):
        res, out = self._run(f"replace {self.a[4]} {self.a[7]}\nN1\nN2\nN3\nN4")
        self.assertIn("Edited 1 region", res)
        exp = [self.L(i) for i in range(1, 5)] + ["N1", "N2", "N3", "N4"] + [self.L(i) for i in range(9, 27)]
        self.assertEqual(out, "\n".join(exp) + "\n")

    def test_expand_one_to_many(self):
        res, out = self._run(f"replace {self.a[9]}\nX1\nX2\nX3")
        self.assertIn("Edited 1 region", res)
        exp = [self.L(i) for i in range(1, 10)] + ["X1", "X2", "X3"] + [self.L(i) for i in range(11, 27)]
        self.assertEqual(out, "\n".join(exp) + "\n")

    def test_shrink_range_delete(self):
        res, out = self._run(f"replace {self.a[19]} {self.a[21]}")
        self.assertIn("Edited 1 region", res)
        exp = [self.L(i) for i in range(1, 20)] + [self.L(i) for i in range(23, 27)]
        self.assertEqual(out, "\n".join(exp) + "\n")

    def test_delete_single(self):
        res, out = self._run(f"delete {self.a[12]}")
        self.assertIn("Edited 1 region", res)
        exp = [self.L(i) for i in range(1, 13)] + [self.L(i) for i in range(14, 27)]
        self.assertEqual(out, "\n".join(exp) + "\n")

    def test_delete_last(self):
        res, out = self._run(f"delete {self.a[25]}")
        self.assertIn("Edited 1 region", res)
        self.assertEqual(out, "\n".join([self.L(i) for i in range(1, 26)]) + "\n")

    def test_insert_before(self):
        res, out = self._run(f"insert_before {self.a[1]}\nINS")
        self.assertIn("Edited 1 region", res)
        exp = [self.L(1), "INS"] + [self.L(i) for i in range(2, 27)]
        self.assertEqual(out, "\n".join(exp) + "\n")

    def test_insert_after(self):
        res, out = self._run(f"insert_after {self.a[1]}\nINS")
        self.assertIn("Edited 1 region", res)
        exp = [self.L(1), self.L(2), "INS"] + [self.L(i) for i in range(3, 27)]
        self.assertEqual(out, "\n".join(exp) + "\n")

    def test_insert_after_multiline(self):
        res, out = self._run(f"insert_after {self.a[4]}\nM1\nM2\nM3")
        self.assertIn("Edited 1 region", res)
        exp = [self.L(i) for i in range(1, 6)] + ["M1", "M2", "M3"] + [self.L(i) for i in range(6, 27)]
        self.assertEqual(out, "\n".join(exp) + "\n")

    def test_append(self):
        res, out = self._run(f"append {self.a[25]}\nAPP")
        self.assertIn("Edited 1 region", res)
        exp = [self.L(i) for i in range(1, 27)] + ["APP"]
        self.assertEqual(out, "\n".join(exp) + "\n")

    def test_prepend(self):
        res, out = self._run(f"prepend {self.a[0]}\nPRE")
        self.assertIn("Edited 1 region", res)
        exp = ["PRE"] + [self.L(i) for i in range(1, 27)]
        self.assertEqual(out, "\n".join(exp) + "\n")

    # ---- combinations (verified via independent _simulate) ----
    def test_two_non_overlapping_replaces(self):
        dsl = f"replace {self.a[1]} R2\nreplace {self.a[19]} R20"
        edits = [
            {"op": "replace", "start": 2, "content": ["R2"]},
            {"op": "replace", "start": 20, "content": ["R20"]},
        ]
        res, out = self._run(dsl)
        self.assertIn("Edited 2 region", res)
        self.assertEqual(out, self._simulate(edits))

    def test_three_non_overlapping_replaces(self):
        dsl = f"replace {self.a[3]} R4\nreplace {self.a[9]} R10\nreplace {self.a[16]} R17"
        edits = [
            {"op": "replace", "start": 4, "content": ["R4"]},
            {"op": "replace", "start": 10, "content": ["R10"]},
            {"op": "replace", "start": 17, "content": ["R17"]},
        ]
        res, out = self._run(dsl)
        self.assertIn("Edited 3 region", res)
        self.assertEqual(out, self._simulate(edits))

    def test_replace_plus_delete(self):
        dsl = f"replace {self.a[1]} R2\ndelete {self.a[14]}"
        edits = [
            {"op": "replace", "start": 2, "content": ["R2"]},
            {"op": "delete", "start": 15},
        ]
        res, out = self._run(dsl)
        self.assertIn("Edited 2 region", res)
        self.assertEqual(out, self._simulate(edits))

    def test_range_plus_single_replace(self):
        dsl = f"replace {self.a[5]} {self.a[8]}\nA1\nA2\nA3\nA4\nreplace {self.a[19]} R20"
        edits = [
            {"op": "replace", "start": 6, "end": 9, "content": ["A1", "A2", "A3", "A4"]},
            {"op": "replace", "start": 20, "content": ["R20"]},
        ]
        res, out = self._run(dsl)
        self.assertIn("Edited 2 region", res)
        self.assertEqual(out, self._simulate(edits))

    def test_insert_before_and_after_distinct(self):
        dsl = f"insert_before {self.a[4]}\nIB\ninsert_after {self.a[7]}\nIA"
        edits = [
            {"op": "insert_before", "start": 5, "content": ["IB"]},
            {"op": "insert_after", "start": 8, "content": ["IA"]},
        ]
        res, out = self._run(dsl)
        self.assertIn("Edited 2 region", res)
        self.assertEqual(out, self._simulate(edits))

    def test_multiple_inserts(self):
        dsl = f"insert_after {self.a[2]}\nI3\ninsert_before {self.a[9]}\nI10\nappend {self.a[25]}\nAPP"
        edits = [
            {"op": "insert_after", "start": 3, "content": ["I3"]},
            {"op": "insert_before", "start": 10, "content": ["I10"]},
            {"op": "append", "start": 26, "content": ["APP"]},
        ]
        res, out = self._run(dsl)
        self.assertIn("Edited 3 region", res)
        self.assertEqual(out, self._simulate(edits))

    def test_adjacent_replaces(self):
        dsl = f"replace {self.a[6]} R7\nreplace {self.a[7]} R8"
        edits = [
            {"op": "replace", "start": 7, "content": ["R7"]},
            {"op": "replace", "start": 8, "content": ["R8"]},
        ]
        res, out = self._run(dsl)
        self.assertIn("Edited 2 region", res)
        self.assertEqual(out, self._simulate(edits))

    def test_big_combo(self):
        dsl = (
            f"replace {self.a[2]} {self.a[4]}\nB1\nB2\nB3\n"
            f"delete {self.a[11]}\n"
            f"insert_before {self.a[19]}\nIB\n"
            f"append {self.a[25]}\nAPP"
        )
        edits = [
            {"op": "replace", "start": 3, "end": 5, "content": ["B1", "B2", "B3"]},
            {"op": "delete", "start": 12},
            {"op": "insert_before", "start": 20, "content": ["IB"]},
            {"op": "append", "start": 26, "content": ["APP"]},
        ]
        res, out = self._run(dsl)
        self.assertIn("Edited 4 region", res)
        self.assertEqual(out, self._simulate(edits))

    def test_replace_first_and_last(self):
        dsl = f"replace {self.a[0]} F\nreplace {self.a[25]} L"
        edits = [
            {"op": "replace", "start": 1, "content": ["F"]},
            {"op": "replace", "start": 26, "content": ["L"]},
        ]
        res, out = self._run(dsl)
        self.assertIn("Edited 2 region", res)
        self.assertEqual(out, self._simulate(edits))

    def test_range_expand_and_shrink(self):
        dsl = (
            f"replace {self.a[9]} {self.a[11]}\nE1\nE2\nE3\nE4\nE5\n"
            f"replace {self.a[17]} {self.a[19]}"
        )
        edits = [
            {"op": "replace", "start": 10, "end": 12, "content": ["E1", "E2", "E3", "E4", "E5"]},
            {"op": "replace", "start": 18, "end": 20, "content": []},
        ]
        res, out = self._run(dsl)
        self.assertIn("Edited 2 region", res)
        self.assertEqual(out, self._simulate(edits))

    def test_mixed_insert_replace_delete(self):
        dsl = (
            f"insert_after {self.a[1]}\nI2\n"
            f"replace {self.a[14]} R15\n"
            f"delete {self.a[22]}"
        )
        edits = [
            {"op": "insert_after", "start": 2, "content": ["I2"]},
            {"op": "replace", "start": 15, "content": ["R15"]},
            {"op": "delete", "start": 23},
        ]
        res, out = self._run(dsl)
        self.assertIn("Edited 3 region", res)
        self.assertEqual(out, self._simulate(edits))

    def test_all_ops_combo(self):
        dsl = (
            f"replace {self.a[0]} NEW1\n"
            f"replace {self.a[5]} {self.a[7]}\nRR1\nRR2\nRR3\n"
            f"insert_before {self.a[12]}\nIB\n"
            f"append {self.a[25]}\nAPP\n"
            f"delete {self.a[20]}"
        )
        edits = [
            {"op": "replace", "start": 1, "content": ["NEW1"]},
            {"op": "replace", "start": 6, "end": 8, "content": ["RR1", "RR2", "RR3"]},
            {"op": "insert_before", "start": 13, "content": ["IB"]},
            {"op": "append", "start": 26, "content": ["APP"]},
            {"op": "delete", "start": 21},
        ]
        res, out = self._run(dsl)
        self.assertIn("Edited 5 region", res)
        self.assertEqual(out, self._simulate(edits))


if __name__ == "__main__":
    unittest.main()
