# Development notes

For working on PathRenamer itself. Users don't need any of this — see the [README](README.md).

## Source code

The Python source is in [`src/`](src/):

| File | Purpose |
| --- | --- |
| `src/main.py` | The app: window layouts, metadata lookups, renaming, subtitles |
| `src/parser.py` | Reads title, year, season and episode from release names and folders |
| `src/scanner.py` | Subtitle language/flag detection and Plex subtitle names |
| `src/themes.py` | Colour themes (palettes) and the stylesheets built from them |
| `src/history.py` | Rename history kept on disk, for undo |
| `src/newicon.ico` | App and window icon |
| `src/build_exe.bat` | Builds `dist\PathRenamer.exe` with PyInstaller |

The window has two layouts, chosen in Settings → Appearance → Layout (or
View → Layout): **Modern** (default; file list and preview side by side, details
panel, resizable panes that stack on narrow windows) and **Classic** (the
original 1.2.0 layout). Both use the same theme colours.

Run from source:

```
cd src
pip install -r requirements.txt
python main.py
```

### How this source was recovered

The working copy of the v1.2.0 source was lost, so it was rebuilt from
`PathRenamer-Setup-1.2.0.exe`: the program was unpacked from the installer and
the PyInstaller bundle, and the Python files were rebuilt from the bytecode
inside it. Each recovered file compiles to exactly the bytecode that shipped,
checked code object by code object, including line and column positions.
Code, strings and docstrings are therefore original. Comments are not stored
in bytecode, so the comments in these files were written afresh.

### Running the tests

```
pip install -r src/requirements.txt pytest
python -m pytest
```

The tests feed real-world release names (scene, P2P, anime fansub, foreign and
daily-show names) through the same code the app uses, and check renaming,
undo, subtitle pickup and naming, and duplicate detection on real files in a
temp folder. They also build the real window offscreen in both layouts (switching
layouts, the details panel, Settings, drag & drop). They pass with guessit 3.8
(the version in the 1.2.0 build) and guessit 4.x.

### Making a release

Releases are built by GitHub Actions (`.github/workflows/release.yml`) on Windows
with Python 3.13 and the versions in `src/requirements-lock.txt`. Everything is
done on the GitHub website:

1. Make sure `APP_VERSION` in `src/main.py` is the new version and
   `RELEASE_NOTES.md` describes it (the release page shows that text).
2. Open the **Actions** tab, click **Release** on the left, then
   **Run workflow** → **Run workflow** (branch `main`).
3. After about five minutes the run turns green and **Releases** has
   "Version X.Y.Z" with `PathRenamer-Setup-X.Y.Z.exe` and `PathRenamer.exe`.

The workflow runs the tests, builds `PathRenamer.exe` with PyInstaller and
`PathRenamer-Setup-<version>.exe` with Inno Setup (`installer/PathRenamer.iss`),
and publishes release tag `V<version>`. If that version was already released
it stops and says so; a tag left behind by a deleted release is moved to the
new build. Publishing a release on the website with a new tag, or pushing a
tag (`git tag V1.3.0 && git push origin V1.3.0`), also works: the installer is
attached a few minutes later.
