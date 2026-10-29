# PathRenamer with Docker

PathRenamer also runs without a window: on a home server, a NAS (Synology,
Unraid, QNAP, TrueNAS), a Raspberry Pi, or any Linux or Mac computer with
Docker. It names files exactly like the Windows app and has every free
feature:

| | Free | Pro |
|---|---|---|
| Rename files and folders (`rename`), with undo and history | Yes | Yes |
| Download subtitles by hand (`subtitles`) | Yes | Yes |
| Ask Plex, Jellyfin, Emby or Kodi to scan (`scan`) | Yes | Yes |
| Rename new downloads on their own (`watch`) | Reports them | Yes |
| Download missing subtitles after every rename | | Yes |
| The media server scans right after every rename | | Yes |

Pro comes with a free 14-day trial. It starts the first time `watch` runs.

## Start it with Docker Compose

1. Install Docker ([Docker Desktop](https://docs.docker.com/get-started/get-docker/) on a
   Mac, or the Docker package of your NAS).
2. Make a folder called `pathrenamer` and save
   [docker-compose.yml](docker-compose.yml) in it.
3. Open `docker-compose.yml` in a text editor and change the folders on the
   left side of each `:` to yours (see [Folders](#folders) below).
4. In that folder, run:

   ```sh
   docker compose up -d
   ```

5. See what it does:

   ```sh
   docker compose logs -f pathrenamer
   ```

New downloads in the watch folder are picked up as soon as they finish
(when the file hasn't changed for 15 seconds).

### Folders

PathRenamer sees your files by the paths inside the container, such as
`/data/downloads`. The compose file maps one folder of yours to `/data`:

```yaml
volumes:
  - ./pathrenamer-config:/config     # settings, rename history, license
  - /srv/data:/data                  # your downloads and your media
```

and tells PathRenamer where things are inside it:

```yaml
PATHRENAMER_WATCH: /data/downloads
PATHRENAMER_MOVIES: /data/media/movies
PATHRENAMER_TV: /data/media/tv
```

**Hard links** (`PATHRENAMER_ACTION: hardlink`) put a file in your library
without moving the download, so your torrent client keeps seeding, and
without using extra space. They only work when the downloads and the
library are inside **one** mapped folder, as above. With two separate
volumes (`/downloads` and `/movies`) the files are copied instead.

**Your user.** PathRenamer runs as the user and group in `PUID` and `PGID`
(1000 by default), so renamed files belong to you rather than to `root`.
Find yours with `id -u` and `id -g`. That user needs permission to change
the downloads and media folders.

## Rename by hand (free)

Show the new names first:

```sh
docker compose exec pathrenamer pathrenamer rename /data/downloads --dry-run
```

then rename them:

```sh
docker compose exec pathrenamer pathrenamer rename /data/downloads --yes
```

Matches PathRenamer isn't sure about are marked `[??]` and left alone; add
`--include-unsure` after checking them. Undo the last rename with
`pathrenamer undo`.

Without Docker Compose, the same with `docker run`:

```sh
docker run --rm -it -v ~/pathrenamer-config:/config -v /srv/data:/data \
  -e PATHRENAMER_MOVIES=/data/media/movies \
  ghcr.io/royalkiller2222/pathrenamer rename /data/downloads
```

## Commands

Inside the container, run them as `pathrenamer COMMAND` (for example
`docker compose exec pathrenamer pathrenamer history`).

| Command | What it does |
|---|---|
| `rename PATH...` | Match the files, show the new names, rename them. Options: `--type tv\|movie\|anime`, `--to FOLDER`, `--action move\|copy\|hardlink`, `--dry-run`, `--yes`, `--include-unsure` |
| `watch [FOLDER]` | Watch a folder (what the container does when it starts). Pro renames sure matches; otherwise they are reported in the log. `--no-rename` only reports |
| `undo [ID]` | Put the last rename back, or the one with that ID |
| `history` | List earlier renames and their IDs |
| `subtitles PATH...` | Download missing subtitles (`--languages "English, Spanish"`) |
| `scan` | Ask the media server to scan its movie and TV libraries |
| `plex-signin` | Connect to your Plex server by signing in to Plex |
| `config show` / `config set KEY VALUE` | Show or change settings |
| `license status` / `activate KEY` / `deactivate` / `check` | PathRenamer Pro |
| `update` | Is there a newer version? |

## Settings

These environment variables win over the settings file:

| Variable | What it sets |
|---|---|
| `PATHRENAMER_WATCH` | The folder to watch |
| `PATHRENAMER_MOVIES`, `PATHRENAMER_TV`, `PATHRENAMER_ANIME` | The library folders |
| `PATHRENAMER_TYPE` | What arrives in the watch folder: `auto` (default, by the name), `tv`, `movie` or `anime` |
| `PATHRENAMER_ACTION` | `move` (default), `copy` or `hardlink` |
| `PATHRENAMER_LICENSE_KEY` | Your Pro key, activated on the first start |
| `PUID`, `PGID` | The user and group PathRenamer runs as |
| `UMASK` | Permissions for new files, for example `002` |
| `TZ` | Your time zone for the log, for example `Europe/Amsterdam` |

Everything else is in `/config/config.json`, the same settings the Windows
app has. List them with `pathrenamer config show` and change one with
`pathrenamer config set`, for example:

```sh
pathrenamer config set format_movie "{title} ({year})/{title} ({year}) {res}"
pathrenamer config set tmdb_api_key YOUR_TMDB_KEY
pathrenamer config set subs_languages "English, Dutch"
```

## Media servers

PathRenamer asks your server to scan after renaming (Pro), or when you run
`pathrenamer scan` (free). Pick one:

**Plex**

```sh
pathrenamer plex-signin
```

opens a sign-in link; allow PathRenamer and your server is saved.

**Jellyfin** (make an API key in Dashboard → API Keys)

```sh
pathrenamer config set media_server jellyfin
pathrenamer config set jellyfin_url http://192.168.1.20:8096
pathrenamer config set jellyfin_key YOUR_API_KEY
```

**Emby**: the same with `emby`, `emby_url` and `emby_key`.

**Kodi** (turn on Settings → Services → Control → Allow remote control via HTTP)

```sh
pathrenamer config set media_server kodi
pathrenamer config set kodi_url http://192.168.1.30:8080
pathrenamer config set kodi_user kodi
pathrenamer config set kodi_password YOUR_PASSWORD
```

Apps that play from these servers, like Plezy, see the new files as soon as
the server does.

When the server sees your files under other paths than PathRenamer does
(usual with Docker), PathRenamer asks for a scan of the whole movie or TV
library instead of only the new folder. Turn the scan after renaming on
with `pathrenamer config set plex_auto_scan true` (for every server type).

## PathRenamer Pro

```sh
pathrenamer license activate YOUR-KEY      # or set PATHRENAMER_LICENSE_KEY
pathrenamer license status
pathrenamer license deactivate             # to move the license to another computer
```

Pro is checked with the license server once a week and keeps working
offline for 30 days. Turn on subtitles after renaming with
`pathrenamer config set subs_auto true`.

## Updating

```sh
docker compose pull
docker compose up -d
```

Your settings and rename history are kept in the config folder. The log
says when a new version is out (checked once a day; turn that off with
`pathrenamer config set check_updates false`).

## When something doesn't work

- **"Permission denied"**: set `PUID` and `PGID` to the owner of your
  folders, or give that user write access to them.
- **Files are copied instead of hard-linked**: the downloads and the
  library are in different volumes. Map one parent folder (see
  [Folders](#folders)).
- **New downloads aren't picked up**: the file has to stop changing for 15
  seconds first, and files that were already there when `watch` started are
  left alone (start it with `pathrenamer watch --existing` to include them).
  `docker compose logs pathrenamer` shows what happened.
- More detail: `/config/pathrenamer.log`.
