// The settings page of the app: signing in, the folders of this computer, starting with Windows.
// It is a page of the app itself (not of the server), so it may call the app's commands.
"use strict";

const { invoke } = window.__TAURI__.core;
const { listen } = window.__TAURI__.event;

const $ = (id) => document.getElementById(id);

/** Calls a command of the app; its refusal comes as text, which is fit to show. */
async function call(command, args) {
  try {
    return await invoke(command, args);
  } catch (error) {
    throw new Error(typeof error === "string" ? error : (error && error.message) || String(error));
  }
}

function show(element, text) {
  element.textContent = text || "";
  element.hidden = !text;
}

let state = null;

function render(next) {
  state = next;
  $("sign-in").hidden = next.signed_in;
  $("signed-in").hidden = !next.signed_in;
  show($("notice"), next.notice);
  $("subtitle").textContent = next.signed_in ? "Settings of the desktop app" : "Your conversations, and what she remembers about you.";
  $("version").textContent = `Clara app ${next.version}`;
  if (!next.signed_in) {
    if (!$("url").value) $("url").value = next.url;
    advise();
    return;
  }
  $("server-url").textContent = next.url;
  $("user-name").textContent = next.user_name;
  $("server-status").textContent = next.server || "Waiting for the server…";
  $("computer").textContent = next.computer;
  $("autostart").checked = next.autostart;
  renderFolders(next.folders);
}

function renderFolders(folders) {
  const list = $("folders");
  list.replaceChildren(...folders.map((folder) => {
    const name = document.createElement("span");
    name.className = "alias";
    name.textContent = folder.alias;
    const path = document.createElement("span");
    path.className = "path";
    path.textContent = folder.path;
    const what = document.createElement("span");
    what.className = "what";
    what.append(name, path);
    if (folder.missing) {
      const missing = document.createElement("span");
      missing.className = "missing";
      missing.textContent = "Not on this computer any more";
      what.append(missing);
    }
    const remove = document.createElement("button");
    remove.type = "button";
    remove.textContent = "Remove";
    remove.setAttribute("aria-label", `Remove the folder ${folder.alias}`);
    remove.addEventListener("click", () => act(() => call("remove_folder", { alias: folder.alias })));
    const item = document.createElement("li");
    item.append(what, remove);
    return item;
  }));
  $("no-folders").hidden = folders.length > 0;
}

/** Runs a change and shows the state it leaves, or what went wrong. */
async function act(change) {
  show($("folders-error"), "");
  try {
    render(await change());
  } catch (error) {
    show($("folders-error"), error.message);
    refresh();
  }
}

async function refresh() {
  try {
    render(await call("get_state"));
  } catch (error) {
    show($("notice"), error.message);
  }
}

let adviceTimer = null;
function advise() {
  clearTimeout(adviceTimer);
  adviceTimer = setTimeout(async () => {
    try {
      show($("url-advice"), await call("url_advice", { url: $("url").value }));
    } catch {
      show($("url-advice"), "");
    }
  }, 250);
}

$("url").addEventListener("input", advise);

$("sign-in-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const button = $("sign-in-button");
  show($("sign-in-error"), "");
  button.disabled = true;
  button.textContent = "Signing in…";
  try {
    const next = await call("sign_in", { url: $("url").value, user: $("user").value, password: $("password").value });
    $("password").value = "";
    render(next);
    await call("open_site"); // the site opens (already signed in), and this page goes away
  } catch (error) {
    show($("sign-in-error"), error.message);
    $("password").select();
  } finally {
    button.disabled = false;
    button.textContent = "Sign in";
  }
});

$("open-site").addEventListener("click", () => call("open_site"));
$("add-folder").addEventListener("click", () => act(() => call("add_folder")));
$("autostart").addEventListener("change", (event) => act(() => call("set_autostart", { enabled: event.target.checked })));
$("sign-out").addEventListener("click", async () => {
  $("sign-out").disabled = true;
  try {
    render(await call("sign_out"));
  } catch (error) {
    show($("notice"), error.message);
  } finally {
    $("sign-out").disabled = false;
  }
});

listen("state-changed", refresh);
refresh();
