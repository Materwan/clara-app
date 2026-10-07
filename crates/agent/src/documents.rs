//! The text of a file of this computer, as Clara reads it: a PDF page by page, anything else as text.

use encoding_rs::WINDOWS_1252;
use std::fs;
use std::path::Path;
use std::thread;

/// A file bigger than this is surely not meant to be read whole.
pub const MAX_BYTES: u64 = 50_000_000;
const SNIFF_BYTES: usize = 8192; // read to tell a text file from a binary one

/// Extensions of files that are not text (a PDF is not in the list: it has a reader of its own).
const BINARY: &[&str] = &[
    "png", "jpg", "jpeg", "gif", "bmp", "ico", "webp", "mp3", "wav", "mp4", "mkv", "avi", "mov", "exe", "dll", "so", "bin", "zip", "7z",
    "rar", "gz", "tar", "iso", "woff", "woff2", "ttf", "otf", "sqlite", "db",
];

/// The file cannot be read as text; the message says why and is fit to show (and to send to Clara).
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct DocumentError(pub String);

impl std::fmt::Display for DocumentError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.write_str(&self.0)
    }
}

impl std::error::Error for DocumentError {}

fn extension(path: &Path) -> String {
    path.extension().map(|e| e.to_string_lossy().to_ascii_lowercase()).unwrap_or_default()
}

pub fn is_binary_name(path: &Path) -> bool {
    BINARY.contains(&extension(path).as_str())
}

fn name(path: &Path) -> String {
    path.file_name().map_or_else(|| path.display().to_string(), |n| n.to_string_lossy().into_owned())
}

/// The text of a file: a PDF's pages (`[page 1]`...), or a text file in UTF-8 (or Windows-1252, as Windows editors write it).
pub fn read_text(path: &Path) -> Result<String, DocumentError> {
    let name = name(path);
    if is_binary_name(path) {
        return Err(DocumentError(format!("{name} is not a text file.")));
    }
    let size = fs::metadata(path).map_err(|error| DocumentError(format!("Cannot read {name}: {error}.")))?.len();
    if size > MAX_BYTES {
        return Err(DocumentError(format!("{name} is too big ({} MB; at most {} MB).", size / 1_000_000, MAX_BYTES / 1_000_000)));
    }
    let data = fs::read(path).map_err(|error| DocumentError(format!("Cannot read {name}: {error}.")))?;
    if extension(path) == "pdf" {
        return pdf_text(&name, data);
    }
    if data[..data.len().min(SNIFF_BYTES)].contains(&0) {
        return Err(DocumentError(format!("{name} is not a text file.")));
    }
    Ok(match String::from_utf8(data) {
        Ok(text) => text.strip_prefix('\u{feff}').map(str::to_owned).unwrap_or(text),
        Err(error) => WINDOWS_1252.decode(error.as_bytes()).0.into_owned(),
    })
}

/// The text of each page of a PDF, `[page n]` before each page that has some.
fn pdf_text(name: &str, data: Vec<u8>) -> Result<String, DocumentError> {
    // pdf-extract panics on some damaged files: on a thread of its own, that is an error, not the end of the app
    let pages = thread::spawn(move || pdf_extract::extract_text_from_mem_by_pages(&data))
        .join()
        .map_err(|_| DocumentError(format!("{name} could not be read as a PDF.")))?
        .map_err(|error| {
            let reason = error.to_string();
            if reason.to_ascii_lowercase().contains("password") || reason.to_ascii_lowercase().contains("encrypt") {
                DocumentError(format!("{name} is protected by a password."))
            } else {
                DocumentError(format!("{name} could not be read as a PDF ({reason})."))
            }
        })?;
    let pages: Vec<String> = pages.into_iter().map(|page| page.trim().to_owned()).collect();
    if pages.iter().all(String::is_empty) {
        return Err(DocumentError(format!("{name} has no text to read (a scanned PDF?).")));
    }
    Ok(pages
        .iter()
        .enumerate()
        .filter(|(_, page)| !page.is_empty())
        .map(|(number, page)| format!("[page {}]\n{page}", number + 1))
        .collect::<Vec<_>>()
        .join("\n\n"))
}

#[cfg(test)]
pub(crate) mod tests {
    use super::*;

    /// A PDF of one page per text, made by hand (the offsets of its cross-reference table included).
    pub(crate) fn pdf_of(pages: &[&str]) -> Vec<u8> {
        let mut objects: Vec<String> = vec![];
        let count = pages.len();
        objects.push("<< /Type /Catalog /Pages 2 0 R >>".into());
        let kids: Vec<String> = (0..count).map(|n| format!("{} 0 R", 4 + n * 2)).collect();
        objects.push(format!("<< /Type /Pages /Kids [{}] /Count {count} >>", kids.join(" ")));
        objects.push("<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>".into());
        for (n, text) in pages.iter().enumerate() {
            objects.push(format!(
                "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 300 200] /Contents {} 0 R /Resources << /Font << /F1 3 0 R >> >> >>",
                5 + n * 2
            ));
            let stream = if text.is_empty() { String::new() } else { format!("BT /F1 18 Tf 20 100 Td ({text}) Tj ET") };
            objects.push(format!("<< /Length {} >>\nstream\n{stream}\nendstream", stream.len()));
        }
        let mut out = b"%PDF-1.4\n".to_vec();
        let mut offsets = vec![];
        for (n, body) in objects.iter().enumerate() {
            offsets.push(out.len());
            out.extend(format!("{} 0 obj\n{body}\nendobj\n", n + 1).bytes());
        }
        let xref = out.len();
        out.extend(format!("xref\n0 {}\n0000000000 65535 f \n", objects.len() + 1).bytes());
        for offset in offsets {
            out.extend(format!("{offset:010} 00000 n \n").bytes());
        }
        out.extend(format!("trailer\n<< /Size {} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n", objects.len() + 1).bytes());
        out
    }

    #[test]
    fn text_comes_as_utf8_or_as_windows_1252_and_binaries_are_refused() {
        let dir = tempfile::tempdir().unwrap();
        let utf8 = dir.path().join("a.txt");
        fs::write(&utf8, "\u{feff}caf\u{e9} \u{2713}").unwrap();
        assert_eq!(read_text(&utf8).unwrap(), "caf\u{e9} \u{2713}");
        let old = dir.path().join("b.txt");
        fs::write(&old, b"caf\xe9").unwrap(); // Windows-1252
        assert_eq!(read_text(&old).unwrap(), "caf\u{e9}");
        let picture = dir.path().join("c.png");
        fs::write(&picture, b"x").unwrap();
        assert_eq!(read_text(&picture).unwrap_err().0, "c.png is not a text file.");
        let disguised = dir.path().join("d.dat");
        fs::write(&disguised, b"ab\0cd").unwrap();
        assert_eq!(read_text(&disguised).unwrap_err().0, "d.dat is not a text file.");
    }

    #[test]
    fn a_pdf_is_read_page_by_page() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("two.pdf");
        fs::write(&path, pdf_of(&["Hello Clara", "Second page"])).unwrap();
        let text = read_text(&path).unwrap();
        assert!(text.starts_with("[page 1]\nHello Clara"), "{text}");
        assert!(text.contains("[page 2]\nSecond page"), "{text}");
    }

    #[test]
    fn a_pdf_without_text_or_a_broken_one_is_explained() {
        let dir = tempfile::tempdir().unwrap();
        let blank = dir.path().join("scan.pdf");
        fs::write(&blank, pdf_of(&[""])).unwrap();
        assert_eq!(read_text(&blank).unwrap_err().0, "scan.pdf has no text to read (a scanned PDF?).");
        let broken = dir.path().join("broken.pdf");
        fs::write(&broken, b"%PDF-1.4 this is not a pdf").unwrap();
        assert!(read_text(&broken).unwrap_err().0.starts_with("broken.pdf could not be read as a PDF"));
    }
}
