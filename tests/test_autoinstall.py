import json
import os
import subprocess

import pytest
import yaml
import jsonschema

from conftest import REPO

SCHEMA = json.load(open(os.path.join(REPO, "tests", "schema", "autoinstall-schema.json")))


def render(tmp_path, *args, psk=None):
    env = dict(os.environ, COUCH_ADMIN_HASH="$6$saltsalt$" + "x" * 86)
    if psk:
        env["COUCH_WIFI_PSK"] = psk
    out = tmp_path / "seed"
    subprocess.run([os.path.join(REPO, "installer", "make-usb.sh"), "--render-only", str(out), *args],
                   check=True, env=env, capture_output=True)
    return yaml.safe_load((out / "user-data").read_text()), (out / "user-data").read_text()


@pytest.mark.parametrize("args,psk", [
    (["--disk", "/dev/nvme0n1"], None),
    (["--disk", "only"], None),
    (["--disk", "/dev/sda", "--wifi-ssid", "Living Room", "--hostname", "couch2", "--timezone", "America/Chicago"],
     "correct horse battery"),
])
def test_rendered_user_data_validates_against_subiquity_schema(tmp_path, args, psk):
    doc, text = render(tmp_path, *args, psk=psk)
    assert text.startswith("#cloud-config")
    jsonschema.validate(doc["autoinstall"], SCHEMA)


def test_disk_only_uses_guard_and_largest(tmp_path):
    doc, _ = render(tmp_path, "--disk", "only")
    a = doc["autoinstall"]
    assert a["storage"]["layout"] == {"name": "direct", "match": {"size": "largest"}}
    assert a["early-commands"][0][-1] == "only"


def test_install_runs_our_installer_and_no_interactive(tmp_path):
    a = render(tmp_path, "--disk", "/dev/nvme0n1")[0]["autoinstall"]
    assert a["interactive-sections"] == []
    assert any("install.sh" in c for c in a["late-commands"])
    assert a["ssh"]["allow-pw"] is False
    assert a["identity"]["password"].startswith("$6$")
    assert a["updates"] == "security"


def test_template_holds_no_secrets():
    text = open(os.path.join(REPO, "installer", "autoinstall", "user-data")).read()
    assert "$6$" not in text and "password: \"@@ADMIN_HASH@@\"" in text
