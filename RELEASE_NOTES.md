PathRenamer 1.7.0 takes care of extras and finds episodes by their title.

- **Featurettes and other extras**: files in Featurettes, Behind The Scenes, Deleted Scenes, Interviews, Trailers or Extras folders are renamed into the movie's (or show's) folder, where Plex, Jellyfin and Emby show them as extras, e.g. `Dune (2021)/Featurettes/The Spice Must Flow.mkv`. Turn it off under Settings → Options.
- **Episodes by title**: files without an episode number, like "The Office - The Dundies.mkv", are matched by their episode title. Turn on Settings → Options → "Match TV episodes by their title" to also fix files whose numbers are wrong.

Install with **PathRenamer-Setup-1.7.0.exe**, or click **Update to 1.7.0** in PathRenamer 1.6.0. It keeps your settings. Docker: `docker compose pull && docker compose up -d`.
