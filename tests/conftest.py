"""Test session setup: ensure jsii can find a Node.js runtime.

CDK's jsii kernel spawns ``node``. On machines without a system Node.js install,
point jsii (via JSII_NODE) at the node binary bundled by nodejs-wheel-binaries.
"""

import os
import shutil
from pathlib import Path


def _ensure_node_runtime() -> None:
    if shutil.which("node") or os.environ.get("JSII_NODE"):
        return
    import nodejs_wheel

    root = Path(nodejs_wheel.__file__).parent
    for candidate in (root / "node.exe", root / "bin" / "node"):
        if candidate.exists():
            os.environ["JSII_NODE"] = str(candidate)
            return


_ensure_node_runtime()
