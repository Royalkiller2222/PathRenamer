PathRenamer 1.3.0 is mostly about renaming files correctly. About 1,900 real release names were run through the app: 71% used to be renamed wrongly, now 6.5% are.

**Better renaming**
- Files are no longer named after the folder they happen to sit in (`Incoming/The.Office.US.S02E03.mkv` was renamed to "Incoming").
- Titles like "Spider-Man", "1923", "The Last of Us" and "Dune Part Two" come out right.
- Multi-episode files are named `S01E01-E03`, and daily shows by air date (`Season 2024/Show 2024-05-17`).
- Specials (Season 00) and episode 0 are kept.
- Anime and foreign names are understood: `[Group] Title - 01`, `Title S2 - 03`, `Staffel 2 Folge 5`, `Temporada 2 [Cap.201]` and more.
- Movies with no year are no longer named "Title (Unknown)".

**Safer renaming**
- Files with no recognisable title or episode number are flagged instead of renamed to "Unknown" or S01E01.
- Two files that would get the same name are flagged.
- Sample files and extras folders are skipped.

**Subtitles**
- `Subs/2_English.srt`-style subtitles are found and renamed with their video.
- `Show - 1.srt` no longer gets attached to `Show - 10.mkv`.

**Under the hood**
- Built with Python 3.13 and current versions of PyQt6, guessit, rapidfuzz and requests.

Install with **PathRenamer-Setup-1.3.0.exe**. It upgrades an existing installation and keeps your settings. **PathRenamer.exe** is the same app without an installer.
