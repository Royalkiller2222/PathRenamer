PathRenamer 1.7.0 makes automation free, takes care of extras and knows every episode order.

- **The watch folder and media server scans are now free**: new downloads are renamed into your library on their own, and Plex, Jellyfin, Emby or Kodi scans them right away, without Pro. Pro is only for what costs money to run: subtitles downloaded automatically after every rename.
- **Episode orders**: aired, DVD, streaming and absolute (Settings → Formats). DVD and streaming orders come from TVmaze for shows whose releases number episodes differently.
- **Five built-in presets** (Presets menu): Aired order, DVD order, Streaming order, Absolute order and **Strip episode numbers**, which ignores the numbers in the file names, finds each episode by its title and names it in the order you pick.
- **Featurettes and other extras**: files in Featurettes, Behind The Scenes, Deleted Scenes, Interviews, Trailers or Extras folders go into the movie's (or show's) folder, where Plex, Jellyfin and Emby show them as extras.
- **Episodes by title**: files without an episode number, like "The Office - The Dundies.mkv", are matched by their episode title.

Install with **PathRenamer-Setup-1.7.0.exe**, or click **Update to 1.7.0** in PathRenamer 1.6.0. It keeps your settings. Docker: `docker compose pull && docker compose up -d`.
