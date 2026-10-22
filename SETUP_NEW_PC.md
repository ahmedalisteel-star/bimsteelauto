# Moving BIMSteelAuto to another PC

**Short answer: copying the folder is most of it, but not all of it.**

Five Python packages live *outside* the folder, in your system Python. They
have to be installed on the new machine, or the engine will not run.

FreeCAD needs **none** of them — the adapter reads `design_result.json` and
imports only pure-Python parts of the engine. That is the whole point of
the JSON boundary, and it is why the FreeCAD side needs nothing but
FreeCAD.

---

## What to copy

Copy the **whole** folder, including the workbench beside it:

```
bimsteelauto\
├─ peb\                 the package, the tests, the vendored cfs library
└─ BIMSteelAutoWB\      the FreeCAD workbench
```

That is everything the project itself consists of. The `jobs\` folder
inside `peb\` carries your past jobs; bring it if you want them.

**Optional but useful:** `<your-macro-folder>\` —
`peb_building_param_v8.py` lives there and is needed for the shop-detailing
work, which is the next milestone.

---

## Steps on the new PC

### 1. Install Python 3.12

From python.org. Tick **"Add python.exe to PATH"** during install.

### 2. Install FreeCAD 1.1

From freecad.org.

### 3. Install the engine's dependencies

```powershell
cd "<wherever you put it>\bimsteelauto\peb"; python -m pip install -r requirements.txt
```

`pyaisc360` installs from your own GitHub repo, pinned to the exact commit
the 858 tests were verified against — so you need **git** installed and an
internet connection for that one line.

### 4. Link the workbench into FreeCAD

Run PowerShell **as Administrator**, once:

```powershell
cmd /c mklink /J "$env:APPDATA\FreeCAD\Mod\BIMSteelAutoWB" "<wherever you put it>\bimsteelauto\BIMSteelAutoWB"
```

A junction rather than a copy, so edits to the package are live.

### 5. Prove it works

```powershell
python -m pytest -q
```

**Expect 858 passed, 1 skipped, 3 xfailed.** If you get that, everything is
correct — the suite exercises the engine, the FreeCAD adapter and the
workbench structure.

Then restart FreeCAD and check **BIMSteelAuto** appears in the workbench
selector.

---

## What you do NOT have to edit

Nothing. The package finds itself:

- The **workbench** works out where the package is from its own location —
  it sits beside `peb\`, and Windows junctions resolve back to the real
  folder.
- The **macro** tries the saved preference, then the installed workbench,
  then asks you once and remembers.

So a different drive letter or a different folder name costs you nothing.
If it ever does get lost, use **BIMSteelAuto → Settings** in FreeCAD and
point at the `peb` folder.

The paths written in `USER_GUIDE.md` and `HANDOVER_PROMPT.md` are prose
examples — update them if you like, but nothing depends on them.

---

## Then tell Claude

Attach or connect the folder and paste:

> I have moved BIMSteelAuto to a new PC. The folder is at `<path>`.
> Read `peb\SETUP_NEW_PC.md`, check the setup is complete, run `pytest`
> and tell me the number. It should be 858 passed, 1 skipped, 3 xfailed.

If the number is wrong, Claude has the whole test suite to tell it *what*
is wrong — a missing dependency fails loudly and by name, and the layer
tests will say if something did not copy.

---

## Checklist

| | |
|---|---|
| ☐ | Copied `bimsteelauto\` whole, with `BIMSteelAutoWB\` inside it |
| ☐ | Python 3.12 installed, on PATH |
| ☐ | FreeCAD 1.1 installed |
| ☐ | git installed (for the `pyaisc360` line) |
| ☐ | `python -m pip install -r requirements.txt` |
| ☐ | Workbench junction created (as Administrator) |
| ☐ | `python -m pytest -q` → **858 passed, 1 skipped, 3 xfailed** |
| ☐ | FreeCAD restarted, **BIMSteelAuto** in the workbench selector |
