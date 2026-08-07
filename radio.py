#!/usr/bin/env python3
"""Standalone internet radio player with keyboard volume control (- and = keys).

No external media player required - audio decoding and playback are done
in-process via the 'miniaudio' library (a small pip-installable package
with a bundled/precompiled decoder, no VLC/ffmpeg install needed).

Requires:
    pip install miniaudio

AAC streams (.aac / .m4a URLs) additionally require PyAV, which bundles its
own FFmpeg decoders in the wheel (still no separate ffmpeg/VLC install):
    pip install av

Usage:
    python radio.py <stream_url>
    python radio.py --station <name>
    python radio.py --list
"""

import argparse
import array
import csv
import os
import ssl
import sys

try:
    import miniaudio
except ImportError:
    sys.exit(
        "Missing dependency 'miniaudio'.\n"
        "Install it with: pip install miniaudio"
    )

# Default template written to stations.txt if it doesn't exist yet.
DEFAULT_STATIONS = [
    ("soma-groove", "https://ice1.somafm.com/groovesalad-128-mp3"),
    ("soma-dronezone", "https://ice1.somafm.com/dronezone-128-mp3"),
    ("soma-indie", "https://ice1.somafm.com/indiepop-128-mp3"),
    ("wuvt", "https://stream.wuvt.vt.edu/wuvt-lb.ogg"),
]

STATIONS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "stations.txt")

# Ignore SSL certificate verification (some radio streams use self-signed
# or otherwise mismatched certificates). Set as the process-wide default
# so it also covers miniaudio.IceCastClient's internal streaming connection,
# which doesn't accept an explicit ssl_context.
ssl_context = ssl.create_default_context()
ssl_context.check_hostname = False
ssl_context.verify_mode = ssl.CERT_NONE
ssl._create_default_https_context = ssl._create_unverified_context


def emit(text=""):
    """Print a line with an explicit CRLF instead of plain print()'s '\\n'.

    print() relies on the terminal's own output post-processing (OPOST/
    ONLCR on POSIX) to turn a bare '\\n' into a carriage-return-plus-newline.
    That's normally invisible, but this program repeatedly puts the tty into
    raw mode to read single keypresses (see get_key_reader), and on some
    terminals (macOS Terminal.app in particular) that leaves output looking
    like it only does line feeds without carriage returns, so each redrawn
    line staircases further right instead of starting at column 0. Writing
    '\\r\\n' ourselves sidesteps that entirely; it's a no-op on terminals that
    were already handling '\\n' correctly, Windows included.
    """
    sys.stdout.write(text + "\r\n")
    sys.stdout.flush()


def load_stations(path):
    """Load (name, url) pairs from a CSV file: name,url per line.

    Blank lines and lines starting with '#' are ignored. If the file
    doesn't exist, it is created with a default set of stations.
    """
    if not os.path.exists(path):
        with open(path, "w", newline="") as f:
            writer = csv.writer(f)
            for name, url in DEFAULT_STATIONS:
                writer.writerow([name, url])
        emit(f"Created default station list at: {path}")

    stations = []
    with open(path, newline="") as f:
        reader = csv.reader(f)
        for row in reader:
            if not row or row[0].strip().startswith("#"):
                continue
            if len(row) < 2:
                continue
            name, url = row[0].strip(), row[1].strip()
            if name and url:
                stations.append((name, url))
    return stations


VOLUME_STEP = 0.05
VOLUME_MIN = 0.0
VOLUME_MAX = 1.0
VOLUME_DEFAULT = 0.7


def enable_windows_ansi():
    """Turn on ANSI escape sequence support in the Windows console, if needed."""
    if os.name != "nt":
        return
    import ctypes

    kernel32 = ctypes.windll.kernel32
    STD_OUTPUT_HANDLE = -11
    ENABLE_VIRTUAL_TERMINAL_PROCESSING = 0x0004
    handle = kernel32.GetStdHandle(STD_OUTPUT_HANDLE)
    mode = ctypes.c_uint32()
    if kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
        kernel32.SetConsoleMode(handle, mode.value | ENABLE_VIRTUAL_TERMINAL_PROCESSING)


def get_key_reader():
    """Return a read_key() function for a single keypress.

    Returns a plain one-character string for normal keys (letters, digits,
    '-', '=', ...), or one of the sentinel strings "UP", "DOWN", "ENTER",
    "ESC" for the corresponding special keys.
    """
    if os.name == "nt":
        import msvcrt
        import time

        arrow_map = {b"H": "UP", b"P": "DOWN"}

        def read_key():
            # msvcrt.getch() blocks in a C call that Ctrl+C can't interrupt
            # promptly (KeyboardInterrupt only gets delivered once the call
            # returns, i.e. after the *next* keypress). Poll with kbhit()
            # instead so control returns to Python regularly, letting
            # KeyboardInterrupt raise as soon as Ctrl+C is pressed.
            while not msvcrt.kbhit():
                time.sleep(0.02)
            ch = msvcrt.getch()
            if ch in (b"\x00", b"\xe0"):
                ch2 = msvcrt.getch()
                return arrow_map.get(ch2, "")
            if ch in (b"\r", b"\n"):
                return "ENTER"
            if ch in (b"\x1b", b"\x03"):
                return "ESC"
            try:
                return ch.decode("utf-8", errors="ignore")
            except UnicodeDecodeError:
                return ""

        return read_key
    else:
        import select
        import termios
        import tty

        arrow_map = {"A": "UP", "B": "DOWN"}

        def read_key():
            fd = sys.stdin.fileno()
            old_settings = termios.tcgetattr(fd)
            try:
                tty.setraw(fd)
                ch = sys.stdin.read(1)
                if ch == "\x1b":
                    # A real arrow key sends ESC '[' letter in one burst;
                    # a lone Escape keypress won't have more bytes ready yet.
                    ready, _, _ = select.select([sys.stdin], [], [], 0.01)
                    if ready:
                        ch2 = sys.stdin.read(1)
                        if ch2 == "[":
                            ch3 = sys.stdin.read(1)
                            return arrow_map.get(ch3, "")
                    return "ESC"
                if ch in ("\r", "\n"):
                    return "ENTER"
                if ch == "\x03":
                    return "ESC"
                return ch
            finally:
                termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)

        return read_key


def choose_station(stations, read_key, initial_index=0):
    selected = initial_index
    count = len(stations)

    def render(first):
        if not first:
            sys.stdout.write(f"\033[{count}A")
        for i, (name, _url) in enumerate(stations):
            marker = "> " if i == selected else "  "
            sys.stdout.write(f"\033[2K{marker}{i + 1}. {name}\r\n")
        sys.stdout.flush()

    emit("Stations (up/down + Enter, or press a number, 'q' to cancel):")
    render(first=True)

    while True:
        key = read_key()
        if key == "UP":
            selected = (selected - 1) % count
            render(first=False)
        elif key == "DOWN":
            selected = (selected + 1) % count
            render(first=False)
        elif key == "ENTER":
            return stations[selected][1]
        elif key.isdigit():
            index = 9 if key == "0" else int(key) - 1
            if 0 <= index < count:
                return stations[index][1]
        elif key in ("q", "ESC"):
            return None


def clamp(value, low, high):
    return max(low, min(high, value))


def apply_volume(source_stream, volume_state):
    """Wrap a miniaudio PCM sample generator, scaling samples by a live volume level.

    volume_state is a dict with a "level" key (0.0-1.0), a "muted" key, and
    a "skip_muted" key (used to silence playback while skipping to the next
    track) that can all be updated externally while this generator is
    running. Manual scaling is used instead of PlaybackDevice.set_master_volume()
    because that method isn't present in all installed builds of miniaudio.
    """
    frames_wanted = yield b""
    while True:
        chunk = source_stream.send(frames_wanted)
        muted = volume_state.get("muted") or volume_state.get("skip_muted")
        level = 0.0 if muted else volume_state["level"]
        if level < 1.0:
            for i in range(len(chunk)):
                chunk[i] = int(chunk[i] * level)
        frames_wanted = yield chunk


# Formats miniaudio can't decode, but PyAV/FFmpeg can: plain AAC/ALAC
# containers, and HLS manifests (many stations, e.g. BBC, are HLS-only now).
PYAV_EXTENSIONS = (".aac", ".aacp", ".m4a", ".m3u8")


def needs_pyav(url):
    path = url.split("?", 1)[0].split("#", 1)[0]
    return path.lower().endswith(PYAV_EXTENSIONS)


def open_pyav_stream(url):
    """Open an AAC/HLS stream via PyAV and return (pcm_generator, closer).

    pcm_generator follows the same protocol as miniaudio.stream_any(): send()
    a frame count, get back that many stereo 16-bit frames as an array('h').
    closer() releases the underlying network/decoder resources.
    """
    try:
        import av
    except ImportError:
        sys.exit(
            "Missing dependency 'av' (needed for AAC/HLS streams).\n"
            "Install it with: pip install av"
        )

    container = av.open(url)
    audio_stream = container.streams.audio[0]
    resampler = av.audio.resampler.AudioResampler(format="s16", layout="stereo", rate=44100)
    decoder = container.decode(audio_stream)

    def generator():
        buffer = array.array("h")

        def refill(min_samples):
            while len(buffer) < min_samples:
                try:
                    frame = next(decoder)
                except StopIteration:
                    return
                for resampled in resampler.resample(frame):
                    pcm = resampled.to_ndarray()  # shape (channels, samples), int16
                    interleaved = pcm.transpose().reshape(-1).astype("int16")
                    buffer.extend(array.array("h", interleaved.tobytes()))

        frames_wanted = yield b""
        while True:
            needed = frames_wanted * 2  # stereo interleaved sample count
            refill(needed)
            chunk = buffer[:needed]
            del buffer[:needed]
            if len(chunk) < needed:
                chunk.extend(array.array("h", bytes(2 * (needed - len(chunk)))))
            frames_wanted = yield chunk

    gen = generator()
    next(gen)  # prime, matching miniaudio.stream_any()'s own generator convention
    return gen, container.close


def parse_args():
    parser = argparse.ArgumentParser(description="Play an internet radio stream.")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("url", nargs="?", help="Stream URL to play")
    group.add_argument("--station", "-s", help="Name of a station from stations.txt")
    parser.add_argument(
        "--list", action="store_true", help="List stations from stations.txt and exit"
    )
    parser.add_argument(
        "--stations-file",
        default=STATIONS_FILE,
        help=f"Path to the stations CSV file (default: {STATIONS_FILE})",
    )
    parser.add_argument(
        "--volume",
        "-v",
        type=int,
        default=int(VOLUME_DEFAULT * 100),
        help=f"Starting volume 0-100 (default {int(VOLUME_DEFAULT * 100)})",
    )
    return parser.parse_args()


def make_title_handler(title_state, skip_state, volume_state):
    """Build an update_stream_title callback that also resolves an active skip.

    title_state tracks the last known title so a skip started with 'n' can
    tell an actual song change (title differs) apart from the stream simply
    re-sending the same title, which some Icecast servers do periodically.
    """

    def on_title(client, new_title):
        emit(f"\r\nStream title: {new_title}")
        previous_title = title_state["last"]
        title_state["last"] = new_title
        if skip_state["active"] and new_title != previous_title:
            skip_state["active"] = False
            volume_state["skip_muted"] = False
            emit("Resuming playback: next track started.")

    return on_title


def main():
    try:
        run(parse_args())
    except KeyboardInterrupt:
        emit("\r\nInterrupted.")


def run(args):
    enable_windows_ansi()
    read_key = get_key_reader()
    stations = load_stations(args.stations_file)

    if args.list:
        emit(f"Stations in {args.stations_file}:")
        for name, url in stations:
            emit(f"  {name}: {url}")
        return

    if args.station:
        matches = [u for n, u in stations if n.lower() == args.station.lower()]
        if not matches:
            sys.exit(
                f"Unknown station '{args.station}'. Use --list to see options "
                f"from {args.stations_file}."
            )
        url = matches[0]
    elif args.url:
        url = args.url
    else:
        if not stations:
            sys.exit(
                f"No stations found in {args.stations_file}. "
                "Add some (name,url per line), or pass a URL directly."
            )
        url = choose_station(stations, read_key)
        if url is None:
            return

    volume_state = {"level": clamp(args.volume / 100.0, VOLUME_MIN, VOLUME_MAX), "muted": False}

    while url is not None:
        url = play_stream(url, stations, volume_state, read_key)


def play_stream(url, stations, volume_state, read_key):
    """Play url until the user quits or switches station.

    Returns the URL of the next station to play, or None to quit.
    """
    emit(f"Connecting to: {url}")
    title_state = {"last": None}
    skip_state = {"active": False}
    volume_state["skip_muted"] = False
    if needs_pyav(url):
        emit("Audio format: AAC/HLS (via PyAV)")
        stream, close_source = open_pyav_stream(url)
        skip_supported = False  # PyAV path has no stream-title metadata to skip against
    else:
        source = miniaudio.IceCastClient(
            url,
            update_stream_title=make_title_handler(title_state, skip_state, volume_state),
            ssl_context=ssl_context,
        )
        emit(f"Audio format: {source.audio_format.name}")
        if source.station_name:
            emit(f"Station: {source.station_name}")
        stream = miniaudio.stream_any(source, source.audio_format)
        close_source = source.close
        skip_supported = True

    device = miniaudio.PlaybackDevice()

    volume_controlled_stream = apply_volume(stream, volume_state)
    next(volume_controlled_stream)  # prime, matching miniaudio's own generator convention
    device.start(volume_controlled_stream)

    def show_volume():
        if skip_state["active"]:
            sys.stdout.write("\r\033[2KSkipping to next track (muted)...")
        elif volume_state.get("muted"):
            sys.stdout.write("\r\033[2KVolume: Muted")
        else:
            sys.stdout.write(f"\r\033[2KVolume: {round(volume_state['level'] * 100)}%")
        sys.stdout.flush()

    if stations:
        emit("Controls: '=' vol up, '-' vol down, 'm' mute, 'n' skip track, up/down/number to switch station, 'l' station list, 'q' quit")
    else:
        emit("Controls: '=' volume up, '-' volume down, 'm' mute, 'n' skip track, 'q' quit")
    show_volume()

    current_index = next((i for i, (_n, u) in enumerate(stations) if u == url), None)
    next_url = None

    try:
        while True:
            key = read_key()

            if key in ("=", "+"):
                volume_state["level"] = clamp(volume_state["level"] + VOLUME_STEP, VOLUME_MIN, VOLUME_MAX)
                volume_state["muted"] = False
                skip_state["active"] = False
                volume_state["skip_muted"] = False
                show_volume()
            elif key == "-":
                volume_state["level"] = clamp(volume_state["level"] - VOLUME_STEP, VOLUME_MIN, VOLUME_MAX)
                volume_state["muted"] = False
                skip_state["active"] = False
                volume_state["skip_muted"] = False
                show_volume()
            elif key == "m":
                volume_state["muted"] = not volume_state.get("muted")
                show_volume()
            elif key == "n":
                if not skip_supported:
                    emit("\r\nSkip isn't available for this stream (no title metadata).")
                    show_volume()
                elif skip_state["active"]:
                    skip_state["active"] = False
                    volume_state["skip_muted"] = False
                    emit("\r\nSkip cancelled.")
                    show_volume()
                elif title_state["last"] is None:
                    emit("\r\nNo stream title received yet, can't skip.")
                    show_volume()
                else:
                    skip_state["active"] = True
                    volume_state["skip_muted"] = True
                    emit(f"\r\nSkipping '{title_state['last']}' — muted until the next Stream title.")
                    show_volume()
            elif key in ("q", "ESC"):
                break
            elif stations and key == "UP":
                current_index = (current_index - 1) % len(stations) if current_index is not None else 0
                next_url = stations[current_index][1]
                break
            elif stations and key == "DOWN":
                current_index = (current_index + 1) % len(stations) if current_index is not None else 0
                next_url = stations[current_index][1]
                break
            elif stations and key.isdigit():
                index = 9 if key == "0" else int(key) - 1
                if 0 <= index < len(stations):
                    current_index = index
                    next_url = stations[index][1]
                    break
            elif stations and key == "l":
                emit()
                chosen = choose_station(stations, read_key, initial_index=current_index or 0)
                if chosen is not None:
                    next_url = chosen
                    current_index = next(
                        (i for i, (_n, u) in enumerate(stations) if u == chosen), None
                    )
                    break
                show_volume()
    except KeyboardInterrupt:
        pass
    finally:
        device.stop()
        close_source()

    if next_url:
        name = stations[current_index][0] if current_index is not None else next_url
        emit(f"\r\nSwitching to: {name}")
    else:
        emit("\r\nStopped.")

    return next_url


if __name__ == "__main__":
    main()
