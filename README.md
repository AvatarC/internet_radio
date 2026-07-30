# radio.py

A standalone internet radio player for the terminal. No VLC, no ffmpeg
binary, no external media player — audio is decoded and played back
in-process using Python audio libraries.

## Features

- Plays MP3/OGG/FLAC/WAV Icecast/Shoutcast streams out of the box, and AAC
  streams (`.aac`/`.m4a`) if you install the optional `av` package.
- Live volume control with `-` and `=` while playing.
- Station list loaded from a simple CSV file (`stations.txt`), with an
  arrow-key + Enter picker, plus single-key number shortcuts.
- Displays the stream's ICY title (song/show info) as it changes.
- Ignores SSL certificate errors, since many radio streams use mismatched
  or self-signed certs.

## Requirements

- Python 3.8+
- [`miniaudio`](https://pypi.org/project/miniaudio/) — required, handles
  MP3/OGG/FLAC/WAV streams and playback:

  ```
  pip install miniaudio
  ```

- [`av`](https://pypi.org/project/av/) (PyAV) — optional, only needed if
  you want to play AAC/`.m4a` streams. It bundles its own FFmpeg decoders
  in the wheel, so it doesn't require a separate ffmpeg install:

  ```
  pip install av
  ```

## Usage

Run with no arguments to pick a station from `stations.txt` (created for
you with a few defaults on first run):

```
python radio.py
```

Play a specific station by name:

```
python radio.py --station "Sunshine 106.8"
```

Play a raw stream URL directly, bypassing the station list:

```
python radio.py https://ice1.somafm.com/groovesalad-128-mp3
```

List all configured stations:

```
python radio.py --list
```

Set a starting volume (0-100, default 70):

```
python radio.py --station wuvt --volume 50
```

### Station picker controls

- `↑` / `↓` — move the selection
- `Enter` — play the highlighted station
- `1`-`9` / `0` — jump straight to station 1-9 (or 10th with `0`) and play it
- `q` / `Esc` — cancel

### Playback controls

- `=` / `+` — volume up (also unmutes)
- `-` — volume down (also unmutes)
- `m` — toggle mute; unmuting restores the exact volume you had before
- `↑` / `↓` — switch to the previous/next station in `stations.txt`
- `1`-`9` / `0` — jump straight to station 1-9 (or 10th with `0`)
- `l` — reopen the full station picker (arrow keys + Enter, or `q`/`Esc` to
  cancel and keep listening to the current station)
- `q` / `Esc` / `Ctrl+C` — stop and quit

Switching stations reconnects to the new stream without restarting the
script, and keeps your current volume level.

## stations.txt

A plain CSV file, one station per line: `name,url`. Lines starting with
`#` and blank lines are ignored.

```
Radio Monte Carlo,https://edge.radiomontecarlo.net/RMC.mp3
Sunshine 106.8,https://playerservices.streamtheworld.com/api/livestream-redirect/SUNSHINE_106_8.mp3
```

If the file doesn't exist, it's created automatically with a small set of
default stations the first time you run the script. Edit it directly to
add, remove, or rename stations. Use `--stations-file <path>` to point at
a different file.

Note: entries must be a direct audio stream URL, not a playlist file
(`.m3u`/`.pls`) — the player doesn't parse those. If a station only
publishes a playlist link, open it in a text editor/browser to find the
actual stream URL inside and use that instead.

## Notes

- AAC detection is based on the URL's file extension (`.aac`, `.aacp`,
  `.m4a`). If a station serves AAC without one of those extensions, the
  `miniaudio` path will fail to decode it — in that case try renaming/
  aliasing the URL, or ask for extension-less AAC detection to be added.
- SSL certificate verification is disabled for the `miniaudio` playback
  path, since several public radio streams use certificates that fail
  strict validation. This does *not* cover the `av`/AAC path — PyAV uses
  FFmpeg's own network stack rather than Python's `ssl` module, so an AAC
  stream with a bad certificate may still fail to connect.

## License

MIT — see [LICENSE](LICENSE).
