use clara_agent::folders::{relative, FolderError, LocalFolders, Registry};
use serde_json::{json, Map, Value};
use std::fs;
use std::path::PathBuf;

fn args(value: Value) -> Map<String, Value> {
    value.as_object().cloned().unwrap_or_default()
}

struct Docs {
    _dir: tempfile::TempDir,
    root: PathBuf, // the folder Clara may work in
    #[cfg_attr(not(unix), allow(dead_code))]
    outside: PathBuf, // next to it: what must stay out of reach (the tests with links need a system that makes them)
    alias: String,
    local: LocalFolders,
}

impl Docs {
    fn run(&self, op: &str, arguments: Value) -> Result<String, FolderError> {
        self.local.run(&self.alias, op, &args(arguments))
    }
}

fn docs() -> Docs {
    let dir = tempfile::tempdir().unwrap();
    let root = dir.path().join("docs");
    fs::create_dir_all(root.join("src")).unwrap();
    fs::write(root.join("notes.md"), "one\ntwo\nthree\n").unwrap();
    fs::write(root.join("src").join("main.py"), "print('hello')\n").unwrap();
    let outside = dir.path().join("outside");
    fs::create_dir_all(&outside).unwrap();
    fs::write(outside.join("secret.txt"), "nope").unwrap();
    fs::write(dir.path().join("secret.txt"), "nope").unwrap();
    let mut registry = Registry::load(&dir.path().join("computer-folders.json"));
    let alias = registry.add(&root).unwrap();
    Docs { _dir: dir, root, outside, alias, local: LocalFolders::new(registry) }
}

fn message(result: Result<String, FolderError>) -> String {
    match result {
        Ok(text) => panic!("expected a refusal, got: {text}"),
        Err(error) => error.0,
    }
}

#[test]
fn a_computer_has_a_stable_id_and_a_list_of_folders() {
    let dir = tempfile::tempdir().unwrap();
    let file = dir.path().join("f.json");
    let mut first = Registry::load(&file);
    assert!(!first.device.is_empty() && !first.name.is_empty());
    fs::create_dir_all(dir.path().join("Work Files")).unwrap();
    let alias = first.add(&dir.path().join("Work Files")).unwrap();
    assert_eq!(alias, "Work-Files");
    assert_eq!(first.add(&dir.path().join("Work Files")).unwrap(), alias); // the same folder: the same alias
    fs::create_dir_all(dir.path().join("other").join("Work Files")).unwrap();
    assert_eq!(first.add(&dir.path().join("other").join("Work Files")).unwrap(), "Work-Files-2");
    let mut again = Registry::load(&file);
    assert_eq!(again.device, first.device);
    assert_eq!(again.folders.keys().collect::<Vec<_>>(), ["Work-Files", "Work-Files-2"]);
    again.remove("Work-Files").unwrap();
    assert_eq!(Registry::load(&file).folders.keys().collect::<Vec<_>>(), ["Work-Files-2"]);
    assert!(again.add(&dir.path().join("missing")).is_err());
}

#[test]
fn the_file_of_the_python_app_is_understood() {
    let dir = tempfile::tempdir().unwrap();
    let file = dir.path().join("computer-folders.json");
    fs::write(&file, r#"{"device": "abc123", "name": "PC-ERWAN", "folders": {"docs": "C:\\Users\\e\\docs"}}"#).unwrap();
    let registry = Registry::load(&file);
    assert_eq!((registry.device.as_str(), registry.name.as_str()), ("abc123", "PC-ERWAN"));
    assert_eq!(registry.folders["docs"], r"C:\Users\e\docs");
}

#[test]
fn clara_lists_reads_and_searches_a_folder() {
    let d = docs();
    assert_eq!(d.run("list", json!({})).unwrap().lines().collect::<Vec<_>>(), ["src/", "notes.md (14 bytes)"]);
    let read = d.run("read", json!({"path": "notes.md", "start_line": 2})).unwrap();
    assert!(read.contains("two") && !read.contains("one"), "{read}");
    assert!(read.starts_with("notes.md, lines 2-3 of 3:"), "{read}");
    assert!(d.run("search", json!({"query": "HELLO"})).unwrap().contains("src/main.py:1: print('hello')"));
    assert!(d.run("search", json!({"query": "absent"})).unwrap().starts_with("No match"));
    assert!(d.run("search", json!({"query": "t[wh]", "regex": true})).unwrap().contains("notes.md:2: two"));
    assert!(message(d.run("search", json!({"query": "("  , "regex": true}))).starts_with("not a valid regular expression"));
    assert!(d.run("search", json!({"query": "three", "path": "notes.md"})).unwrap().contains("notes.md:3: three"));
}

#[test]
fn a_long_file_is_read_in_pieces_that_say_where_to_go_on() {
    let d = docs();
    let long: String = (1..=1000).map(|n| format!("line {n}\n")).collect();
    fs::write(d.root.join("long.txt"), long).unwrap();
    let first = d.run("read", json!({"path": "long.txt"})).unwrap();
    assert!(first.starts_with("long.txt, lines 1-400 of 1000 (read on with start_line=401):"), "{}", &first[..80]);
    let last = d.run("read", json!({"path": "long.txt", "start_line": "990"})).unwrap();
    assert!(last.starts_with("long.txt, lines 990-1000 of 1000:"));
    assert_eq!(d.run("read", json!({"path": "long.txt", "start_line": 5000})).unwrap(), "long.txt has only 1000 lines.");
    assert_eq!(message(d.run("read", json!({"path": "long.txt", "end_line": "abc"}))), "end_line must be a number.");
}

#[test]
fn clara_writes_changes_moves_and_deletes_inside_a_folder() {
    let d = docs();
    assert_eq!(
        d.run("write", json!({"path": "a/b/new.txt", "content": "hi", "mode": "create"})).unwrap(),
        "Created a/b/new.txt (2 characters)."
    );
    assert_eq!(fs::read_to_string(d.root.join("a/b/new.txt")).unwrap(), "hi");
    assert!(message(d.run("write", json!({"path": "notes.md", "content": "x", "mode": "create"}))).contains("already exists"));
    assert_eq!(d.run("write", json!({"path": "notes.md", "content": "!", "mode": "append"})).unwrap(), "Added to notes.md (1 characters).");
    assert!(fs::read(d.root.join("notes.md")).unwrap().ends_with(b"three\n!"));
    assert!(d.run("write", json!({"path": "notes.md", "content": "new", "mode": "overwrite"})).unwrap().starts_with("Replaced"));
    d.run("move", json!({"path": "notes.md", "dest": "old.md"})).unwrap();
    assert!(message(d.run("move", json!({"path": "old.md", "dest": "src/main.py"}))).contains("already exists"));
    assert!(message(d.run("move", json!({"path": "src", "dest": "src/inside"}))).contains("into itself"));
    d.run("delete", json!({"path": "old.md"})).unwrap();
    assert!(!d.root.join("old.md").exists());
    assert!(message(d.run("delete", json!({"path": "src"}))).contains("something in it"));
    assert!(message(d.run("delete", json!({"path": ""}))).contains("itself"));
    assert!(message(d.run("format", json!({}))).contains("does not do"));
    assert!(message(d.run("write", json!({"path": "x", "content": "x", "mode": "rewrite"}))).starts_with("mode must be"));
    assert!(message(d.run("write", json!({"path": "x"}))).starts_with("content must be text"));
    assert_eq!(
        d.run("write", json!({"path": "big.txt", "content": "x".repeat(1200), "mode": "create"})).unwrap(),
        "Created big.txt (1,200 characters)."
    );
}

#[test]
fn nothing_outside_the_folder_can_be_reached() {
    let d = docs();
    for path in
        ["../secret.txt", "src/../../secret.txt", "/etc/passwd", "C:\\Windows\\win.ini", "..\\x", "c:evil.txt", "\\\\server\\share\\x"]
    {
        for (op, arguments) in [
            ("read", json!({"path": path})),
            ("write", json!({"path": path, "content": "x", "mode": "overwrite"})),
            ("delete", json!({"path": path})),
            ("move", json!({"path": "notes.md", "dest": path})),
            ("move", json!({"path": path, "dest": "stolen.txt"})),
        ] {
            assert!(d.run(op, arguments).is_err(), "{op} {path} was allowed");
        }
    }
    assert_eq!(fs::read_to_string(d.root.parent().unwrap().join("secret.txt")).unwrap(), "nope");
    assert!(!d.root.join("stolen.txt").exists());
}

#[test]
fn the_clean_form_of_a_path() {
    assert_eq!(relative("./a//b/").unwrap(), "a/b");
    assert_eq!(relative("a\\b").unwrap(), "a/b");
    assert_eq!(relative("/").unwrap(), "");
    assert_eq!(relative("  ").unwrap(), "");
    assert_eq!(relative("a/../b").unwrap_err().0, "A path may not go up (..).");
    assert_eq!(relative("/abs").unwrap_err().0, "Give a path inside the folder, not an absolute one.");
}

#[cfg(unix)]
mod links {
    use super::*;
    use std::os::unix::fs::symlink;

    #[test]
    fn a_link_that_leaves_the_folder_is_refused_for_every_operation() {
        let d = docs();
        symlink(&d.outside, d.root.join("link")).unwrap();
        assert!(message(d.run("read", json!({"path": "link/secret.txt"}))).contains("outside"));
        assert!(message(d.run("list", json!({"path": "link"}))).contains("outside"));
        assert!(message(d.run("write", json!({"path": "link/new.txt", "content": "x"}))).contains("outside"));
        assert!(!d.outside.join("new.txt").exists());
        // a search does not follow it either
        assert!(d.run("search", json!({"query": "nope"})).unwrap().starts_with("No match"));
    }

    #[test]
    fn a_link_to_a_file_outside_is_refused_even_when_nothing_is_there_yet() {
        let d = docs();
        symlink(d.outside.join("not-yet.txt"), d.root.join("dangling")).unwrap();
        assert!(d.run("write", json!({"path": "dangling", "content": "x", "mode": "overwrite"})).is_err());
        assert!(!d.outside.join("not-yet.txt").exists());
        symlink(d.outside.join("secret.txt"), d.root.join("peek")).unwrap();
        assert!(message(d.run("read", json!({"path": "peek"}))).contains("outside"));
    }

    #[test]
    fn deleting_or_moving_a_link_acts_on_the_link_and_never_on_what_it_leads_to() {
        let d = docs();
        symlink(d.outside.join("secret.txt"), d.root.join("peek")).unwrap();
        d.run("move", json!({"path": "peek", "dest": "moved"})).unwrap();
        assert!(d.root.join("moved").is_symlink());
        d.run("delete", json!({"path": "moved"})).unwrap();
        assert!(!d.root.join("moved").exists());
        assert_eq!(fs::read_to_string(d.outside.join("secret.txt")).unwrap(), "nope");
    }

    #[test]
    fn a_link_that_stays_inside_works() {
        let d = docs();
        symlink(d.root.join("src"), d.root.join("inner")).unwrap();
        assert!(d.run("read", json!({"path": "inner/main.py"})).unwrap().contains("print('hello')"));
    }
}

#[test]
fn a_folder_that_was_removed_from_the_app_cannot_be_reached_any_more() {
    let mut d = docs();
    d.local.registry.remove(&d.alias).unwrap();
    assert!(message(d.run("list", json!({}))).contains("not on this computer"));
}

#[test]
fn a_folder_that_is_gone_from_the_disk_is_explained() {
    let d = docs();
    fs::remove_dir_all(&d.root).unwrap();
    assert!(message(d.run("list", json!({}))).contains("does not exist on this computer any more"));
}

#[test]
fn a_binary_or_a_pdf_in_the_folder_is_handled() {
    let d = docs();
    fs::write(d.root.join("pic.png"), b"\x89PNG").unwrap();
    assert_eq!(message(d.run("read", json!({"path": "pic.png"}))), "pic.png is not a text file.");
    assert!(d.run("search", json!({"query": "PNG"})).unwrap().starts_with("No match")); // passed over
    fs::write(d.root.join("broken.pdf"), b"not a pdf").unwrap();
    assert!(message(d.run("read", json!({"path": "broken.pdf"}))).contains("could not be read as a PDF"));
}
