"""The frontend is one HTML file with one inline script. A duplicate `const`
in it once stopped the browser parsing *any* of the page's JavaScript, so every
tab and button went dead while /health, every API and the deploy stayed green.
Python tests cannot see that. This one does: it hands the inline script to Node
and fails if it does not parse."""
import re
import shutil
import subprocess
from pathlib import Path

import pytest

INDEX = Path(__file__).resolve().parents[1] / "web" / "static" / "index.html"
# Inline blocks only — <script src=...> is a CDN library, not ours to check.
INLINE = re.compile(r"<script(?![^>]*\bsrc=)[^>]*>(.*?)</script>", re.S)


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_inline_script_parses(tmp_path):
    blocks = INLINE.findall(INDEX.read_text(encoding="utf-8"))
    assert blocks, "expected at least one inline <script> in index.html"
    for i, code in enumerate(blocks):
        js = tmp_path / f"block{i}.js"
        js.write_text(code, encoding="utf-8")
        result = subprocess.run(["node", "--check", str(js)],
                                capture_output=True, text=True)
        assert result.returncode == 0, f"inline script {i} is not valid JS:\n{result.stderr}"
