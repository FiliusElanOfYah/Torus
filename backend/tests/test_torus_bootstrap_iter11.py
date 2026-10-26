# TORUS bootstrap.sh one-paste installer + README/INSTALL docs consistency (iteration 11)
import os
import re
import stat
import subprocess
import tempfile

import pytest

APP = "/app"
BOOT = "/app/torus/bootstrap.sh"
README = "/app/README.md"
INSTALL = "/app/torus/INSTALL.md"
CLI = "/app/torus/bin/torus"
RAW_URL = "https://raw.githubusercontent.com/FiliusElanOfYah/Torus/main/torus/bootstrap.sh"


def run(cmd, env=None, stdin=None):
    e = dict(os.environ)
    if env:
        e.update(env)
    return subprocess.run(cmd, env=e, input=stdin, capture_output=True, text=True, timeout=120)


def branch():
    return subprocess.check_output(["git", "-C", APP, "rev-parse", "--abbrev-ref", "HEAD"], text=True).strip()


# ---- static checks ----
class TestStatic:
    def test_bash_syntax(self):
        r = run(["bash", "-n", BOOT])
        assert r.returncode == 0, r.stderr

    def test_refuses_without_prefix(self, tmp_path):
        r = run(["bash", BOOT], env={"PREFIX": "/nonexistent", "HOME": str(tmp_path / "h1")})
        assert r.returncode == 1
        assert "This installer is for Termux" in r.stderr
        assert "PREFIX=/nonexistent not found" in r.stderr
        assert not (tmp_path / "h1" / ".torus").exists()

    def test_script_calls_install_and_status(self):
        s = open(BOOT).read()
        assert re.search(rr"^\s*torus install all", s, re.M)
        assert re.search(r"^torus status \|\| true$", s, re.M)

    def test_claude_block(self):
        s = open(BOOT).read()
        block = s.split("cat <<'EOF'", 1)[1]
        for needle in ["torus key set anthropic", "torus llm verify", "torus llm on", "torus seal && torus audit"]:
            assert needle in block, needle


# ---- simulated Termux: download-only path ----
@pytest.fixture(scope="class")
def sim():
    d = tempfile.mkdtemp(prefix="torus-boot-")
    os.makedirs(f"{d}/prefix/bin")
    os.makedirs(f"{d}/home")
    env = {"HOME": f"{d}/home", "PREFIX": f"{d}/prefix", "TORUS_NO_INSTALL": "1",
           "TORUS_REPO": "file:///app", "TORUS_BRANCH": branch()}
    yield d, env
    subprocess.run(["rm", "-rf", d])


class TestSimulatedTermux:
    def test_first_run_downloads_and_links(self, sim):
        d, env = sim
        r = run(["bash", BOOT], env=env)
        print(r.stdout, r.stderr)
        assert r.returncode == 0, r.stderr
        assert "downloading TORUS" in r.stdout
        assert "torus command linked" in r.stdout
        assert "skipping install" in r.stdout
        target = f"{d}/home/.torus/src/torus/bin/torus"
        assert os.path.isfile(target)
        assert os.stat(target).st_mode & stat.S_IXUSR
        link = f"{d}/prefix/bin/torus"
        assert os.path.islink(link)
        assert os.readlink(link) == target
        for p in os.listdir(f"{d}/home/.torus/src/torus/phases"):
            if p.endswith(".sh"):
                assert os.access(f"{d}/home/.torus/src/torus/phases/{p}", os.X_OK), p

    def test_second_run_updates_idempotent(self, sim):
        d, env = sim
        r = run(["bash", BOOT], env=env)
        print(r.stdout, r.stderr)
        assert r.returncode == 0, r.stderr
        assert "updating" in r.stdout
        assert "downloading TORUS" not in r.stdout
        assert "torus command linked" in r.stdout
        assert os.readlink(f"{d}/prefix/bin/torus") == f"{d}/home/.torus/src/torus/bin/torus"

    def test_piped_like_curl(self, sim):
        # curl ... | bash : script on stdin
        d, env = sim
        r = run(["bash"], env=env, stdin=open(BOOT).read())
        assert r.returncode == 0, r.stderr
        assert "skipping install" in r.stdout

    def test_linked_cli_help_runs(self, sim):
        d, env = sim
        r = run(["bash", f"{d}/prefix/bin/torus", "help"], env={"HOME": env["HOME"], "PREFIX": env["PREFIX"]})
        assert r.returncode == 0, r.stderr
        assert "torus install all" in r.stdout


# ---- docs ----
class TestDocs:
    def test_readme(self):
        s = open(README).read()
        assert f"curl -fsSL {RAW_URL} | bash" in s
        assert "Termux:Boot" in s and "F-Droid" in s
        assert "(torus/INSTALL.md)" in s

    def test_install_md_steps(self):
        s = open(INSTALL).read()
        nums = [int(n) for n in re.findall(r"^## Step (\d+)", s, re.M)]
        assert nums == list(range(1, 12)), nums
        for n in ["torus install all", "torus key set anthropic", "torus llm verify", RAW_URL]:
            assert n in s, n


# ---- CLI consistency ----
class TestCliConsistency:
    def test_usage_lists_readme_commands(self):
        r = run(["bash", CLI, "help"])
        assert r.returncode == 0, r.stderr
        for c in ["ask", "status", "doctor", "logs", "oracle", "code", "export", "update", "llm", "seal", "audit", "rollback"]:
            assert re.search(rf"torus {c}\b", r.stdout), c

    def test_phase_scripts_exist(self):
        files = os.listdir("/app/torus/phases")
        for n in range(10):
            assert any(f.startswith(f"{n}-") and f.endswith(".sh") for f in files), n


# ---- regressions found in iteration 11 (expected to FAIL until fixed by main agent) ----
class TestRegressions:
    def test_torus_update_pull_after_bootstrap(self, tmp_path):
        # bootstrap chmod +x on phases (tracked 100644) leaves mode diffs -> `torus update` (git pull --ff-only) aborts
        t = str(tmp_path)
        subprocess.check_call(["git", "clone", "-q", APP, f"{t}/origin"])
        os.makedirs(f"{t}/prefix/bin"); os.makedirs(f"{t}/home")
        env = {"HOME": f"{t}/home", "PREFIX": f"{t}/prefix", "TORUS_NO_INSTALL": "1",
               "TORUS_REPO": f"file://{t}/origin", "TORUS_BRANCH": branch()}
        assert run(["bash", BOOT], env=env).returncode == 0
        with open(f"{t}/origin/torus/phases/3-ingest.sh", "a") as f:
            f.write("# upstream change\n")
        subprocess.check_call(["git", "-C", f"{t}/origin", "-c", "user.email=a@b", "-c", "user.name=t", "commit", "-qam", "up"])
        r = run(["git", "-C", f"{t}/home/.torus/src", "pull", "--ff-only"])
        assert r.returncode == 0, r.stderr

    def test_piped_install_does_not_eat_rest_of_script(self, tmp_path):
        # under `curl | bash`, a child that reads stdin (apt/dpkg prompt) swallows the remaining script lines
        t = str(tmp_path)
        os.makedirs(f"{t}/prefix/bin"); os.makedirs(f"{t}/home"); os.makedirs(f"{t}/stub")
        with open(f"{t}/stub/torus", "w") as f:
            f.write('#!/bin/bash\necho "STUB $*"; [ "$1" = install ] && cat >/dev/null; true\n')
        os.chmod(f"{t}/stub/torus", 0o755)
        env = {"HOME": f"{t}/home", "PREFIX": f"{t}/prefix", "TORUS_REPO": "file:///app",
               "TORUS_BRANCH": branch(), "PATH": f"{t}/stub:" + os.environ["PATH"]}
        r = run(["bash"], env=env, stdin=open(BOOT).read())
        assert "STUB status" in r.stdout and "torus key set anthropic" in r.stdout, r.stdout
