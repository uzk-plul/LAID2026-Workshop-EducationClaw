//! Per-mood colour palettes.

use crate::config::Mood;
use ratatui::style::Color;

#[derive(Clone, Copy)]
pub struct Theme {
    /// Behind the whole chassis.
    pub void: Color,
    /// The dark panel the eyes and message sit on.
    pub screen: Color,
    /// Lit top edge of the chassis.
    pub bezel_hi: Color,
    pub bezel: Color,
    /// Shaded bottom edge of the chassis.
    pub bezel_lo: Color,
    /// Thin inner rim between chassis and screen.
    pub rim: Color,
    /// Eyes, message text, antenna.
    pub accent: Color,
    pub accent_dim: Color,
    /// Brightest highlight, used for pulses.
    pub glow: Color,
}

pub fn theme(mood: Mood) -> Theme {
    match mood {
        Mood::Neutral => Theme {
            void: Color::Rgb(6, 7, 20),
            screen: Color::Rgb(13, 16, 43),
            bezel_hi: Color::Rgb(139, 152, 255),
            bezel: Color::Rgb(93, 106, 224),
            bezel_lo: Color::Rgb(52, 60, 150),
            rim: Color::Rgb(70, 80, 190),
            accent: Color::Rgb(56, 224, 255),
            accent_dim: Color::Rgb(24, 112, 152),
            glow: Color::Rgb(160, 246, 255),
        },
        Mood::Happy => Theme {
            void: Color::Rgb(6, 12, 20),
            screen: Color::Rgb(11, 30, 40),
            bezel_hi: Color::Rgb(152, 172, 255),
            bezel: Color::Rgb(106, 126, 240),
            bezel_lo: Color::Rgb(58, 72, 162),
            rim: Color::Rgb(84, 104, 210),
            accent: Color::Rgb(92, 255, 190),
            accent_dim: Color::Rgb(30, 138, 106),
            glow: Color::Rgb(198, 255, 228),
        },
        Mood::Sad => Theme {
            void: Color::Rgb(4, 5, 14),
            screen: Color::Rgb(9, 11, 30),
            bezel_hi: Color::Rgb(96, 106, 180),
            bezel: Color::Rgb(64, 72, 140),
            bezel_lo: Color::Rgb(34, 38, 84),
            rim: Color::Rgb(48, 56, 120),
            accent: Color::Rgb(96, 140, 220),
            accent_dim: Color::Rgb(42, 64, 118),
            glow: Color::Rgb(140, 178, 238),
        },
    }
}
