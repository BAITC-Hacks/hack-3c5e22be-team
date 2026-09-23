const paths = {
 search:'<circle cx="10.5" cy="10.5" r="6.5"/><path d="m16 16 4.5 4.5"/>',
 chat:'<rect x="3" y="3" width="18" height="16" rx="4"/><path d="m7 19-1 3 6-3M8 9h8m-8 4h5"/>',
 box:'<path d="m12 3 9 5-9 5-9-5 9-5Zm-9 5v10l9 4 9-4V8M12 13v9M7 6l10 5"/>',
 bag:'<rect x="4" y="7" width="16" height="14" rx="3"/><path d="M8 8V6a4 4 0 0 1 8 0v2"/>',
 truck:'<path d="M2 5h12v12H2zm12 5h4l4 4v3h-8"/><circle cx="6" cy="19" r="2"/><circle cx="18" cy="19" r="2"/>',
 plus:'<path d="M12 5v14M5 12h14"/>', close:'<path d="m6 6 12 12M6 18 18 6"/>',
 help:'<circle cx="12" cy="12" r="9"/><path d="M9.5 9a2.5 2.5 0 1 1 4 2c-1.5 1-1.5 1-1.5 3M12 17h.01"/>',
 sun:'<circle cx="12" cy="12" r="4"/><path d="M12 1v2m0 18v2M1 12h2m18 0h2M4 4l1.5 1.5m13 13L20 20M4 20l1.5-1.5m13-13L20 4"/>',
 moon:'<path d="M20 15a8 8 0 0 1-11-11 9 9 0 1 0 11 11Z"/>',
 user:'<circle cx="12" cy="8" r="3"/><path d="M5 21v-3a7 7 0 0 1 14 0v3"/>',
 bot:'<rect x="3" y="5" width="18" height="14" rx="7"/><path d="M8 10v3m8-3v3m-7 9 3-3 3 3M12 2v3"/>',
 menu:'<path d="M4 6h16M4 12h16M4 18h16"/>', send:'<path d="m3 3 19 9L3 21l3-9-3-9Zm3 9h16"/>',
 compare:'<path d="M3 7h17m-4-4 4 4-4 4M21 17H4m4-4-4 4 4 4"/>',
 grid:'<rect x="3" y="3" width="7" height="7" rx="1.5"/><rect x="14" y="3" width="7" height="7" rx="1.5"/><rect x="3" y="14" width="7" height="7" rx="1.5"/><rect x="14" y="14" width="7" height="7" rx="1.5"/>',
 refresh:'<path d="M20 7v5h-5M4 17v-5h5M6 5a8 8 0 0 1 14 7M4 12a8 8 0 0 0 14 7"/>',
 arrow:'<path d="M5 12h14m-5-5 5 5-5 5"/>',
 spark:'<path d="m12 2 2.8 7.2L22 12l-7.2 2.8L12 22l-2.8-7.2L2 12l7.2-2.8L12 2Z"/>',
};
export function icon(name) {
 const span = document.createElement('span'); span.className = 'icon'; span.setAttribute('aria-hidden', 'true');
 // Only fixed local SVG paths, never catalog or user HTML.
 span.innerHTML = `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round">${paths[name] || paths.box}</svg>`;
 return span;
}
export function hydrateIcons() { document.querySelectorAll('[data-icon]').forEach(node => node.replaceChildren(icon(node.dataset.icon))); }
