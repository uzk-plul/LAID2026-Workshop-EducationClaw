//! Draws the robot face into a ratatui buffer.
//!
//! Everything is laid out from the terminal size, so the face stretches to fill
//! a 5:1 screen (e.g. 200x20 or 100x10 cells) but still degrades gracefully on
//! smaller ones.

use ratatui::buffer::Buffer;
use ratatui::layout::Rect;
use ratatui::style::{Color, Modifier};

use crate::config::Mood;
use crate::font;
use crate::theme::{theme, Theme};

pub const MIN_W: u16 = 40;
pub const MIN_H: u16 = 8;

pub struct Face<'a> {
    pub message: &'a str,
    pub mood: Mood,
    pub tick: u64,
}

impl Face<'_> {
    pub fn render(&self, area: Rect, buf: &mut Buffer) {
        let t = theme(self.mood);
        fill(buf, area, ' ', t.void, t.void);

        let Some(l) = layout(area) else {
            let msg = format!("need {MIN_W}x{MIN_H}, got {}x{}", area.width, area.height);
            let x = area.x + area.width.saturating_sub(msg.len() as u16) / 2;
            let y = area.y + area.height / 2;
            for (i, c) in msg.chars().enumerate() {
                put(buf, x + i as u16, y, c, t.accent, t.void);
            }
            return;
        };

        self.draw_antenna(&l, &t, buf);
        self.draw_chassis(&l, &t, buf);
        if l.ears {
            self.draw_ears(&l, &t, buf);
        }
        if let (Some(eye_l), Some(eye_r)) = (l.eye_l, l.eye_r) {
            let blink = self.blinking();
            self.draw_eye(eye_l, Side::Left, blink, l.screen.y, &t, buf);
            self.draw_eye(eye_r, Side::Right, blink, l.screen.y, &t, buf);
        }
        self.draw_message(&l, &t, buf);
        self.draw_flourish(&l, &t, buf);
    }

    // ---- pieces -----------------------------------------------------------

    fn draw_antenna(&self, l: &Layout, t: &Theme, buf: &mut Buffer) {
        let Some((cx, y0)) = l.ball else { return };
        let pulse = self.pulse();

        match self.mood {
            // A sad robot's antenna hangs down and barely glows.
            Mood::Sad => {
                let y = if l.stem { y0 + 1 } else { y0 };
                if l.stem {
                    put(buf, cx, y0, '╷', t.bezel_lo, t.void);
                }
                put(buf, cx, y, '•', mix(t.accent_dim, t.accent, pulse), t.void);
            }
            _ => {
                if l.stem {
                    put(buf, cx, y0 + 1, '│', t.bezel, t.void);
                }
                let colour = mix(t.accent, t.glow, pulse);
                put(buf, cx, y0, '●', colour, t.void);
                if pulse > 0.72 {
                    put(buf, cx - 1, y0, '·', t.accent_dim, t.void);
                    put(buf, cx + 1, y0, '·', t.accent_dim, t.void);
                }
            }
        }
    }

    fn draw_chassis(&self, l: &Layout, t: &Theme, buf: &mut Buffer) {
        fill(buf, l.chassis, ' ', t.void, t.void);
        // Outer shell: lit along the top, shaded along the bottom.
        bezel(buf, l.chassis, t.bezel_hi, t.bezel, t.bezel_lo, t.void);
        if l.layers == 2 {
            let inner = inset(l.chassis, 2, 1);
            bezel(buf, inner, t.rim, t.rim, t.bezel_lo, t.void);
        }
        // Screen, with faint scanlines.
        for y in l.screen.y..l.screen.bottom() {
            let bg = if (y - l.screen.y).is_multiple_of(2) {
                t.screen
            } else {
                shade(t.screen, 0.72)
            };
            for x in l.screen.x..l.screen.right() {
                put(buf, x, y, ' ', bg, bg);
            }
        }
    }

    fn draw_ears(&self, l: &Layout, t: &Theme, buf: &mut Buffer) {
        let h = (l.chassis.height / 2).max(3);
        let y0 = l.chassis.y + (l.chassis.height - h) / 2;
        for (x0, side) in [
            (l.chassis.x.saturating_sub(3), Side::Left),
            (l.chassis.right(), Side::Right),
        ] {
            for y in y0..y0 + h {
                for dx in 0..3u16 {
                    let outer = match side {
                        Side::Left => dx == 0,
                        Side::Right => dx == 2,
                    };
                    let ch = match (y == y0, y == y0 + h - 1, outer) {
                        (true, _, true) => {
                            if side == Side::Left {
                                '▗'
                            } else {
                                '▖'
                            }
                        }
                        (_, true, true) => {
                            if side == Side::Left {
                                '▝'
                            } else {
                                '▘'
                            }
                        }
                        (true, _, false) => '▄',
                        (_, true, false) => '▀',
                        _ => '█',
                    };
                    let colour = if y == y0 { t.bezel_hi } else { t.bezel_lo };
                    put(buf, x0 + dx, y, ch, colour, t.void);
                }
            }
            // A lit sliver on the inner face of the pod.
            let sx = if side == Side::Left { x0 + 2 } else { x0 };
            for y in y0 + 1..y0 + h - 1 {
                put(buf, sx, y, '▌', t.accent_dim, t.bezel_lo);
            }
        }
    }

    fn draw_eye(
        &self,
        r: Rect,
        side: Side,
        blink: bool,
        origin_y: u16,
        t: &Theme,
        buf: &mut Buffer,
    ) {
        let bg = |y: u16| screen_bg(t, origin_y, y);

        if blink {
            let y = r.y + r.height / 2;
            for x in r.x..r.right() {
                put(buf, x, y, '▄', t.accent, bg(y));
            }
            return;
        }

        match self.mood {
            Mood::Neutral => capsule(buf, r, t.accent, t, origin_y),
            Mood::Happy => {
                if r.width < 3 || r.height < 2 {
                    let y = r.y + r.height / 3;
                    for x in r.x..r.right() {
                        put(buf, x, y, '▄', t.accent, bg(y));
                    }
                    return;
                }
                // An arch: a crown across the top, soft at the shoulders, with
                // legs running down either side.
                let top = r.y + r.height / 4;
                for x in r.x..r.right() {
                    let ch = if x == r.x || x == r.right() - 1 {
                        '▄'
                    } else {
                        '█'
                    };
                    put(buf, x, top, ch, t.accent, bg(top));
                }
                for (k, ch) in [(1u16, '█'), (2, '▀')] {
                    let y = top + k;
                    if y >= r.bottom() {
                        break;
                    }
                    put(buf, r.x, y, ch, t.accent, bg(y));
                    put(buf, r.right() - 1, y, ch, t.accent, bg(y));
                }
            }
            Mood::Sad => {
                // Half-lidded eye sitting low, under a brow that droops outward.
                let body_h = (r.height / 2).max(1);
                let body = Rect::new(r.x, r.bottom() - body_h, r.width, body_h);
                capsule(buf, body, t.accent, t, origin_y);
                // Keep a gap under the brow so it reads as a brow, not a lid.
                if body.y > r.y {
                    let by = (body.y - 2).max(r.y);
                    for (i, x) in (r.x..r.right()).enumerate() {
                        let outer_half = match side {
                            Side::Left => (i as u16) < r.width.div_ceil(2),
                            Side::Right => (i as u16) >= r.width / 2,
                        };
                        let ch = if outer_half { '▄' } else { '▀' };
                        put(buf, x, by, ch, t.accent_dim, bg(by));
                    }
                }
            }
        }
    }

    fn draw_message(&self, l: &Layout, t: &Theme, buf: &mut Buffer) {
        let text_area = if l.msg.width >= 8 && l.msg.height >= 3 {
            // Rounded frame around the words, like the panel on the reference.
            let r = l.msg;
            let border = if self.mood == Mood::Sad {
                t.accent_dim
            } else {
                t.accent
            };
            for x in r.x + 1..r.right() - 1 {
                put(buf, x, r.y, '─', border, screen_bg(t, l.screen.y, r.y));
                let by = r.bottom() - 1;
                put(buf, x, by, '─', border, screen_bg(t, l.screen.y, by));
            }
            for y in r.y + 1..r.bottom() - 1 {
                let bg = screen_bg(t, l.screen.y, y);
                put(buf, r.x, y, '│', border, bg);
                put(buf, r.right() - 1, y, '│', border, bg);
            }
            put(buf, r.x, r.y, '╭', border, screen_bg(t, l.screen.y, r.y));
            put(
                buf,
                r.right() - 1,
                r.y,
                '╮',
                border,
                screen_bg(t, l.screen.y, r.y),
            );
            let by = r.bottom() - 1;
            put(buf, r.x, by, '╰', border, screen_bg(t, l.screen.y, by));
            put(
                buf,
                r.right() - 1,
                by,
                '╯',
                border,
                screen_bg(t, l.screen.y, by),
            );
            inset(r, 2, 1)
        } else {
            l.msg
        };

        if text_area.width == 0 || text_area.height == 0 {
            return;
        }

        let msg = self.message.trim();
        if msg.is_empty() {
            return;
        }

        match fit_big(msg, text_area) {
            Some((scale, lines)) => {
                self.draw_big_text(&lines, scale, text_area, l.screen.y, t, buf)
            }
            None => self.draw_plain_text(msg, text_area, l.screen.y, t, buf),
        }
    }

    fn draw_big_text(
        &self,
        lines: &[String],
        scale: u16,
        area: Rect,
        origin_y: u16,
        t: &Theme,
        buf: &mut Buffer,
    ) {
        let rows = font::cell_rows(scale);
        let gap = line_gap(scale);
        let total = lines.len() as u16 * rows + lines.len().saturating_sub(1) as u16 * gap;
        let mut y = area.y + area.height.saturating_sub(total) / 2;

        for line in lines {
            let chars: Vec<char> = line.chars().collect();
            let w = font::text_width(chars.len(), scale);
            let x0 = area.x + area.width.saturating_sub(w) / 2;
            for (i, &c) in chars.iter().enumerate() {
                let gx = x0 + i as u16 * font::advance(scale);
                for row in 0..rows {
                    for col in 0..font::glyph_cols(scale) {
                        // Two half-rows of scaled pixels share one terminal cell.
                        let px = col / scale;
                        let top = font::pixel(c, px, (row * 2) / scale);
                        let bot = font::pixel(c, px, (row * 2 + 1) / scale);
                        let ch = match (top, bot) {
                            (true, true) => '█',
                            (true, false) => '▀',
                            (false, true) => '▄',
                            (false, false) => continue,
                        };
                        let cy = y + row;
                        put(buf, gx + col, cy, ch, t.accent, screen_bg(t, origin_y, cy));
                    }
                }
            }
            y += rows + gap;
        }
    }

    fn draw_plain_text(&self, msg: &str, area: Rect, origin_y: u16, t: &Theme, buf: &mut Buffer) {
        let mut lines = wrap(msg, area.width as usize);
        if lines.len() > area.height as usize {
            lines.truncate(area.height as usize);
            if let Some(last) = lines.last_mut() {
                let max = area.width as usize;
                while last.chars().count() >= max && !last.is_empty() {
                    last.pop();
                }
                last.push('…');
            }
        }
        let y0 = area.y + (area.height - lines.len() as u16) / 2;
        for (i, line) in lines.iter().enumerate() {
            let y = y0 + i as u16;
            let w = line.chars().count() as u16;
            let x0 = area.x + area.width.saturating_sub(w) / 2;
            let bg = screen_bg(t, origin_y, y);
            for (j, c) in line.chars().enumerate() {
                put(buf, x0 + j as u16, y, c, t.accent, bg);
                if let Some(cell) = buf.cell_mut((x0 + j as u16, y)) {
                    cell.modifier.insert(Modifier::BOLD);
                }
            }
        }
    }

    /// Small mood-specific extras: sparkles when happy, a tear when sad.
    fn draw_flourish(&self, l: &Layout, t: &Theme, buf: &mut Buffer) {
        match self.mood {
            Mood::Happy => {
                let s = l.screen;
                let spots = [
                    (s.x + 1, s.y),
                    (s.right() - 2, s.y),
                    (s.x + 1, s.bottom() - 1),
                    (s.right() - 2, s.bottom() - 1),
                ];
                for (i, (x, y)) in spots.into_iter().enumerate() {
                    let phase = (self.tick / 5 + i as u64 * 2) % 8;
                    let ch = match phase {
                        0 => '·',
                        1 => '*',
                        2 => '·',
                        _ => continue,
                    };
                    put(buf, x, y, ch, t.glow, screen_bg(t, s.y, y));
                }
            }
            Mood::Sad => {
                let Some(eye) = l.eye_l else { return };
                let cyc = self.tick % 90;
                if cyc < 14 {
                    let y = eye.bottom() + (cyc / 2) as u16;
                    if y < l.screen.bottom() {
                        let x = eye.x + eye.width / 2;
                        put(buf, x, y, '▄', t.accent, screen_bg(t, l.screen.y, y));
                    }
                }
            }
            Mood::Neutral => {}
        }
    }

    // ---- animation --------------------------------------------------------

    /// 0.0 to 1.0 triangle wave driving the antenna light.
    fn pulse(&self) -> f32 {
        let period = match self.mood {
            Mood::Happy => 12.0,
            Mood::Neutral => 26.0,
            Mood::Sad => 60.0,
        };
        let p = (self.tick as f32 % period) / period;
        1.0 - (p * 2.0 - 1.0).abs()
    }

    fn blinking(&self) -> bool {
        let period: u64 = match self.mood {
            Mood::Happy => 34,
            Mood::Neutral => 46,
            Mood::Sad => 120,
        };
        let cycle = self.tick / period;
        let offset = splitmix(cycle) % (period - 4);
        let phase = self.tick % period;
        phase == offset || phase == offset + 1
    }
}

// ---- layout ---------------------------------------------------------------

#[derive(Clone, Copy, PartialEq, Eq)]
enum Side {
    Left,
    Right,
}

struct Layout {
    /// Centre column and top row of the antenna, if there is room for one.
    ball: Option<(u16, u16)>,
    stem: bool,
    chassis: Rect,
    layers: u16,
    screen: Rect,
    eye_l: Option<Rect>,
    eye_r: Option<Rect>,
    msg: Rect,
    ears: bool,
}

fn layout(area: Rect) -> Option<Layout> {
    if area.width < MIN_W || area.height < MIN_H {
        return None;
    }

    let ears = area.width >= 70;
    let ear_w = if ears { 3 } else { 0 };
    let antenna_h = if area.height >= 12 {
        2
    } else if area.height >= 9 {
        1
    } else {
        0
    };

    let chassis = Rect::new(
        area.x + ear_w,
        area.y + antenna_h,
        area.width - 2 * ear_w,
        area.height - antenna_h,
    );
    // A second bezel ring only reads well when there are rows to spare.
    let layers: u16 = if chassis.height >= 11 { 2 } else { 1 };
    let screen = inset(chassis, 2 * layers, layers);
    if screen.width < 12 || screen.height < 3 {
        return None;
    }

    // Cells are about twice as tall as they are wide, so a 2:3 cell ratio draws
    // the tall narrow eyes of the reference.
    let eye_h = (screen.height * 3 / 5).clamp(2, screen.height);
    let eye_w = (eye_h * 2 / 3).max(3);
    let gap = (screen.width / 14).max(2);
    let eye_y = screen.y + (screen.height - eye_h) / 2;
    let mut eye_l = Some(Rect::new(screen.x + gap, eye_y, eye_w, eye_h));
    let mut eye_r = Some(Rect::new(screen.right() - gap - eye_w, eye_y, eye_w, eye_h));

    let mgap = (screen.width / 18).max(2);
    let mx = screen.x + gap + eye_w + mgap;
    let mx2 = screen.right().saturating_sub(gap + eye_w + mgap);
    let vpad = if screen.height >= 9 { 1 } else { 0 };
    let mut msg = Rect::new(
        mx,
        screen.y + vpad,
        mx2.saturating_sub(mx),
        screen.height - 2 * vpad,
    );

    // Too cramped for eyes: give the whole screen to the message.
    if msg.width < 12 {
        eye_l = None;
        eye_r = None;
        msg = inset(screen, 2, vpad);
    }

    Some(Layout {
        ball: if antenna_h > 0 {
            Some((area.x + area.width / 2, area.y))
        } else {
            None
        },
        stem: antenna_h == 2,
        chassis,
        layers,
        screen,
        eye_l,
        eye_r,
        msg,
        ears,
    })
}

// ---- drawing helpers ------------------------------------------------------

fn put(buf: &mut Buffer, x: u16, y: u16, ch: char, fg: Color, bg: Color) {
    if let Some(cell) = buf.cell_mut((x, y)) {
        cell.set_char(ch).set_fg(fg).set_bg(bg);
    }
}

fn fill(buf: &mut Buffer, r: Rect, ch: char, fg: Color, bg: Color) {
    for y in r.y..r.bottom() {
        for x in r.x..r.right() {
            put(buf, x, y, ch, fg, bg);
        }
    }
}

fn inset(r: Rect, dx: u16, dy: u16) -> Rect {
    Rect::new(
        r.x + dx,
        r.y + dy,
        r.width.saturating_sub(2 * dx),
        r.height.saturating_sub(2 * dy),
    )
}

/// A chunky rounded frame: two columns thick on the sides, one row on top and
/// bottom, with quadrant characters knocking the corners off.
fn bezel(buf: &mut Buffer, r: Rect, top: Color, mid: Color, bottom: Color, bg: Color) {
    if r.width < 4 || r.height < 2 {
        return;
    }
    let (x0, x1) = (r.x, r.right() - 1);
    let (y0, y1) = (r.y, r.bottom() - 1);

    for x in x0..=x1 {
        let ch = if x == x0 {
            '▗'
        } else if x == x1 {
            '▖'
        } else {
            '█'
        };
        put(buf, x, y0, ch, top, bg);

        let ch = if x == x0 {
            '▝'
        } else if x == x1 {
            '▘'
        } else {
            '█'
        };
        put(buf, x, y1, ch, bottom, bg);
    }

    for y in y0 + 1..y1 {
        for x in [x0, x0 + 1, x1 - 1, x1] {
            put(buf, x, y, '█', mid, bg);
        }
    }
}

/// A vertical rounded bar, the neutral eye shape.
fn capsule(buf: &mut Buffer, r: Rect, fg: Color, t: &Theme, origin_y: u16) {
    for y in r.y..r.bottom() {
        let ch = if r.height >= 3 && y == r.y {
            '▄'
        } else if r.height >= 3 && y == r.bottom() - 1 {
            '▀'
        } else {
            '█'
        };
        let bg = screen_bg(t, origin_y, y);
        for x in r.x..r.right() {
            put(buf, x, y, ch, fg, bg);
        }
    }
}

/// Scanline-aware screen background for row `y`.
///
/// The phase comes from the screen's own top row, so every layer drawn on top
/// of it stays in step with the stripes behind.
fn screen_bg(t: &Theme, origin_y: u16, y: u16) -> Color {
    if y.wrapping_sub(origin_y).is_multiple_of(2) {
        t.screen
    } else {
        shade(t.screen, 0.72)
    }
}

// ---- colour ---------------------------------------------------------------

fn parts(c: Color) -> (u8, u8, u8) {
    match c {
        Color::Rgb(r, g, b) => (r, g, b),
        _ => (200, 200, 200),
    }
}

fn mix(a: Color, b: Color, t: f32) -> Color {
    let t = t.clamp(0.0, 1.0);
    let (ar, ag, ab) = parts(a);
    let (br, bg, bb) = parts(b);
    let l = |x: u8, y: u8| (x as f32 + (y as f32 - x as f32) * t).round() as u8;
    Color::Rgb(l(ar, br), l(ag, bg), l(ab, bb))
}

fn shade(c: Color, f: f32) -> Color {
    let (r, g, b) = parts(c);
    let s = |x: u8| (x as f32 * f).round().clamp(0.0, 255.0) as u8;
    Color::Rgb(s(r), s(g), s(b))
}

/// Cheap deterministic noise so blinks are irregular without a rng dependency.
fn splitmix(n: u64) -> u64 {
    let mut x = n.wrapping_add(0x9E37_79B9_7F4A_7C15);
    x = (x ^ (x >> 30)).wrapping_mul(0xBF58_476D_1CE4_E5B9);
    x = (x ^ (x >> 27)).wrapping_mul(0x94D0_49BB_1331_11EB);
    x ^ (x >> 31)
}

// ---- text -----------------------------------------------------------------

/// Blank rows between two lines of big text.
fn line_gap(scale: u16) -> u16 {
    scale.div_ceil(2)
}

/// Largest pixel-font scale at which `msg` fits `area`, with the wrapped lines.
///
/// Only breaks at spaces, so a scale that would chop a word in half is rejected
/// in favour of a smaller one - and if even scale 1 cannot hold it, the caller
/// falls back to ordinary terminal text.
fn fit_big(msg: &str, area: Rect) -> Option<(u16, Vec<String>)> {
    const MAX_SCALE: u16 = 4;
    for scale in (1..=MAX_SCALE).rev() {
        let max_chars = ((area.width + scale) / font::advance(scale)) as usize;
        if max_chars == 0 {
            continue;
        }
        if msg
            .split_whitespace()
            .any(|w| w.chars().count() > max_chars)
        {
            continue;
        }
        let lines = wrap(msg, max_chars);
        if lines.is_empty() {
            continue;
        }
        let h = lines.len() as u16 * font::cell_rows(scale)
            + lines.len().saturating_sub(1) as u16 * line_gap(scale);
        if h <= area.height {
            return Some((scale, lines));
        }
    }
    None
}

/// Greedy word wrap at `max` characters, honouring explicit newlines and
/// hard-splitting words that are too long to fit on a line of their own.
fn wrap(text: &str, max: usize) -> Vec<String> {
    if max == 0 {
        return Vec::new();
    }
    let mut out: Vec<String> = Vec::new();
    for para in text.split('\n') {
        let mut line = String::new();
        for word in para.split_whitespace() {
            let mut rest: Vec<char> = word.chars().collect();
            while rest.len() > max {
                if !line.is_empty() {
                    out.push(std::mem::take(&mut line));
                }
                out.push(rest[..max].iter().collect());
                rest = rest[max..].to_vec();
            }
            if rest.is_empty() {
                continue;
            }
            let need = if line.is_empty() {
                rest.len()
            } else {
                line.chars().count() + 1 + rest.len()
            };
            if need > max && !line.is_empty() {
                out.push(std::mem::take(&mut line));
            }
            if !line.is_empty() {
                line.push(' ');
            }
            line.extend(rest);
        }
        out.push(line);
    }
    while out.len() > 1 && out.last().is_some_and(|l| l.is_empty()) {
        out.pop();
    }
    out
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn wraps_on_word_boundaries() {
        assert_eq!(wrap("hello there world", 11), ["hello there", "world"]);
    }

    #[test]
    fn hard_splits_long_words() {
        assert_eq!(wrap("abcdefgh", 3), ["abc", "def", "gh"]);
    }

    #[test]
    fn keeps_explicit_newlines() {
        assert_eq!(wrap("a\nb", 10), ["a", "b"]);
    }

    #[test]
    fn layout_fits_a_five_to_one_screen() {
        let l = layout(Rect::new(0, 0, 200, 20)).expect("layout");
        assert!(l.eye_l.is_some() && l.eye_r.is_some());
        assert!(l.screen.width > 0 && l.screen.height > 0);
        // Nothing may spill outside the terminal.
        assert!(l.chassis.right() <= 200 && l.chassis.bottom() <= 20);
        assert!(l.msg.right() <= l.screen.right());
    }

    #[test]
    fn layout_declines_tiny_terminals() {
        assert!(layout(Rect::new(0, 0, 20, 5)).is_none());
    }

    #[test]
    fn renders_every_mood_without_panicking() {
        for mood in [Mood::Neutral, Mood::Happy, Mood::Sad] {
            for (w, h) in [(40, 8), (100, 10), (200, 20), (240, 24), (39, 7)] {
                let area = Rect::new(0, 0, w, h);
                let mut buf = Buffer::empty(area);
                Face {
                    message: "Hello!",
                    mood,
                    tick: 7,
                }
                .render(area, &mut buf);
            }
        }
    }
}
