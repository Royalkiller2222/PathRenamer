# Changelog

## 1.3.0

Built with Python 3.13 (1.2.0 used 3.12) and current libraries: PyQt6 6.11,
guessit 4.4 (was 3.8), rapidfuzz 3.14, requests 2.34.

Renaming fixes, found by running about 1,900 real-world release names (from the
test data of Sonarr, Radarr, guessit and other release-name parsers) through the
app offline. With the files in a download folder named "Incoming", 71% of them
were renamed wrongly before; 6.5% are now. Most of the rest are batch packs,
reversed or garbled names, or names the reference data numbers differently
(absolute "512" where the app writes S05E12).

**Titles**
- A loose file is no longer named after the folder it sits in
  (`Incoming/The.Office.US.S02E03.mkv` became "Incoming", now "The Office (US)").
  The folder still decides when the file name has no show name, only an episode
  title, or an obfuscated name like `abc123.mkv`.
- Hyphenated titles are kept whole: "Spider-Man", "Obi-Wan Kenobi", "She-Hulk".
- Numeric titles work: "1923", "1883", "1917", "9", "24".
- Title words guessit mistakes for tags are kept: "The Last of Us",
  "It Ends With Us", "The Collector", "Johnny English", "Dune Part Two", "Kill Bill Vol 1".
- Website prefixes are dropped ("www.Site.org - Show.S01E01...").
- Foreign season folders ("Saison 6", "Temporada 2", "Staffel 1") lead to the
  show folder above them; library folders like "Babylon 5" and "The 4400" are
  read as titles, not episode numbers.

**Seasons and episodes**
- Multi-episode files are named the Plex way: `S01E01-E03` (was `S01E01`).
- Daily shows use the air date: `Season 2024/Show 2024-05-17 - Title`
  (every episode used to become `S01E01` and collide).
- Season 0 and episode 0 are kept (they used to become 1).
- A season-pack folder name no longer overrides the episode number in the file name.
- Anime forms: `[Group] Title - 01`, `Title S2 - 03`, `2nd Season 24`, `(S4-24)`,
  `Episode 161`, `SP01` (Season 00), absolute numbers.
- Foreign forms: `Staffel 2 Folge 5`, `Saison 2 Épisode 5`, `Säsong 2 Avsnitt 5`,
  `Temporada 2 [Cap.201]`, `2×7`, `T02E22`, `第二季 第3集`, `Сезон 3, Эпизод 21`.
- Years are no longer taken for seasons ("Show (2011) - 141" was Season 2011).

**Movies**
- No year: `Title/Title.mkv` instead of `Title (Unknown)/...`.
- The year from the folder is used when the file name has none
  (`Movie (1998)/Movie.720p.mkv`).

**Safety**
- Files with no recognisable title or episode number are shown as errors and
  skipped, instead of being renamed to "Unknown" or `S01E01`.
- Two files that would get the same name (e.g. 720p and 1080p copies) are flagged;
  only the first is renamed.
- Samples and extras folders (`Sample`, `Featurettes`, `Extras`, ...) are skipped
  when scanning a folder.

**Subtitles**
- `Show - 1.srt` is no longer attached to `Show - 10.mkv`.
- Subtitles in a release's `Subs` folder are found even when they are not named
  after the video (`Subs/2_English.srt`), and per-episode folders in season packs
  (`Subs/Show.S01E01/2_English.srt`, `Subs/S01E01/`) are picked up.
- Also found: subtitles named only by language next to a single video
  (`English.srt`), `SUBS`/`SubTitles` folders in any letter case, season-pack
  subtitles with shorter names than the episodes (`Subs/Lost.S01E01.eng.srt`),
  and `.sup` (PGS) and `.smi` files.
- Languages are read correctly from 639-2/T codes (`fra`, `deu`), region codes
  (`pt-BR`, `es-419`, `zh-Hans`, `fr-CA`) and names like
  `Spanish (Latin America)` or `Portuguese (Brazil)`; these all used to be
  labelled English. Words in a title are no longer read as tags
  ("The IT Crowd" was Italian, "Hi Score Girl" was hearing-impaired).
- All languages use Plex's three-letter codes (`.eng`, `.spa`, ...).
- A second subtitle with the same language is numbered before the language
  (`Movie.2.eng.srt`) so Plex still sees its language (was `Movie.eng-1.srt`).
- A subtitle that matches two videos goes to the one it is named after
  (`Movie.Extended.en.srt` → the Extended cut).
- Subtitles are moved only if their video is; one that was also added to the
  list on its own is no longer processed twice.
- The status bar says how many subtitles were moved, and which were left in
  place because their new name already exists. Hovering the Subtitles cell
  shows each subtitle's new name.

**Layout**
- New Modern layout (the default): toolbar with the source folder, media type
  buttons and matching mode; file list and preview side by side in resizable
  panes that stack on narrow windows; a details panel showing where the
  selected file and each of its subtitles will go; a summary bar
  ("3 ready · 1 conflict · 4 subtitles"); a drop zone when the list is empty.
  Long names are shortened instead of wrapped, scrolling is smooth, and panels
  slide in and out.
- The original layout is kept as Classic. Settings → Appearance → Layout (or
  View → Layout) switches between them without restarting; Modern has options
  for file list position, row spacing, the details panel and animations.
  Colours are the same in both.
- Settings opens with a sidebar in the Modern layout.
- In Modern, matching mode (automatic or forced TVDB ID) sits in the toolbar
  instead of being asked on every preview.
- The window remembers its size and position.
- Keyboard: F5 preview, Ctrl+O browse, Ctrl+Enter rename, Ctrl+Z undo,
  Delete removes the selected rows or files.
- Dropping a mix of files adds only videos and subtitles (not .nfo or .jpg).

**Metadata lookups**
- Multi-episode files get every episode title from TVMaze ("Part 1 & Part 2"),
  daily shows are looked up by air date, and the show year from the file or
  folder name is used when TVMaze has none.
