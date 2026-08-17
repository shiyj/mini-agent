"""Tests for the simplified whole-file-rewrite agent.

Covers block parsing / whole-file rewrite logic, error handling, and a full
react_loop run with a stubbed model (no network). The heavy hashline `edit:`
DSL is gone — editing is whole-file `file:` rewrite only.
"""

import os
import shutil

import pytest

from mini_agent import get_local_model, run_deep_agent
from mini_agent.code_executor import execute_code
from mini_agent.model import ResponsesModel
from mini_agent.react_agent import parse_step, write_blocks


# ---------------------------------------------------------------------------
# Block parsing / writing
# ---------------------------------------------------------------------------

def test_parse_step_extracts_file_and_python_blocks():
    text = (
        "Some preamble.\n"
        "```file:data/out.py\n"
        "def main():\n"
        "    print('hi')\n"
        "```\n"
        "```python\n"
        "run_python_file('data/out.py')\n"
        "final_answer('done')\n"
        "```\n"
    )
    blocks = parse_step(text)
    langs = [b[0] for b in blocks]
    assert langs == ["file", "python"]
    assert blocks[0][1] == "data/out.py"
    assert "def main():" in blocks[0][2]
    assert blocks[1][2].startswith("run_python_file")


def test_parse_step_converts_tool_name_fence_to_python():
    text = "```read_file('x.py')```"
    blocks = parse_step(text)
    assert blocks[0][0] == "python"
    assert "read_file" in blocks[0][2]


def test_write_blocks_rewrites_whole_file(tmp_path):
    target = tmp_path / "sub" / "out.py"
    blocks = parse_step(f"```file:{os.fspath(target)}\ncontent=1\n```")
    file_reports, path_errors = write_blocks(blocks, step=1)
    assert not path_errors
    assert f"Written {os.path.abspath(os.fspath(target))}" in file_reports
    assert target.read_text(encoding="utf-8") == "content=1"


def test_write_blocks_rejects_edit_block(tmp_path):
    blocks = [("edit", "x.py", "replace 1#abcd print(1)")]
    file_reports, path_errors = write_blocks(blocks, step=1)
    assert file_reports == []
    assert path_errors and "not supported" in path_errors[0]


def test_write_blocks_rejects_no_path_file():
    blocks = [("file", None, "content")]
    file_reports, path_errors = write_blocks(blocks, step=1)
    assert file_reports == []
    assert path_errors and "without path" in path_errors[0]


def test_write_blocks_rejects_placeholder_path(tmp_path):
    """A <path> placeholder copied from the prompt must NOT write a file."""
    blocks = [("file", "<path>", "x=1")]
    file_reports, path_errors = write_blocks(blocks, step=1, fallback_path=None)
    assert file_reports == []
    assert path_errors and "invalid path" in path_errors[0]
    assert not (tmp_path / "<path>").exists()


def test_write_blocks_recovers_placeholder_via_fallback(tmp_path):
    target = tmp_path / "real.py"
    blocks = [("file", "<path>", "x=1")]
    file_reports, path_errors = write_blocks(
        blocks, step=1, fallback_path=str(target)
    )
    assert not path_errors
    assert target.read_text(encoding="utf-8") == "x=1"


def test_write_blocks_strips_backticks_around_path(tmp_path):
    target = tmp_path / "out.py"
    blocks = parse_step(f"```file:`{target}`\nx=1\n```")
    file_reports, path_errors = write_blocks(blocks, step=1)
    assert not path_errors
    assert target.read_text(encoding="utf-8") == "x=1"


def test_write_blocks_reports_write_failure(tmp_path):
    """Writing to an existing directory path should surface an error."""
    directory = tmp_path / "adirectory"
    directory.mkdir()
    blocks = [("file", str(directory), "x=1")]
    file_reports, path_errors = write_blocks(blocks, step=1)
    assert file_reports == []
    assert any("Failed to write" in e for e in path_errors)


# ---------------------------------------------------------------------------
# execute_code error reporting
# ---------------------------------------------------------------------------

def test_execute_code_syntax_error_has_type():
    r = execute_code("def f(\n", [])
    assert r.error is not None
    assert r.error_type == "SyntaxError"


def test_execute_code_indentation_error_has_type():
    r = execute_code("def f():\nprint(1)\n", [])
    assert r.error is not None
    assert r.error_type == "IndentationError"


# ---------------------------------------------------------------------------
# Full react_loop with a stubbed model
# ---------------------------------------------------------------------------

class _StubModel:
    """Minimal model stand-in that returns responses in order, then repeats the last."""

    def __init__(self, responses):
        self._responses = list(responses)
        self.model_name = "stub"
        self.context_window = 8192
        self.max_output_tokens = 512

    def generate(self, messages, stop_sequences=None, **kwargs):
        text = self._responses[-1] if self._responses else ""
        if len(self._responses) > 1:
            self._responses.pop(0)
        return type("R", (), {"content": text, "token_usage": None})


def test_get_local_model_returns_responses_model():
    m = get_local_model("ornith:9b", api_base="http://localhost:1")
    assert isinstance(m, ResponsesModel)
    assert m.model_name == "ornith:9b"


def test_deep_agent_single_step_file_rewrite(tmp_path):
    """Model rewrites a file with ```file: and final_answers — should succeed."""
    target = tmp_path / "greet.py"
    target.write_text("print('broken')", encoding="utf-8")

    outputs = [
        f"```file:{os.fspath(target)}\n"
        f"def greet(name):\n"
        f"    print(f'hello, {{name}}')\n"
        f"if __name__ == '__main__':\n"
        f"    greet('world')\n"
        f"```\n"
        f"```python\n"
        f"run_python_file('{os.fspath(target)}')\n"
        f"final_answer('done')\n"
        f"```\n",
    ]
    stub = _StubModel(outputs)
    result = run_deep_agent("Rewrite greet.py", model=stub, max_steps=4, verbose=False)
    assert result == "done"
    new = target.read_text(encoding="utf-8")
    assert "def greet(name):" in new
    assert "broken" not in new


def test_deep_agent_read_then_write_then_final(tmp_path):
    """Two steps: read_file first, then rewrite, is also accepted."""
    target = tmp_path / "calc.py"
    target.write_text("print(1 + 1)", encoding="utf-8")

    outputs = [
        f"```python\nread_file('{os.fspath(target)}')\n```\n",
        f"```file:{os.fspath(target)}\nprint(2 + 2)\n```\n"
        f"```python\nfinal_answer('ok')\n```\n",
    ]
    stub = _StubModel(outputs)
    result = run_deep_agent("fix calc.py", model=stub, max_steps=5, verbose=False)
    assert result == "ok"
    assert target.read_text(encoding="utf-8") == "print(2 + 2)"


def test_deep_agent_rejects_final_answer_without_work(tmp_path):
    """A bare final_answer() as the first action is refused (E002); the run
    only succeeds once the model actually writes a file."""
    target = tmp_path / "calc.py"
    target.write_text("print(1 + 1)", encoding="utf-8")

    outputs = [
        # Step 1: premature final_answer, no prior work -> rejected.
        f"```python\nfinal_answer('done')\n```\n",
        # Step 2: actually rewrite the file and final_answer -> accepted.
        f"```file:{os.fspath(target)}\nprint(2 + 2)\n```\n"
        f"```python\nfinal_answer('ok')\n```\n",
    ]
    stub = _StubModel(outputs)
    result = run_deep_agent("fix calc.py", model=stub, max_steps=5, verbose=False)
    assert result == "ok"
    assert target.read_text(encoding="utf-8") == "print(2 + 2)"


@pytest.mark.skipif(shutil.which("python3") is None, reason="python3 not on PATH")
def test_deep_agent_rejects_final_answer_after_failed_run(tmp_path):
    """Rule B (E003): final_answer right after a script that ran but FAILED is
    rejected; the model must fix and re-verify before final_answer is accepted."""
    target = tmp_path / "app.py"

    outputs = [
        # Step 1: write a broken script, run it (fails -> 【FAILED】), still final_answer.
        f"```file:{os.fspath(target)}\nraise RuntimeError('boom')\n```\n"
        f"```python\nrun_python_file('{os.fspath(target)}')\nfinal_answer('done')\n```\n",
        # Step 2: fix it and final_answer -> accepted.
        f"```file:{os.fspath(target)}\nprint('ok')\n```\n"
        f"```python\nrun_python_file('{os.fspath(target)}')\nfinal_answer('fixed')\n```\n",
    ]
    stub = _StubModel(outputs)
    result = run_deep_agent("fix app.py", model=stub, max_steps=5, verbose=False)
    assert result == "fixed"
    assert target.read_text(encoding="utf-8") == "print('ok')"