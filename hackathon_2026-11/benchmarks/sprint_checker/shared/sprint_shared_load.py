"""Card S1 (card 395): load a sibling module of this folder by its file, under the unique name
"sprint_shared_<name>". The folder's file names (score, split, records, ask, combine) are also used
by other benchmark folders; a bare `import score` in one test process could get another card's
score.py (the full offline suite did, 14 Nov). Every module here loads its siblings this way."""
import importlib.util
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent


def sibling(name):
    key = "sprint_shared_" + name
    if key not in sys.modules:
        spec = importlib.util.spec_from_file_location(key, HERE / f"{name}.py")
        mod = importlib.util.module_from_spec(spec)
        sys.modules[key] = mod
        try:
            spec.loader.exec_module(mod)
        except BaseException:
            del sys.modules[key]
            raise
    return sys.modules[key]
