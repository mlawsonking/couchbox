import importlib.machinery
import importlib.util
import os
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)
sys.path.insert(0, os.path.join(REPO, "tests", "fixtures"))


def load_script(rel_path: str, name: str):
    """Import an extension-less executable Python script (e.g. couch/doctor/couch-doctor) as a module."""
    path = os.path.join(REPO, rel_path)
    loader = importlib.machinery.SourceFileLoader(name, path)
    spec = importlib.util.spec_from_loader(name, loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod
