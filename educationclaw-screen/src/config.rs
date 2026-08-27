//! The JSON payload that drives the face.

use serde::Deserialize;
use std::fmt;
use std::fs;
use std::path::Path;
use std::time::SystemTime;

#[derive(Debug, Clone, Copy, PartialEq, Eq, Default, Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum Mood {
    #[default]
    Neutral,
    Happy,
    Sad,
}

impl Mood {
    pub fn next(self) -> Mood {
        match self {
            Mood::Neutral => Mood::Happy,
            Mood::Happy => Mood::Sad,
            Mood::Sad => Mood::Neutral,
        }
    }
}

impl fmt::Display for Mood {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        let s = match self {
            Mood::Neutral => "neutral",
            Mood::Happy => "happy",
            Mood::Sad => "sad",
        };
        f.write_str(s)
    }
}

impl std::str::FromStr for Mood {
    type Err = String;

    fn from_str(s: &str) -> Result<Self, Self::Err> {
        match s.trim().to_ascii_lowercase().as_str() {
            "neutral" | "" => Ok(Mood::Neutral),
            "happy" => Ok(Mood::Happy),
            "sad" => Ok(Mood::Sad),
            other => Err(format!("unknown mood {other:?} (neutral, happy or sad)")),
        }
    }
}

/// `{ "message": "Hello!", "mood": "happy" }`
#[derive(Debug, Clone, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Payload {
    #[serde(alias = "text", default)]
    pub message: String,
    #[serde(default)]
    pub mood: Mood,
}

impl Default for Payload {
    fn default() -> Self {
        Payload {
            message: String::from("..."),
            mood: Mood::Neutral,
        }
    }
}

impl Payload {
    pub fn error(message: impl Into<String>) -> Self {
        Payload {
            message: message.into(),
            mood: Mood::Sad,
        }
    }
}

/// A payload source that remembers the file's mtime and size, so we can poll
/// for changes. Size is part of the stamp because a coarse filesystem clock can
/// give two quick edits the same timestamp.
pub struct Source {
    path: std::path::PathBuf,
    stamp: Option<(SystemTime, u64)>,
}

impl Source {
    pub fn new(path: impl AsRef<Path>) -> Self {
        Source {
            path: path.as_ref().to_path_buf(),
            stamp: None,
        }
    }

    /// Read and parse the file, remembering its mtime.
    pub fn load(&mut self) -> Result<Payload, String> {
        self.stamp = stamp_of(&self.path);
        let raw =
            fs::read_to_string(&self.path).map_err(|e| format!("{}: {e}", self.path.display()))?;
        serde_json::from_str::<Payload>(&raw).map_err(|e| format!("bad json: {e}"))
    }

    /// Reload only if the file changed since the last read.
    pub fn reload_if_changed(&mut self) -> Option<Result<Payload, String>> {
        let now = stamp_of(&self.path);
        if now != self.stamp {
            Some(self.load())
        } else {
            None
        }
    }
}

fn stamp_of(path: &Path) -> Option<(SystemTime, u64)> {
    let meta = fs::metadata(path).ok()?;
    Some((meta.modified().ok()?, meta.len()))
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::time::Duration;

    fn scratch(name: &str) -> std::path::PathBuf {
        let dir = std::env::temp_dir().join(format!("robotface-{}-{name}", std::process::id()));
        fs::create_dir_all(&dir).unwrap();
        dir.join("message.json")
    }

    #[test]
    fn parses_message_and_mood() {
        let p: Payload = serde_json::from_str(r#"{"message":"Hi","mood":"happy"}"#).unwrap();
        assert_eq!(p.message, "Hi");
        assert_eq!(p.mood, Mood::Happy);
    }

    #[test]
    fn mood_defaults_to_neutral_and_text_is_an_alias() {
        let p: Payload = serde_json::from_str(r#"{"text":"Hi"}"#).unwrap();
        assert_eq!(p.message, "Hi");
        assert_eq!(p.mood, Mood::Neutral);
    }

    #[test]
    fn reports_bad_json_instead_of_failing() {
        let path = scratch("bad");
        fs::write(&path, "{ nope").unwrap();
        assert!(Source::new(&path).load().is_err());
    }

    #[test]
    fn picks_up_edits_to_the_file() {
        let path = scratch("watch");
        fs::write(&path, r#"{"message":"one","mood":"neutral"}"#).unwrap();

        let mut source = Source::new(&path);
        assert_eq!(source.load().unwrap().message, "one");
        // Unchanged file: no reload.
        assert!(source.reload_if_changed().is_none());

        std::thread::sleep(Duration::from_millis(50));
        fs::write(&path, r#"{"text":"two","mood":"happy"}"#).unwrap();

        let reloaded = source
            .reload_if_changed()
            .expect("change detected")
            .unwrap();
        assert_eq!(reloaded.message, "two");
        assert_eq!(reloaded.mood, Mood::Happy);
        assert!(source.reload_if_changed().is_none());
    }
}
