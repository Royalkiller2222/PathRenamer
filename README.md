Plex Renamer is a free renaming tool made to be highly customizable with a modern UI. 


Preview of the app <img width="1053" height="719" alt="app" src="https://github.com/user-attachments/assets/d2b339cd-df6c-441a-9205-b548fbb862cf" />



Shows similar results! Max 10.
<img widthh="898" height="681" alt="filematching" src="https://github.com/user-attachments/assets/577cc13e-a7fe-43ee-9bfa-44ac95eda3b0" />

Feel free to leave suggestions/comments but please keep in mind this is a passion project. It's going to have bugs, I'll try my best to fix what I can in future releases!

Support me at: https://ko-fi.com/pathrenamer

## Source code

The Python source for v1.2.0 is in [`src/`](src/):

| File | Purpose |
| --- | --- |
| `src/main.py` | The app: UI, filename parsing, metadata lookups, renaming |
| `src/parser.py` | Show-folder name cleanup helpers |
| `src/scanner.py` | Subtitle language detection and naming helpers |
| `src/newicon.ico` | App and window icon |
| `src/build_exe.bat` | Builds `dist\PathRenamer.exe` with PyInstaller |

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
undo, subtitle pickup and duplicate detection on real files in a temp folder.
They pass with guessit 3.8 (the version in the 1.2.0 build) and guessit 4.x.

### Making a release

Releases are built by GitHub Actions (`.github/workflows/release.yml`) on Windows
with Python 3.13 and the versions in `src/requirements-lock.txt`:

1. Set `APP_VERSION` in `src/main.py` and update `RELEASE_NOTES.md`.
2. Commit, then push a matching tag: `git tag v1.3.0` and `git push origin v1.3.0`.

The workflow runs the tests, builds `PathRenamer.exe` with PyInstaller and
`PathRenamer-Setup-<version>.exe` with Inno Setup (`installer/PathRenamer.iss`),
and publishes both on the Releases page.
