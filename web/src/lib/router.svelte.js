// Tiny hash router: #/path/with/parts -> route.parts = ["path", "with", "parts"].
export const route = $state({ parts: [] });

function read() {
  const h = location.hash.replace(/^#\/?/, "");
  route.parts = h ? h.split("/").map(decodeURIComponent) : [];
}

read();
window.addEventListener("hashchange", read);

export function go(path) {
  location.hash = path.startsWith("#") ? path : `#${path}`;
}
