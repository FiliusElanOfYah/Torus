# TORUS

Local-first research and build assistant that runs **on your Android phone** (Termux), with
Claude as an opt-in, sealed top layer. Two parts live in this repo:

| Folder | What it is | Where it runs |
| --- | --- | --- |
| `torus/` | The real thing: the `torus` command, the daemon mesh, the phone web page, the phase installers | Termux on the phone |
| `backend/`, `frontend/` | The hosted setup guide (phase checklist, OPSEC audit, bundles) | Emergent preview / your domain |

## Install on the phone (Samsung Galaxy S26 Ultra or any Android 12+)

1. Install **Termux** and **Termux:Boot** from F-Droid. Open each once.
2. In Termux:
   ```
   termux-setup-storage
   pkg update -y && pkg install -y curl
   curl -fsSL https://raw.githubusercontent.com/FiliusElanOfYah/Torus/main/torus/bootstrap.sh | bash
   ```
   That one line downloads TORUS, installs Phases 0-9 and prints the status. Run it again any
   time to update or repair.
3. Connect Claude with your own key:
   ```
   torus key set anthropic
   torus llm verify
   torus llm on && torus llm test
   torus seal && torus audit
   ```
4. `torus open` for the phone page. Reboot once so Termux:Boot starts the mesh.

Step-by-step details, what every phase does and how to undo: **[`torus/INSTALL.md`](torus/INSTALL.md)**.

## Everyday commands

```
torus ask "question" [url ...]   torus status      torus doctor       torus logs <daemon>
torus oracle                     torus code        torus export       torus update
torus llm on|off|verify|auto     torus seal        torus audit        torus rollback
```
