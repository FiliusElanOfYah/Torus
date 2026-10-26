# TORUS bootstrap.sh iteration 12: update-after-bootstrap, piped install w/o tty, non-git src, edit backups, help text
import glob
import os
import subprocess

import pytest

APP = "/app"
BOOT = "/app/torus/bootstrap.sh"
CLI = "/app/torus/bin/torus"


def branch():
    return subprocess.check_output(["git", "-C", APP, "rev-parse", "--abbrev-ref", "HEAD"], text=True).strip()


def run(cmd, env=None, stdin=None, nott=False):
    e = dict(os.environ)
    e.update(env or {})
    if nott:
        cmd = ["setsid", "-w"] + cmd  # new session => no controlling tty, /dev/tty cannot be opened
    return subprocess.run(cmd, env=e, input=stdin, capture_output=True, text=True, timeout=120)


def layout(t, repo="file:///app", no_install=True):
    os.makedirs(f"{t}/prefix/bin", exist_ok=True)
    os.makedirs(f"{t}/home", exist_ok=True)
    env = {"HOME": f"{t}/home", "PREFIX": f"{t}/prefix", "TORUS_REPO": repo, "TORUS_BRANCH": branch()}
    if no_install:
        env["TORUS_NO_INSTALL"] = "1"
    return env


# Regression 1: bootstrap leaves a clean checkout and `git pull --ff-only` works
class TestUpdateAfterBootstrap:
    def test_clean_and_pull(self, tmp_path):
        t = str(tmp_path)
        env = layout(t)
        r = run(["bash", BOOT], env=env)
        assert r.returncode == 0, r.stderr
        src = f"{t}/home/.torus/src"
        st = subprocess.run(["git", "-C", src, "status", "--porcelain"], capture_output=True, text=True)
        assert st.stdout.strip() == "", st.stdout
        p = subprocess.run(["git", "-C", src, "pull", "--ff-only"], capture_output=True, text=True)
        assert p.returncode == 0, p.stderr
        assert "would be overwritten" not in p.stderr
        for f in glob.glob(f"{src}/torus/phases/*.sh"):
            assert os.access(f, os.X_OK), f
        assert subprocess.check_output(["git", "-C", src, "config", "core.fileMode"], text=True).strip() == "false"

    def test_rerun_picks_up_upstream_commit(self, tmp_path):
        t = str(tmp_path)
        subprocess.check_call(["git", "clone", "-q", APP, f"{t}/origin"])
        env = layout(t, repo=f"file://{t}/origin")
        assert run(["bash", BOOT], env=env).returncode == 0
        with open(f"{t}/origin/torus/phases/3-ingest.sh", "a") as f:
            f.write("# upstream change\n")
        subprocess.check_call(["git", "-C", f"{t}/origin", "-c", "user.email=a@b", "-c", "user.name=t", "commit", "-qam", "up"])
        head = subprocess.check_output(["git", "-C", f"{t}/origin", "rev-parse", "--short", "HEAD"], text=True).strip()
        r = run(["bash", BOOT], env=env)
        assert r.returncode == 0, r.stderr
        assert head in r.stdout
        assert "edited files" not in r.stdout


# Regression 2: piped `curl | bash` with a stdin-reading `torus` and no controlling tty
class TestPipedInstall:
    def test_piped_no_tty(self, tmp_path):
        t = str(tmp_path)
        env = layout(t, no_install=False)
        os.makedirs(f"{t}/stub")
        # stand-in torus (on PATH ahead of $PREFIX/bin, because bootstrap re-links $PREFIX/bin/torus)
        with open(f"{t}/stub/torus", "w") as f:
            f.write('#!/bin/bash\nread x; echo "STUB $* got:$x"; true\n')
        os.chmod(f"{t}/stub/torus", 0o755)
        env["PATH"] = f"{t}/stub:" + os.environ["PATH"]
        r = run(["bash"], env=env, stdin=open(BOOT).read(), nott=True)
        print(r.stdout, r.stderr)
        assert r.returncode == 0, r.stderr
        assert "STUB install all got:" in r.stdout
        assert "STUB status got:" in r.stdout
        # stdin of children was /dev/null, not the script
        assert "got:#" not in r.stdout and "got:set" not in r.stdout and "got:main" not in r.stdout
        tail = r.stdout.rstrip().splitlines()[-12:]
        assert any("torus key set anthropic" in l for l in tail)
        assert any("torus open" in l for l in tail)


# New: non-git src moved aside, edited files backed up before reset --hard
class TestSrcSafety:
    def test_non_git_src_moved_aside(self, tmp_path):
        t = str(tmp_path)
        env = layout(t)
        os.makedirs(f"{t}/home/.torus/src")
        open(f"{t}/home/.torus/src/mine.txt", "w").write("keep me")
        r = run(["bash", BOOT], env=env)
        assert r.returncode == 0, r.stderr
        assert "not a TORUS checkout" in r.stdout
        kept = glob.glob(f"{t}/home/.torus/backups/src-not-git-*/mine.txt")
        assert len(kept) == 1 and open(kept[0]).read() == "keep me"
        assert os.path.isdir(f"{t}/home/.torus/src/.git")

    def test_edits_backed_up_then_reset(self, tmp_path):
        t = str(tmp_path)
        env = layout(t)
        assert run(["bash", BOOT], env=env).returncode == 0
        src = f"{t}/home/.torus/src"
        target = f"{src}/torus/INSTALL.md"
        orig = open(target).read()
        with open(target, "a") as f:
            f.write("\nMY LOCAL NOTE\n")
        r = run(["bash", BOOT], env=env)
        print(r.stdout, r.stderr)
        assert r.returncode == 0, r.stderr
        assert "you edited files" in r.stdout
        assert "torus/INSTALL.md" in r.stdout
        kept = glob.glob(f"{t}/home/.torus/backups/src-edits-*/torus/INSTALL.md")
        assert len(kept) == 1 and "MY LOCAL NOTE" in open(kept[0]).read()
        assert open(target).read() == orig
        st = subprocess.run(["git", "-C", src, "status", "--porcelain"], capture_output=True, text=True)
        assert st.stdout.strip() == ""

    def test_deleted_tracked_file_restored(self, tmp_path):
        t = str(tmp_path)
        env = layout(t)
        assert run(["bash", BOOT], env=env).returncode == 0
        src = f"{t}/home/.torus/src"
        os.remove(f"{src}/torus/INSTALL.md")
        r = run(["bash", BOOT], env=env)
        assert r.returncode == 0, r.stderr
        assert os.path.isfile(f"{src}/torus/INSTALL.md")


# CLI help text + status exit code
class TestCli:
    def test_help_haiku_and_no_dup(self):
        out = subprocess.check_output(["bash", CLI, "help"], text=True)
        ask = [l for l in out.splitlines() if "torus ask" in l and "--model" in l]
        assert ask and "haiku" in ask[0]
        assert sum(1 for l in out.splitlines() if l.strip().startswith("torus llm model")) == 1

    def test_status_ok_without_service_dirs(self, tmp_path):
        t = str(tmp_path)
        env = layout(t)
        r = run(["bash", CLI, "status"], env=env)
        assert r.returncode == 0, r.stderr

    def test_phases_wording(self):
        assert "Phases 0-9" in open(BOOT).read()
        readme = open("/app/README.md").read()
        assert "Phases 0-9" in readme or "Phases 0\u20139" in readme
        assert "Phases 1-9" not in readme and "Phases 1\u20139" not in readme
