"""Docs stay in sync with the code they describe."""
import os
import re
import subprocess
import sys

from conftest import REPO, load_script
from couch.launcher.state import REASONS


def read(rel):
    with open(os.path.join(REPO, rel), encoding="utf-8") as fh:
        return fh.read()


def test_failure_screens_doc_is_generated_from_state():
    gen = load_script("scripts/gen-failure-screens.py", "genfs")
    assert read("docs/FAILURE-SCREENS.md") == gen.render(), "run scripts/gen-failure-screens.py"
    for code in REASONS:
        assert f"## {code}" in read("docs/FAILURE-SCREENS.md")


def test_runbook_covers_required_topics():
    rb = read("docs/RUNBOOK.md").lower()
    for topic in ["make-usb.sh", "first boot", "pair", "add a game", "re-pair", "failure", "ssh", "rollback",
                  "couch-doctor", "couch-update --import", "/health", "couchadmin"]:
        assert topic in rb, topic


def test_testplan_numbers_every_acceptance_item():
    tp = read("docs/TESTPLAN.md")
    for i in range(1, 11):
        assert re.search(rf"^## {i}\. ", tp, re.M), f"acceptance item {i} missing"
    assert tp.count("[ ] PASS") >= 10


def test_plan_b_exists_and_says_not_built():
    pb = read("docs/PLAN-B-WINDOWS.md").lower()
    assert "not built" in pb and "moonlight" in pb


def test_install_options_documented():
    help_text = subprocess.run([os.path.join(REPO, "installer", "install.sh"), "--help"], capture_output=True,
                               text=True).stdout
    rb = read("docs/RUNBOOK.md")
    for opt in re.findall(r"--[a-z-]+", help_text):
        if opt in ("--from-firstboot", "--pins", "--skip-packages"):
            continue
        assert opt in rb, opt
