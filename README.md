# PathRenamer

PathRenamer renames your TV shows, movies and anime so Plex, Jellyfin, Emby and Kodi recognize them, and moves their subtitles along. Renaming is free.

**[Download the latest version](https://github.com/Royalkiller2222/PathRenamer/releases/latest)** for Windows: run `PathRenamer-Setup-x.x.x.exe`. On a server, NAS, Linux or Mac: **[use Docker](DOCKER.md)**. Guides and FAQ: **[royalkiller2222.github.io/PathRenamer](https://royalkiller2222.github.io/PathRenamer/)**


Preview of the app <img width="1053" height="719" alt="app" src="https://github.com/user-attachments/assets/d2b339cd-df6c-441a-9205-b548fbb862cf" />



Shows similar results! Max 10.
<img widthh="898" height="681" alt="filematching" src="https://github.com/user-attachments/assets/577cc13e-a7fe-43ee-9bfa-44ac95eda3b0" />

Feel free to leave suggestions/comments but please keep in mind this is a passion project. It's going to have bugs, I'll try my best to fix what I can in future releases!

Support me at: https://ko-fi.com/pathrenamer

## How to use it

1. The first time, pick your library folders (TV Shows, Movies, Anime) in Settings.
2. Choose or drop your downloads folder, and pick TV Shows, Movies or Anime.
3. Click **Preview** to see the new names, then **Rename Files**.

Something wrong? Right-click a row to fix its name, episode or match. **Undo** puts files back, and **Edit → Rename History** can undo older renames too.

## Highlights

- Clean names for Plex, Jellyfin, Emby and Kodi (and apps that play from them, like Plezy), including multi-episode files, daily shows, specials and movie editions
- Subtitles are found and renamed with their video, with the right language
- Download missing subtitles from OpenSubtitles.com
- Resolution and codecs read from the video when the name doesn't say
- Move, copy or hard-link files (hard links keep torrents seeding)
- Ask Plex, Jellyfin, Emby or Kodi to scan, so new files show up in seconds
- Runs in the tray and can start with Windows; a Docker version runs on servers and NAS boxes
- Updates itself: when a new version is out, one click installs it
- Presets, a Modern or Classic layout, and Dark, Emerald, Midnight, Light or your own Custom theme

## Updates

PathRenamer checks for a new version once a day. When there is one, **Update to …** appears at the bottom right of the window: click it, and the new version is downloaded, checked and installed, and PathRenamer starts again. You can also use **Help → Check for Updates**. Turn the daily check off under Settings → About.

## Docker, servers and NAS boxes

The same renaming, without a window: `docker compose up -d` with [docker-compose.yml](docker-compose.yml) watches your downloads folder, and `pathrenamer rename /data/downloads` renames by hand. Every free feature is included. See **[DOCKER.md](DOCKER.md)**.

## Free and Pro

Everything you do by hand is free, forever. **PathRenamer Pro** adds the parts that run on their own:

- A watch folder that renames new downloads into your library automatically
- Missing subtitles downloaded after every rename
- Plex, Jellyfin, Emby or Kodi scanning right after renaming

Turning on a Pro feature starts a free 14-day trial. Pro costs $5 a year or $19 once ([pricing](https://royalkiller2222.github.io/PathRenamer/pricing/)), with a 14-day refund.

## License

PathRenamer is free software under the [GNU General Public License v3.0](LICENSE): you may use, study, share and change it, and anything you share that is built from it must stay under the same license. PathRenamer Pro is a paid license key for the automatic features; buying it supports development.
