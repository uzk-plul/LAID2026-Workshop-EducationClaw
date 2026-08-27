//! robotface - a wide-screen TUI robot that speaks whatever a JSON file says.

mod config;
mod font;
mod robot;
mod theme;

use std::io::{self, Write};
use std::path::PathBuf;
use std::time::{Duration, Instant};

use clap::Parser;
use crossterm::event::{self, Event, KeyCode, KeyEventKind, KeyModifiers};
use ratatui::buffer::Buffer;
use ratatui::layout::Rect;
use ratatui::style::Color;

use config::{Mood, Payload, Source};
use robot::Face;

#[derive(Parser, Debug)]
#[command(
    name = "robotface",
    about = "A 5:1 wide-screen robot face that displays a message from a JSON file",
    long_about = "A 5:1 wide-screen robot face that displays a message from a JSON file.\n\n\
                  The file looks like:\n  \
                  { \"message\": \"Hello!\", \"mood\": \"happy\" }\n\n\
                  Moods are neutral, happy or sad. The file is watched, so editing\n\
                  it updates the face straight away.\n\n\
                  Keys: q / Esc quit, r reload, m preview the next mood."
)]
struct Args {
    /// JSON file holding the message and mood.
    #[arg(default_value = "message.json")]
    path: PathBuf,

    /// Animation frames per second.
    #[arg(long, default_value_t = 12, value_parser = clap::value_parser!(u64).range(1..=60))]
    fps: u64,

    /// How often to check the file for changes, in milliseconds.
    #[arg(long, default_value_t = 250)]
    poll_ms: u64,

    /// Read the file once and never watch it for changes.
    #[arg(long)]
    no_watch: bool,

    /// Print a single frame at WxH (e.g. 200x20) to stdout and exit.
    #[arg(long, value_name = "WxH", value_parser = parse_size)]
    snapshot: Option<(u16, u16)>,

    /// Drop colour from --snapshot output.
    #[arg(long)]
    no_color: bool,
}

fn parse_size(s: &str) -> Result<(u16, u16), String> {
    let (w, h) = s
        .split_once(['x', 'X', ','])
        .ok_or_else(|| format!("expected WxH, got {s:?}"))?;
    let w = w.trim().parse::<u16>().map_err(|e| e.to_string())?;
    let h = h.trim().parse::<u16>().map_err(|e| e.to_string())?;
    Ok((w, h))
}

fn main() -> io::Result<()> {
    let args = Args::parse();

    let mut source = Source::new(&args.path);
    let mut payload = match source.load() {
        Ok(p) => p,
        Err(e) => Payload::error(e),
    };

    if let Some((w, h)) = args.snapshot {
        return snapshot(&payload, w, h, !args.no_color);
    }

    let mut terminal = ratatui::try_init().map_err(|e| {
        io::Error::new(
            e.kind(),
            format!("could not take over the terminal ({e}); try --snapshot WxH instead"),
        )
    })?;
    let result = run(&mut terminal, &args, &mut source, &mut payload);
    let _ = ratatui::try_restore();
    result
}

fn run(
    terminal: &mut ratatui::DefaultTerminal,
    args: &Args,
    source: &mut Source,
    payload: &mut Payload,
) -> io::Result<()> {
    let frame = Duration::from_millis(1000 / args.fps);
    let poll_every = Duration::from_millis(args.poll_ms.max(20));
    let mut last_poll = Instant::now();
    let mut tick: u64 = 0;
    // Set by the `m` key to preview a mood; cleared whenever the file speaks.
    let mut mood_override: Option<Mood> = None;

    loop {
        let mood = mood_override.unwrap_or(payload.mood);
        terminal.draw(|f| {
            Face {
                message: &payload.message,
                mood,
                tick,
            }
            .render(f.area(), f.buffer_mut());
        })?;

        // Spend the rest of the frame servicing input.
        let deadline = Instant::now() + frame;
        while let Some(left) = deadline.checked_duration_since(Instant::now()) {
            if !event::poll(left)? {
                break;
            }
            match event::read()? {
                Event::Key(k) if k.kind == KeyEventKind::Press => match k.code {
                    KeyCode::Char('q') | KeyCode::Esc => return Ok(()),
                    KeyCode::Char('c') | KeyCode::Char('d')
                        if k.modifiers.contains(KeyModifiers::CONTROL) =>
                    {
                        return Ok(())
                    }
                    KeyCode::Char('r') => {
                        mood_override = None;
                        *payload = source.load().unwrap_or_else(Payload::error);
                    }
                    KeyCode::Char('m') => {
                        mood_override = Some(mood_override.unwrap_or(payload.mood).next());
                    }
                    _ => {}
                },
                _ => {}
            }
        }

        // Pick up edits to the JSON file.
        if !args.no_watch && last_poll.elapsed() >= poll_every {
            last_poll = Instant::now();
            if let Some(loaded) = source.reload_if_changed() {
                *payload = loaded.unwrap_or_else(Payload::error);
                mood_override = None;
            }
        }

        tick = tick.wrapping_add(1);
    }
}

/// Render one frame off-screen and write it to stdout, so the face can be
/// piped, redirected or checked without a terminal.
fn snapshot(payload: &Payload, w: u16, h: u16, colour: bool) -> io::Result<()> {
    let area = Rect::new(0, 0, w.max(1), h.max(1));
    let mut buf = Buffer::empty(area);
    Face {
        message: &payload.message,
        mood: payload.mood,
        tick: 0,
    }
    .render(area, &mut buf);

    let stdout = io::stdout();
    let mut out = io::BufWriter::new(stdout.lock());
    for y in 0..area.height {
        let mut line = String::new();
        for x in 0..area.width {
            let cell = &buf[(x, y)];
            if colour {
                line.push_str(&sgr(cell.fg, cell.bg));
            }
            line.push_str(cell.symbol());
        }
        if colour {
            line.push_str("\x1b[0m");
        }
        writeln!(out, "{}", line.trim_end())?;
    }
    out.flush()
}

fn sgr(fg: Color, bg: Color) -> String {
    let c = |c: Color, base: u8| match c {
        Color::Rgb(r, g, b) => format!("\x1b[{base};2;{r};{g};{b}m"),
        _ => format!("\x1b[{}m", base + 1),
    };
    format!("{}{}", c(fg, 38), c(bg, 48))
}
