//! The folders of this computer that Clara may work in, and the code that works in them for her.
//!
//! The person adds a folder (the *Folders* window of the app); it gets an *alias*, and the server only ever knows that
//! alias and the id of this computer. When Clara wants to read or change something there, the server hands the app a
//! *job* ("write notes.md in the folder docs"); the app does it, and only in a folder it was told about, at a path that
//! stays inside it. So even a server that was taken over cannot reach the rest of this disk: the list of folders lives
//! here, and nothing a job says can widen it.

use regex::{Regex, RegexBuilder};
use serde_json::{Map, Value};
use std::collections::BTreeMap;
use std::fs::{self, OpenOptions};
use std::io::{self, Write};
use std::path::{Path, PathBuf};

use crate::config::{config_dir, write_private};
use crate::documents::{self, DocumentError};

pub const REGISTRY_FILE: &str = "computer-folders.json";
const LIST_LIMIT: usize = 300;
const READ_LINES: usize = 400; // lines a read gives at once
const READ_CHARS: usize = 40_000;
const SEARCH_MATCHES: usize = 60;
const SEARCH_LINE: usize = 240;
const SEARCH_FILE_BYTES: u64 = 2_000_000;
const SEARCH_FILES: usize = 5_000;
const MAX_WRITE: usize = 1_000_000;
const RESULT_LIMIT: usize = 100_000;
const IGNORED_DIRS: &[&str] =
    &[".git", ".hg", ".svn", "node_modules", "__pycache__", ".venv", "venv", ".mypy_cache", ".pytest_cache", "target"];

/// A job that cannot be done: the message says why (it goes back to Clara as it is).
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct FolderError(pub String);

impl std::fmt::Display for FolderError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.write_str(&self.0)
    }
}

impl std::error::Error for FolderError {}

impl From<DocumentError> for FolderError {
    fn from(error: DocumentError) -> Self {
        FolderError(error.0)
    }
}

fn error<T>(message: impl Into<String>) -> Result<T, FolderError> {
    Err(FolderError(message.into()))
}

/// What the system said, without its "(os error 13)".
fn io_message(error: &io::Error) -> String {
    let text = error.to_string();
    match text.rfind(" (os error") {
        Some(at) => text[..at].to_owned(),
        None => text,
    }
}

fn from_io(error: io::Error) -> FolderError {
    FolderError(io_message(&error))
}

fn thousands(number: usize) -> String {
    let digits = number.to_string();
    let mut out = String::new();
    for (i, digit) in digits.chars().enumerate() {
        if i > 0 && (digits.len() - i) % 3 == 0 {
            out.push(',');
        }
        out.push(digit);
    }
    out
}

/// A path as people read it: on Windows, canonical paths come as `\\?\C:\...`.
fn plain(path: PathBuf) -> PathBuf {
    let text = path.to_string_lossy();
    match text.strip_prefix(r"\\?\") {
        Some(rest) if !rest.starts_with("UNC\\") => PathBuf::from(rest),
        _ => path,
    }
}

/// What this computer is called to the server, and the folders it lets Clara into (alias -> path).
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Registry {
    path: PathBuf,
    pub device: String,
    pub name: String,
    pub folders: BTreeMap<String, String>,
}

impl Registry {
    pub fn default_path() -> PathBuf {
        config_dir().join(REGISTRY_FILE)
    }

    /// The registry saved at `path` (a new one, with a new id for this computer, when there is none).
    pub fn load(path: &Path) -> Registry {
        let saved: Value = fs::read_to_string(path).ok().and_then(|text| serde_json::from_str(&text).ok()).unwrap_or(Value::Null);
        let text = |name: &str| saved.get(name).and_then(Value::as_str).unwrap_or("").to_owned();
        let folders = saved
            .get("folders")
            .and_then(Value::as_object)
            .map(|folders| folders.iter().filter_map(|(alias, folder)| Some((alias.clone(), folder.as_str()?.to_owned()))).collect())
            .unwrap_or_default();
        let mut registry = Registry { path: path.to_owned(), device: text("device"), name: text("name"), folders };
        if registry.device.is_empty() {
            registry.device = uuid::Uuid::new_v4().simple().to_string()[..16].to_owned();
            registry.name = gethostname::gethostname().to_string_lossy().into_owned();
            let _ = registry.save(); // not saved: the next load makes another id, and the server hears of this one only when a folder is added
        }
        registry
    }

    pub fn load_default() -> Registry {
        Registry::load(&Registry::default_path())
    }

    pub fn save(&self) -> io::Result<()> {
        let body = serde_json::json!({"device": self.device, "name": self.name, "folders": self.folders});
        write_private(&self.path, &serde_json::to_string_pretty(&body).map_err(io::Error::other)?)
    }

    /// Lets Clara work in this folder; returns its alias (the folder's name, made unique).
    pub fn add(&mut self, folder: &Path) -> Result<String, FolderError> {
        let real = match folder.canonicalize() {
            Ok(real) if real.is_dir() => plain(real),
            _ => return error(format!("{} is not a folder.", folder.display())),
        };
        for (alias, existing) in &self.folders {
            if Path::new(existing).canonicalize().is_ok_and(|e| plain(e) == real) {
                return Ok(alias.clone());
            }
        }
        let name = real.file_name().map(|n| n.to_string_lossy().into_owned()).unwrap_or_default();
        let slug = Regex::new(r"[^\w.-]+").expect("a fixed pattern").replace_all(&name, "-").trim_matches('-').to_owned();
        let base = if slug.is_empty() { "folder".to_owned() } else { slug };
        let (mut alias, mut number) = (base.clone(), 2);
        while self.folders.contains_key(&alias) {
            alias = format!("{base}-{number}");
            number += 1;
        }
        self.folders.insert(alias.clone(), real.to_string_lossy().into_owned());
        self.save().map_err(from_io)?;
        Ok(alias)
    }

    pub fn remove(&mut self, alias: &str) -> io::Result<()> {
        if self.folders.remove(alias).is_some() {
            self.save()?;
        }
        Ok(())
    }

    /// The folder of an alias, as the system names it (links resolved).
    fn base(&self, alias: &str) -> Result<PathBuf, FolderError> {
        let Some(folder) = self.folders.get(alias) else {
            return error("That folder is not on this computer any more (it was removed from the Clara app).");
        };
        match Path::new(folder).canonicalize() {
            Ok(base) if base.is_dir() => Ok(base),
            _ => error(format!("The folder {folder} does not exist on this computer any more.")),
        }
    }
}

// ---- paths ------------------------------------------------------------------------------------------------------

const DEVICE_NAMES: &[&str] = &[
    "CON", "PRN", "AUX", "NUL", "COM1", "COM2", "COM3", "COM4", "COM5", "COM6", "COM7", "COM8", "COM9", "LPT1", "LPT2", "LPT3", "LPT4",
    "LPT5", "LPT6", "LPT7", "LPT8", "LPT9",
];

/// `NUL`, `con.txt`...: on Windows these are not files but devices (reading `CON` waits for the keyboard).
fn is_device_name(part: &str) -> bool {
    let stem = part.split('.').next().unwrap_or("").trim_end().to_ascii_uppercase();
    DEVICE_NAMES.contains(&stem.as_str())
}

fn has_drive(text: &str) -> bool {
    let mut chars = text.chars();
    matches!((chars.next(), chars.next()), (Some(letter), Some(':')) if letter.is_ascii_alphabetic())
}

/// A path inside a folder as Clara wrote it, made safe: `""` is the folder itself.
pub fn relative(path: &str) -> Result<String, FolderError> {
    let text = path.trim().replace('\\', "/");
    if matches!(text.as_str(), "" | "." | "/") {
        return Ok(String::new());
    }
    if text.starts_with('/') || has_drive(&text) {
        return error("Give a path inside the folder, not an absolute one.");
    }
    let parts: Vec<&str> = text.split('/').filter(|part| !part.is_empty() && *part != ".").collect();
    if parts.iter().any(|part| *part == ".." || part.contains('\0')) {
        return error("A path may not go up (..).");
    }
    if cfg!(windows) && parts.iter().any(|part| part.contains(':') || is_device_name(part)) {
        return error(format!("{text}: not a usable name."));
    }
    Ok(parts.join("/"))
}

/// The path with its links resolved, whether it exists or not: the deepest part that exists is resolved by the system,
/// the rest (clean names, no `..`) is added as it is. A link that leads nowhere is refused, not trusted.
fn resolve(path: &Path) -> Result<PathBuf, FolderError> {
    let mut missing = vec![];
    let mut current = path.to_owned();
    loop {
        match current.canonicalize() {
            Ok(real) => return Ok(missing.iter().rev().fold(real, |out, part| out.join(part))),
            Err(error) if error.kind() == io::ErrorKind::NotFound => {
                if fs::symlink_metadata(&current).is_ok() {
                    return self::error(format!("{}: a link that leads nowhere.", path.display()));
                }
                let (Some(name), Some(parent)) = (current.file_name(), current.parent()) else { return self::error("not a usable path.") };
                missing.push(name.to_owned());
                current = parent.to_owned();
            }
            Err(other) => return Err(from_io(other)),
        }
    }
}

fn stays_inside(base: &Path, resolved: &Path, rel: &str) -> Result<(), FolderError> {
    if resolved != base && !resolved.starts_with(base) {
        return error(format!("{rel}: that leads outside the folder."));
    }
    Ok(())
}

/// `(the resolved path, its clean relative form)`; it must stay inside `base` (links that leave it are refused).
pub fn inside(base: &Path, path: &str) -> Result<(PathBuf, String), FolderError> {
    let rel = relative(path)?;
    if rel.is_empty() {
        return Ok((base.to_owned(), rel));
    }
    let resolved = resolve(&base.join(&rel))?;
    stays_inside(base, &resolved, &rel)?;
    Ok((resolved, rel))
}

/// Like `inside`, for what is done to the thing itself: a link is deleted or moved, not what it leads to.
fn inside_itself(base: &Path, path: &str) -> Result<(PathBuf, String), FolderError> {
    let rel = relative(path)?;
    if rel.is_empty() {
        return Ok((base.to_owned(), rel));
    }
    let joined = base.join(&rel);
    let (Some(name), Some(parent)) = (joined.file_name(), joined.parent()) else { return Ok((base.to_owned(), rel)) };
    let parent = resolve(parent)?;
    stays_inside(base, &parent, &rel)?;
    Ok((parent.join(name), rel))
}

// ---- reading ----------------------------------------------------------------------------------------------------

fn lines_of(name: &str, text: &str, start: i64, end: Option<i64>) -> String {
    let lines: Vec<&str> = text.lines().collect();
    let start = start.max(1) as usize;
    if start > lines.len() {
        return format!("{name} has only {} lines.", lines.len());
    }
    let wanted_end = end.filter(|e| *e > 0).map_or(start + READ_LINES - 1, |e| e as usize);
    let mut last = lines.len().min(wanted_end).min(start + READ_LINES - 1);
    let (mut out, mut used) = (vec![], 0);
    for number in start..=last {
        let line = format!("{number:>5}  {}", lines[number - 1]);
        let length = line.chars().count();
        if used + length > READ_CHARS && !out.is_empty() {
            last = number - 1;
            break;
        }
        out.push(line);
        used += length + 1;
    }
    let more = if last < lines.len() { format!(" (read on with start_line={})", last + 1) } else { String::new() };
    format!("{name}, lines {start}-{last} of {}{more}:\n{}", lines.len(), out.join("\n"))
}

fn number(args: &Map<String, Value>, name: &str) -> Result<Option<i64>, FolderError> {
    match args.get(name) {
        None | Some(Value::Null) => Ok(None),
        Some(Value::Number(n)) => Ok(n.as_i64().or_else(|| n.as_f64().map(|f| f as i64))),
        Some(Value::String(text)) if text.trim().is_empty() => Ok(None),
        Some(Value::String(text)) => text.trim().parse().map(Some).or_else(|_| error(format!("{name} must be a number."))),
        Some(_) => error(format!("{name} must be a number.")),
    }
}

fn text_arg<'a>(args: &'a Map<String, Value>, name: &str) -> &'a str {
    args.get(name).and_then(Value::as_str).unwrap_or("")
}

fn flag(args: &Map<String, Value>, name: &str) -> bool {
    match args.get(name) {
        Some(Value::Bool(value)) => *value,
        Some(Value::String(text)) => matches!(text.to_ascii_lowercase().as_str(), "true" | "1" | "yes"),
        Some(Value::Number(n)) => n.as_i64().unwrap_or(0) != 0,
        _ => false,
    }
}

/// Does the jobs of the server inside the folders of a registry.
pub struct LocalFolders {
    pub registry: Registry,
}

impl LocalFolders {
    pub fn new(registry: Registry) -> LocalFolders {
        LocalFolders { registry }
    }

    pub fn run(&self, alias: &str, op: &str, args: &Map<String, Value>) -> Result<String, FolderError> {
        let base = self.registry.base(alias)?;
        let result = match op {
            "list" => list(&base, args),
            "read" => read(&base, args),
            "search" => search(&base, args),
            "write" => write(&base, args),
            "delete" => delete(&base, args),
            "move" => rename(&base, args),
            _ => error(format!("This computer does not do {op}.")),
        }?;
        Ok(if result.chars().count() <= RESULT_LIMIT { result } else { result.chars().take(RESULT_LIMIT - 1).collect::<String>() + "…" })
    }
}

fn list(base: &Path, args: &Map<String, Value>) -> Result<String, FolderError> {
    let (path, rel) = inside(base, text_arg(args, "path"))?;
    if !path.is_dir() {
        return error(format!("{} is not a folder.", if rel.is_empty() { "/" } else { &rel }));
    }
    let mut entries: Vec<(bool, String, PathBuf)> = fs::read_dir(&path)
        .map_err(from_io)?
        .filter_map(Result::ok)
        .map(|entry| (entry.path().is_dir(), entry.file_name().to_string_lossy().into_owned(), entry.path()))
        .collect();
    entries.sort_by_key(|entry| (!entry.0, entry.1.to_lowercase()));
    let mut lines: Vec<String> = entries
        .iter()
        .take(LIST_LIMIT)
        .map(|(is_dir, name, path)| match (is_dir, fs::metadata(path)) {
            (true, _) => format!("{name}/"),
            (false, Ok(meta)) => format!("{name} ({} bytes)", thousands(meta.len() as usize)),
            (false, Err(_)) => name.clone(),
        })
        .collect();
    if entries.len() > LIST_LIMIT {
        lines.push(format!("[{} more]", entries.len() - LIST_LIMIT));
    }
    Ok(if lines.is_empty() { "(empty)".to_owned() } else { lines.join("\n") })
}

fn read(base: &Path, args: &Map<String, Value>) -> Result<String, FolderError> {
    let (path, rel) = inside(base, text_arg(args, "path"))?;
    if !path.is_file() {
        return error(format!("{} is not a file.", if rel.is_empty() { "/" } else { &rel }));
    }
    let (start, end) = (number(args, "start_line")?.unwrap_or(1), number(args, "end_line")?);
    Ok(lines_of(&rel, &documents::read_text(&path)?, start, end))
}

struct Search<'a> {
    base: &'a Path,
    pattern: Regex,
    matches: Vec<String>,
    files: usize,
    scanned: usize,
}

impl Search<'_> {
    fn done(&self) -> bool {
        self.matches.len() >= SEARCH_MATCHES || self.scanned > SEARCH_FILES
    }

    /// Looks for the pattern in one file (a file too big, or not text, is passed over).
    fn scan(&mut self, file: &Path) {
        self.scanned += 1;
        if self.done() || fs::metadata(file).map_or(true, |meta| meta.len() > SEARCH_FILE_BYTES) {
            return;
        }
        let Ok(text) = documents::read_text(file) else { return };
        let short = file.strip_prefix(self.base).unwrap_or(file).to_string_lossy().replace('\\', "/");
        let room = SEARCH_MATCHES - self.matches.len();
        let found: Vec<String> = text
            .lines()
            .enumerate()
            .filter(|(_, line)| self.pattern.is_match(line))
            .take(room)
            .map(|(n, line)| format!("{short}:{}: {}", n + 1, line.trim().chars().take(SEARCH_LINE).collect::<String>()))
            .collect();
        if !found.is_empty() {
            self.files += 1;
            self.matches.extend(found);
        }
    }

    /// The files of a folder, then its sub folders: each in alphabetical order.
    fn walk(&mut self, folder: &Path) {
        let Ok(read) = fs::read_dir(folder) else { return };
        let (mut files, mut folders) = (vec![], vec![]);
        for entry in read.filter_map(Result::ok) {
            let Ok(kind) = entry.file_type() else { continue };
            let name = entry.file_name().to_string_lossy().into_owned();
            if kind.is_symlink() {
                continue; // never followed: what it leads to may be outside
            } else if kind.is_dir() {
                if !IGNORED_DIRS.contains(&name.as_str()) {
                    folders.push((name, entry.path()));
                }
            } else if kind.is_file() && !documents::is_binary_name(&entry.path()) {
                files.push((name, entry.path()));
            }
        }
        files.sort();
        folders.sort();
        for (_, file) in files {
            self.scan(&file);
            if self.done() {
                return;
            }
        }
        for (_, sub) in folders {
            if self.done() {
                return;
            }
            self.walk(&sub);
        }
    }
}

fn search(base: &Path, args: &Map<String, Value>) -> Result<String, FolderError> {
    let (path, _) = inside(base, text_arg(args, "path"))?;
    let query = text_arg(args, "query").trim();
    if query.is_empty() {
        return error("query is empty.");
    }
    let source = if flag(args, "regex") { query.to_owned() } else { regex::escape(query) };
    let pattern =
        RegexBuilder::new(&source).case_insensitive(true).build().or_else(|e| error(format!("not a valid regular expression ({e}).")))?;
    let mut search = Search { base, pattern, matches: vec![], files: 0, scanned: 0 };
    if path.is_file() {
        search.scan(&path); // a file given as the place to search: only that one is looked in
    } else {
        search.walk(&path);
    }
    if search.matches.is_empty() {
        return Ok(format!("No match for {query:?}."));
    }
    let note = if search.matches.len() >= SEARCH_MATCHES { format!("\n[only the first {SEARCH_MATCHES} shown]") } else { String::new() };
    Ok(format!("Matches in {} file{}:\n{}{note}", search.files, if search.files == 1 { "" } else { "s" }, search.matches.join("\n")))
}

// ---- changing ---------------------------------------------------------------------------------------------------

fn write(base: &Path, args: &Map<String, Value>) -> Result<String, FolderError> {
    let mode = args.get("mode").and_then(Value::as_str).filter(|m| !m.is_empty()).unwrap_or("create");
    if !matches!(mode, "create" | "overwrite" | "append") {
        return error("mode must be one of: create, overwrite, append.");
    }
    let Some(content) = args.get("content").and_then(Value::as_str).filter(|c| c.chars().count() <= MAX_WRITE) else {
        return error(format!("content must be text of at most {} characters.", thousands(MAX_WRITE)));
    };
    let (path, rel) = inside(base, text_arg(args, "path"))?;
    if rel.is_empty() {
        return error("Give the path of the file to write.");
    }
    if path.is_dir() {
        return error(format!("{rel} is a folder."));
    }
    let exists = path.exists();
    if let Some(parent) = path.parent() {
        fs::create_dir_all(parent).map_err(from_io)?;
    }
    let mut options = OpenOptions::new();
    match mode {
        "create" => options.write(true).create_new(true),
        "overwrite" => options.write(true).create(true).truncate(true),
        _ => options.append(true).create(true),
    };
    let mut file = options.open(&path).map_err(|e| match e.kind() {
        io::ErrorKind::AlreadyExists => FolderError(format!("{rel} already exists: use mode overwrite or append.")),
        _ => from_io(e),
    })?;
    file.write_all(content.as_bytes()).map_err(from_io)?;
    let verb = match mode {
        "create" => "Created",
        "overwrite" if exists => "Replaced",
        "overwrite" => "Created",
        _ => "Added to",
    };
    Ok(format!("{verb} {rel} ({} characters).", thousands(content.chars().count())))
}

fn delete(base: &Path, args: &Map<String, Value>) -> Result<String, FolderError> {
    let (path, rel) = inside_itself(base, text_arg(args, "path"))?;
    if rel.is_empty() {
        return error("The folder itself cannot be deleted.");
    }
    let Ok(meta) = fs::symlink_metadata(&path) else { return error(format!("No such file: {rel}.")) };
    if meta.is_file() || meta.file_type().is_symlink() {
        // a link to a folder is removed like a folder on Windows
        fs::remove_file(&path).or_else(|_| fs::remove_dir(&path)).map_err(from_io)?;
        return Ok(format!("Deleted {rel}."));
    }
    if meta.is_dir() {
        fs::remove_dir(&path).or_else(|_| error(format!("{rel} is a folder with something in it: delete its files first.")))?;
        return Ok(format!("Deleted the empty folder {rel}."));
    }
    error(format!("No such file: {rel}."))
}

fn rename(base: &Path, args: &Map<String, Value>) -> Result<String, FolderError> {
    let (source, rel) = inside_itself(base, text_arg(args, "path"))?;
    let (dest, to) = inside_itself(base, text_arg(args, "dest"))?;
    if rel.is_empty() || to.is_empty() {
        return error("Give the path to move and where to.");
    }
    if fs::symlink_metadata(&source).is_err() {
        return error(format!("No such file: {rel}."));
    }
    if fs::symlink_metadata(&dest).is_ok() {
        return error(format!("{to} already exists: nothing was moved."));
    }
    if dest.starts_with(&source) {
        return error(format!("{rel} cannot be moved into itself."));
    }
    if let Some(parent) = dest.parent() {
        fs::create_dir_all(parent).map_err(from_io)?;
    }
    fs::rename(&source, &dest).map_err(from_io)?;
    Ok(format!("Moved {rel} to {to}."))
}
