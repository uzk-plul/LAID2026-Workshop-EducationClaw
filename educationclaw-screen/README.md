# robotface

A Rust TUI robot that fills a 5:1 screen, shows a message, and reacts to a mood.
Both come from a JSON file, and editing that file changes the face immediately.

```text
                                             ●
                                             │
   ▗██████████████████████████████████████████████████████████████████████████████████▖
   ██▗██████████████████████████████████████████████████████████████████████████████▖██
   ████            ╭──────────────────────────────────────────────────╮            ████
▗▄▄████     ▄▄▄    │       █   █        ▀█    ▀█           █          │    ▄▄▄     ████▄▄▖
██▌████     ███    │       █▄▄▄█ ▄▀▀▀▄   █     █   ▄▀▀▀▄   █          │    ███     ████▌██
██▌████     ███    │       █   █ █▀▀▀▀   █     █   █   █   ▀          │    ███     ████▌██
██▌████     ▀▀▀    │       ▀   ▀  ▀▀▀   ▀▀▀   ▀▀▀   ▀▀▀    ▀          │    ▀▀▀     ████▌██
▝▀▀████            │                                                  │            ████▀▀▘
   ████            ╰──────────────────────────────────────────────────╯            ████
   ██▝██████████████████████████████████████████████████████████████████████████████▘██
   ▝██████████████████████████████████████████████████████████████████████████████████▘
```

## Run it

```sh
cargo run --release                          # reads ./message.json
cargo run --release -- messages/happy.json
```

Size the terminal about 5:1 in *cells* — `200x20`, `160x16` and `100x10` all
work well. Anything from `40x8` upward renders something sensible; below that the
robot says so instead of drawing a mess.

Keys: `q` or `Esc` to quit, `r` to reload now, `m` to preview the next mood.

## The JSON file

```json
{
  "message": "Hello!",
  "mood": "happy"
}
```

- **`message`** — the text on the screen. `text` works as an alias, `\n` starts a
  new line, and long text wraps.
- **`mood`** — `neutral`, `happy` or `sad`. Defaults to `neutral` if omitted.

The file's timestamp and size are polled four times a second, so writing to it
from anywhere updates the face:

```sh
echo '{"message":"Done","mood":"happy"}' > message.json
```

Malformed or missing JSON is reported *on the robot's own screen* rather than
crashing the program, so it is safe to edit the file while it is running.
`demo.ps1` cycles through a few messages and moods if you want to watch it react.

## Moods

```text
   neutral       happy         sad
     ▄▄▄                       ▄▄▀
     ███          ▄█▄
     ███          █ █          ███
     ▀▀▀          ▀ ▀          ███
                                ▄
```

| mood | eyes | antenna | palette | idle |
| --- | --- | --- | --- | --- |
| `neutral` | tall rounded bars | slow cyan pulse | cyan on indigo | blinks now and then |
| `happy` | arches | fast, bright pulse | mint on teal | sparkles in the corners |
| `sad` | half-lidded, brows drooping outward | hangs down, barely lit | muted blue, dimmed throughout | a tear rolls down |

## Options

```text
robotface [PATH]

  --fps <N>          animation rate, 1-60 (default 12)
  --poll-ms <N>      file-change check interval (default 250)
  --no-watch         read the file once and stop watching it
  --snapshot <WxH>   print one frame to stdout and exit
  --no-color         drop colour from --snapshot output
```

`--snapshot` needs no terminal at all, so it works over a pipe or in CI — every
picture in this README came out of it:

```sh
robotface --snapshot 200x20 messages/sad.json > face.ansi
robotface --snapshot 90x13 --no-color messages/happy.json
```

## How it draws

Every measurement comes from the terminal size, so the face stretches to fill
whatever it is given.

- The chassis is built from block and quadrant characters, with a second bezel
  ring and side pods added when there is room for them.
- The message is set in a 5x7 pixel font ([`src/font.rs`](src/font.rs), authored
  as ASCII art) drawn with half-blocks, so one font pixel is one column wide and
  half a row tall — which comes out square, because terminal cells are about
  twice as tall as they are wide. The renderer picks the largest scale that fits,
  from 4x down to 1x, and falls back to ordinary terminal text when even 1x is
  too big for the panel.
- Colours are truecolor RGB, with faint scanlines across the screen panel.

| file | what is in it |
| --- | --- |
| [`src/robot.rs`](src/robot.rs) | layout and drawing |
| [`src/font.rs`](src/font.rs) | the 5x7 pixel font |
| [`src/theme.rs`](src/theme.rs) | per-mood palettes |
| [`src/config.rs`](src/config.rs) | JSON payload and file watching |
| [`src/main.rs`](src/main.rs) | CLI, event loop, `--snapshot` |

## Tests

```sh
cargo test
```

Covers word wrapping, the layout maths at 5:1, JSON parsing, picking up an edit
to the file, and a render of every mood at sizes from `39x7` to `240x24`.
