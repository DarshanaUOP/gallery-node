// ─────────────────────────────────────────────
//  CONFIG
// ─────────────────────────────────────────────
// Backend base URL — change port if you started app.py on a different port
const API_BASE = `http://${location.hostname}:5000`;

function imgUrl(uniqueName) {
  return `${API_BASE}/api/image/${encodeURIComponent(uniqueName)}`;
}
function thumbUrl(uniqueName) {
  return `${API_BASE}/api/thumb/${encodeURIComponent(uniqueName)}`;
}

// ─────────────────────────────────────────────
//  STATE
// ─────────────────────────────────────────────
let db = { media: [], albums: [], folders: [] };
let config = {};
let currentView = 'all';      // 'all' | 'hidden' | album id
let currentSort = 'date-desc';
let showHidden = false;
let ctxTarget = null;          // media item for context menu
let currentAlbumId = null;
let filteredMedia = [];
let renderedCount = 0;
let lbIndex = 0;

// Lazy image loader for the main gallery grid. We deliberately do NOT rely on
// native <img loading="lazy">: Chromium's native lazy-load distance check is
// computed off the layout snapshot at insertion time, and with grid-template-
// columns set to auto-fill/minmax ("Auto" column setting) the grid needs an
// extra intrinsic-sizing pass to resolve track widths. Native lazy-load can
// end up evaluating images against a stale pre-final layout and then never
// re-checks them — which is why they silently never load, and why opening
// DevTools "fixes" it (docking the Network panel forces a real viewport
// resize, and only a genuine engine-level resize/scroll makes Chromium
// re-run that internal check; dispatching a synthetic 'resize' event from JS
// does not). Using our own IntersectionObserver sidesteps that heuristic
// entirely, matching the pattern already used for the map cluster panel.
let _galleryImgObserver = null;

// Multi-select mode
let selectMode = false;
let selectedUniques = new Set();

// Which folders are collapsed in the sidebar — persisted across reloads.
let collapsedFolders = new Set();
try {
  collapsedFolders = new Set(JSON.parse(localStorage.getItem('luminary_collapsed_folders') || '[]'));
} catch { collapsedFolders = new Set(); }

// ─────────────────────────────────────────────
//  BOOT
// ─────────────────────────────────────────────
async function init() {
  await loadConfig();
  await loadDB();
  renderAll();
  populateAllFilters();   // fetches /api/media/formats, /api/media/cameras, /api/locations
  setupIntersectionObserver();
  setupGalleryImageObserver();
}

// Creates (once) the IntersectionObserver that lazily loads each gallery
// card's <img data-src> when the card scrolls near the viewport. See the
// comment on _galleryImgObserver above for why this replaces native
// loading="lazy".
function setupGalleryImageObserver() {
  if (_galleryImgObserver) return;
  _galleryImgObserver = new IntersectionObserver((entries) => {
    entries.forEach(entry => {
      if (!entry.isIntersecting) return;
      const img = entry.target.querySelector('img[data-src]');
      if (img) {
        img.onload  = () => img.classList.add('loaded');
        img.onerror = () => {
          img.style.display = 'none';
          const ph = img.nextElementSibling;
          if (ph) ph.style.display = 'flex';
        };
        img.src = img.dataset.src;
        img.removeAttribute('data-src');
      }
      _galleryImgObserver.unobserve(entry.target);
    });
  }, { rootMargin: '600px' });
}

async function loadConfig() {
  try {
    const r = await fetch(`${API_BASE}/api/config`);
    config = await r.json();
    if (config.show_hidden_default) {
      showHidden = true;
      document.getElementById('show-hidden-toggle').classList.add('on');
    }
    applyConfigToUI(config);
  } catch {
    config = { lazy_load_batch: 50, show_hidden_default: false };
  }
}

async function loadDB() {
  try {
    const sort = _serverSort();
    const url  = _buildMediaUrl('/api/db', 0).replace(
      `sort=${sort}`, `sort=${sort}`
    );
    // Use /api/db for first load (returns albums too)
    const p = new URLSearchParams({
      offset: 0,
      limit:  config.media_page_size || 500,
      sort,
      ..._serverFilters(),
    });
    const r    = await fetch(`${API_BASE}/api/db?${p.toString()}`);
    const data = await r.json();
    db.media     = data.media   || [];
    db.albums    = data.albums  || [];
    db.folders   = data.folders || [];
    db._total    = data.total    || db.media.length;
    db._hasMore  = data.has_more || false;
    db._offset   = db.media.length;
    db._sort     = sort;
    db._fetching = false;
  } catch {
    db = { media: [], albums: [], folders: [], _total: 0, _hasMore: false, _offset: 0,
           _sort: 'date-desc', _fetching: false };
  }
}

// Map client currentSort to server sort param
function _serverSort() {
  if (currentSort === 'name')     return 'name';
  if (currentSort === 'date-asc') return 'date-asc';
  return 'date-desc';
}

// Build the current active filter params to send to the server
function _serverFilters() {
  const params = {};
  const fmt = document.getElementById('filter-format')?.value  || '';
  const cam = document.getElementById('filter-camera')?.value  || '';
  const loc = document.getElementById('filter-location')?.value || '';
  const q   = document.getElementById('search-input')?.value   || '';

  if (fmt) params.format   = fmt;
  if (cam) params.camera   = cam;
  if (loc) params.location = loc;
  if (q)   params.q        = q;

  // Album view — let server filter by album membership via JOIN
  if (currentView !== 'all' && currentView !== 'hidden') {
    params.album = currentView;
  }

  // Hidden handling
  if (currentView === 'hidden') {
    params.hidden = 'true';
  } else if (showHidden) {
    params.hidden = 'include';
  }
  // else: default — server excludes hidden

  return params;
}

// Build a URLSearchParams string from current filters + sort + pagination
function _buildMediaUrl(base, offset) {
  const p = new URLSearchParams({
    offset: offset,
    limit:  config.media_page_size || 500,
    sort:   db._sort || _serverSort(),
    ..._serverFilters(),
  });
  return `${API_BASE}${base}?${p.toString()}`;
}

// Fetch the next page from the server (with same active filters) and append
// matching items directly to filteredMedia + gallery grid.
// Called only by the intersection observer on scroll.
async function _fetchNextPage() {
  if (!db._hasMore || db._fetching) return;
  db._fetching = true;

  try {
    const url  = _buildMediaUrl('/api/media', db._offset);
    const r    = await fetch(url);
    if (!r.ok) { db._fetching = false; return; }
    const data     = await r.json();
    const newItems = data.items || [];

    db.media.push(...newItems);
    db._total   = data.total;
    db._hasMore = data.has_more;
    db._offset  = db.media.length;

    // Server applies all filters including album membership — render directly
    if (newItems.length > 0) {
      const startIdx = filteredMedia.length;
      filteredMedia.push(...newItems);
      const grid = document.getElementById('gallery-grid');
      newItems.forEach((item, i) => grid.appendChild(createCard(item, startIdx + i)));
      renderedCount = filteredMedia.length;
      _observeLastCard();
      softRefresh();
    }
  } catch { /* network error — observer retries on next scroll */ }

  db._fetching = false;
}

// ─────────────────────────────────────────────
//  RENDER
// ─────────────────────────────────────────────
function renderAll() {
  renderAlbumNav();
  applyFilters();
  updateStats();
  updateFooter();
}

// Soft refresh: update sidebar/stats WITHOUT resetting the grid or scroll position.
function softRefresh() {
  renderAlbumNav();
  updateStats();
  updateFooter();
  // Update visible cards in-place (hidden badge, etc.) without rebuilding the grid
  document.querySelectorAll('.media-item').forEach(card => {
    const item = db.media.find(m => m.uniqueName === card.dataset.unique);
    if (!item) return;
    const badge = card.querySelector('.hidden-badge');
    if (item.isHidden && !badge) {
      const b = document.createElement('span');
      b.className = 'hidden-badge';
      b.textContent = 'hidden';
      card.appendChild(b);
    } else if (!item.isHidden && badge) {
      badge.remove();
    }
  });
}

function renderAlbumNav() {
  const nav     = document.getElementById('album-nav');
  const folders = db.folders || [];
  const albums  = db.albums  || [];
  nav.innerHTML = '';

  if (folders.length === 0 && albums.length === 0) {
    nav.innerHTML = '<div class="album-nav-empty">No albums yet</div>';
    return;
  }

  // Folders first (each showing the albums filed inside it), then any
  // albums that aren't in a folder.
  folders.forEach(folder => {
    const folderAlbums = albums.filter(a => a.folder_id === folder.id);
    const collapsed    = collapsedFolders.has(folder.id);

    const block = document.createElement('div');
    block.className = 'folder-block';

    const head = document.createElement('div');
    head.className = 'folder-nav-item';
    head.innerHTML = `
      <span class="folder-toggle">${collapsed ? '▸' : '▾'}</span>
      <span class="folder-icon">⛁</span>
      <span class="folder-name">${escHtml(folder.name)}</span>
      <span class="folder-actions">
        <span class="album-rename-icon" title="Rename folder" onclick="event.stopPropagation();openRenameFolder('${folder.id}')">✎</span>
        <span class="album-rename-icon" title="Delete folder" onclick="event.stopPropagation();deleteFolder('${folder.id}')">✕</span>
      </span>
      <span class="album-count">${folderAlbums.length}</span>`;
    head.onclick = () => toggleFolder(folder.id);
    block.appendChild(head);

    const albumsWrap = document.createElement('div');
    albumsWrap.className = 'folder-albums' + (collapsed ? ' collapsed' : '');
    folderAlbums.forEach(album => albumsWrap.appendChild(_albumNavItem(album, true)));
    block.appendChild(albumsWrap);

    nav.appendChild(block);
  });

  albums.filter(a => !a.folder_id).forEach(album => {
    nav.appendChild(_albumNavItem(album, false));
  });
}

// Builds one album row for the sidebar tree (nested = indented under a folder)
function _albumNavItem(album, nested) {
  const d = document.createElement('div');
  d.className = 'album-nav-item' + (nested ? ' nested' : '') + (currentAlbumId === album.id ? ' active' : '');
  d.innerHTML = `
    <span style="flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${escHtml(album.name)}</span>
    <span class="album-rename-icon" title="Move to folder" onclick="event.stopPropagation();openMoveAlbum('${album.id}')">⇄</span>
    <span class="album-rename-icon" title="Rename" onclick="event.stopPropagation();openRenameAlbum('${album.id}')">✎</span>
    <span class="album-count">${album.media.length}</span>`;
  d.onclick = () => setView(album.id, d);
  return d;
}

function toggleFolder(folderId) {
  if (collapsedFolders.has(folderId)) collapsedFolders.delete(folderId);
  else collapsedFolders.add(folderId);
  try { localStorage.setItem('luminary_collapsed_folders', JSON.stringify([...collapsedFolders])); } catch {}
  renderAlbumNav();
}

function applyFilters() {
  // Reset pagination state and reload from server with current filters applied.
  // The server handles format/camera/location/search/hidden — we only do
  // album-membership filtering client-side (albums are small, already in memory).
  db.media     = [];
  db._offset   = 0;
  db._hasMore  = false;
  db._fetching = false;
  db._sort     = _serverSort();

  filteredMedia = [];
  renderedCount = 0;
  _galleryImgObserver?.disconnect();
  _lastCardObserver?.disconnect();
  _lastObservedCard = null;
  document.getElementById('gallery-grid').innerHTML = '';
  document.getElementById('empty-state').style.display = 'none';

  const p = new URLSearchParams({
    offset: 0,
    limit:  config.media_page_size || 500,
    sort:   db._sort,
    ..._serverFilters(),
  });

  fetch(`${API_BASE}/api/media?${p.toString()}`)
    .then(r => r.json())
    .then(data => {
      const newItems = data.items || [];
      db.media.push(...newItems);
      db._total   = data.total;
      db._hasMore = data.has_more;
      db._offset  = db.media.length;

      // Server applies all filters (including album JOIN) — render directly
      filteredMedia = newItems;
      renderedCount = 0;
      renderBatch();
      updateStats();
      updateFooter();
      document.getElementById('empty-state').style.display =
        (newItems.length === 0 && !db._hasMore) ? 'flex' : 'none';
    })
    .catch(() => {
      document.getElementById('empty-state').style.display = 'flex';
    });
}


function renderBatch() {
  const BATCH = config.lazy_load_batch || 50;
  const grid = document.getElementById('gallery-grid');
  const end = Math.min(renderedCount + BATCH, filteredMedia.length);
  for (let i = renderedCount; i < end; i++) {
    grid.appendChild(createCard(filteredMedia[i], i));
  }
  renderedCount = end;
  _observeLastCard();
}

function createCard(item, idx) {
  const div = document.createElement('div');
  div.className = 'media-item' + (selectMode ? ' selectable' : '') + (selectedUniques.has(item.uniqueName) ? ' selected' : '');
  div.dataset.unique = item.uniqueName;
  div.dataset.idx = idx;

  const isVideo = item.type === 'video';
  const src = thumbUrl(item.uniqueName);
  const imgHtml = `
    <img data-src="${src}" src="" class="lazy-thumb" alt="${escHtml(item.name)}">
    <div class="media-placeholder" style="display:none">
      <span class="ph-icon">${isVideo ? '▶' : '⬡'}</span>
      <span>${escHtml(item.name)}</span>
    </div>
    ${isVideo ? `
      <div class="play-btn-overlay">
        <div class="play-btn-circle"><div class="play-btn-triangle"></div></div>
      </div>
      ${item.metadata?.video?.duration_fmt
        ? `<div class="video-duration">${escHtml(item.metadata.video.duration_fmt)}</div>`
        : ''}
    ` : ''}`;

  const date = (item.metadata?.date?.modified || item.metadata?.date?.created)
    ? new Date(item.metadata.date.modified || item.metadata.date.created).toLocaleDateString('en-GB', {day:'numeric',month:'short',year:'numeric'})
    : '—';

  const subfolder = item.metadata?.file?.subfolder;

  div.innerHTML = `
    ${imgHtml}
    <div class="item-overlay"></div>
    <div class="item-info">
      <div class="item-name">${escHtml(item.name)}</div>
      ${subfolder ? `<div class="item-path">⊂ ${escHtml(subfolder)}</div>` : ''}
      <div class="item-date">${date}</div>
    </div>
    ${item.isHidden ? '<span class="hidden-badge">hidden</span>' : ''}
    <div class="item-menu-btn" onclick="openCtxMenu(event, '${item.uniqueName}')">⋮</div>
    <div class="item-select-check">✓</div>
  `;

  div.addEventListener('click', (e) => {
    if (e.target.classList.contains('item-menu-btn')) return;
    if (selectMode) { toggleItemSelected(item.uniqueName, div); return; }
    openLightbox(idx);
  });

  if (_galleryImgObserver) _galleryImgObserver.observe(div);

  return div;
}

function escHtml(s) {
  return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}

// ─────────────────────────────────────────────
//  STATS & FOOTER
// ─────────────────────────────────────────────
function updateStats() {
  document.getElementById('stat-visible').textContent = db.media.filter(m => !m.isHidden).length;
  document.getElementById('stat-hidden').textContent = db.media.filter(m => m.isHidden).length;
  document.getElementById('stat-albums').textContent = db.albums.length;
}

function updateFooter() {
  const total = db._total || db.media.length;
  const loaded = db.media.length;
  const suffix = db._hasMore ? ` (${loaded} loaded)` : '';
  document.getElementById('footer-count').textContent = `${total} photos indexed${suffix}`;
  document.getElementById('footer-albums').textContent = `${db.albums.length} albums`;
}

// ─────────────────────────────────────────────
//  FILTER DROPDOWN POPULATION (server-driven)
// ─────────────────────────────────────────────

// Called once after boot — fetches all three in parallel from the server.
// Each dropdown is populated from the full db.json, not just the loaded page.
async function populateAllFilters() {
  await Promise.all([
    populateFormatFilter(),
    populateCameraFilter(),
    populateLocationFilter(),
  ]);
}

async function populateFormatFilter() {
  try {
    const r = await fetch(`${API_BASE}/api/media/formats`);
    if (!r.ok) return;
    const formats = await r.json();   // already sorted, normalised array of strings
    const sel     = document.getElementById('filter-format');
    const prev    = sel.value;
    sel.innerHTML = '<option value="">All Formats</option>';
    formats.forEach(f => {
      const o = document.createElement('option');
      o.value = f; o.textContent = f;
      sel.appendChild(o);
    });
    if (prev && formats.includes(prev)) sel.value = prev;
  } catch { /* backend not running — leave as-is */ }
}

async function populateCameraFilter() {
  try {
    const r = await fetch(`${API_BASE}/api/media/cameras`);
    if (!r.ok) return;
    const cameras = await r.json();   // sorted array of "Make Model" strings
    const sel     = document.getElementById('filter-camera');
    const prev    = sel.value;
    sel.innerHTML = '<option value="">All Cameras</option>';
    cameras.forEach(c => {
      const o = document.createElement('option');
      o.value = c; o.textContent = c;
      sel.appendChild(o);
    });
    if (prev && cameras.includes(prev)) sel.value = prev;
  } catch { /* backend not running */ }
}

async function populateLocationFilter() {
  try {
    const r = await fetch(`${API_BASE}/api/locations`);
    if (!r.ok) return;
    const locations = await r.json();  // [{name, path, visibility, root, label}]
    const sel       = document.getElementById('filter-location');
    const prev      = sel.value;
    sel.innerHTML   = '<option value="">All Locations</option>';
    locations.forEach(({ root, label }) => {
      const o = document.createElement('option');
      o.value       = root;
      o.textContent = label;
      o.title       = root;
      sel.appendChild(o);
    });
    const roots = locations.map(l => l.root);
    if (prev && roots.includes(prev)) sel.value = prev;
  } catch { /* backend not running */ }
}

// Picker location dropdown (used inside Add Photos modal)
async function populatePickerLocationFilter() {
  const sel = document.getElementById('photo-picker-location');
  if (!sel) return;
  sel.innerHTML = '<option value="">All Locations</option>';

  try {
    // Fetch full directory tree (source roots + all subdirectories with files)
    const r = await fetch(`${API_BASE}/api/media/subdirs`);
    if (!r.ok) return;
    const dirs = await r.json(); // [{path, source_root, label, depth}]

    if (dirs.length === 0) return;

    // Group by source_root to render as optgroup sections
    const groups = {};
    const rootLabels = {};

    // Also fetch configured location names for optgroup labels
    try {
      const lr = await fetch(`${API_BASE}/api/locations`);
      if (lr.ok) {
        const locs = await lr.json();
        locs.forEach(l => { rootLabels[l.root] = l.label; });
      }
    } catch { /* fall back to path segment */ }

    dirs.forEach(d => {
      const g = d.source_root || d.path;
      if (!groups[g]) groups[g] = [];
      groups[g].push(d);
    });

    Object.entries(groups).forEach(([srcRoot, items]) => {
      const groupLabel = rootLabels[srcRoot] || srcRoot.split('/').filter(Boolean).pop() || srcRoot;
      const group = document.createElement('optgroup');
      group.label = groupLabel;

      items.forEach(d => {
        const opt   = document.createElement('option');
        opt.value   = d.path;
        opt.title   = d.path;
        // Indent sub-directory labels by depth
        const indent = '\u00A0\u00A0'.repeat(d.depth);   // non-breaking spaces
        opt.textContent = indent + (d.depth === 0 ? '⊞ ' : '↳ ') + d.label;
        group.appendChild(opt);
      });

      sel.appendChild(group);
    });
  } catch { /* backend not running */ }
}

// Show/hide "Add All" button depending on whether a location is selected
function _pickerLocationChanged() {
  const loc = document.getElementById('photo-picker-location').value;
  const btn = document.getElementById('picker-add-all-btn');
  if (btn) btn.style.display = loc ? 'inline-flex' : 'none';
  filterPickerGrid();
}

// Add ALL indexed media from the selected location directly to the album,
// fetching every page from the server — no pagination cap.
async function addAllFromLocation() {
  const loc = document.getElementById('photo-picker-location').value;
  if (!loc || !currentAlbumId) return;

  const album = db.albums.find(a => a.id === currentAlbumId);
  if (!album) return;

  const btn  = document.getElementById('picker-add-all-btn');
  btn.disabled    = true;
  btn.textContent = 'Loading…';

  try {
    const PAGE      = 500;
    let offset      = 0;
    let fetched     = 0;
    const alreadyIn = new Set(album.media);
    const toAdd     = [];

    // Fetch all pages from server
    while (true) {
      const params = new URLSearchParams({
        location: loc, limit: PAGE, offset, sort: 'date-desc',
      });
      const r = await fetch(`${API_BASE}/api/media?${params.toString()}`);
      if (!r.ok) throw new Error('Server error ' + r.status);
      const data  = await r.json();
      const items = data.items || [];
      if (items.length === 0) break;

      items.forEach(m => {
        if (!alreadyIn.has(m.uniqueName)) {
          alreadyIn.add(m.uniqueName);
          toAdd.push(m.uniqueName);
        }
      });

      fetched += items.length;
      offset  += items.length;
      btn.textContent = `Loading… ${fetched}`;

      if (!data.has_more || items.length < PAGE) break;
    }

    if (toAdd.length === 0) {
      closePhotoPicker();
      const dirName = loc.split('/').filter(Boolean).pop() || loc;
      toast(`No new photos to add from "${dirName}"`, 'info');
      return;
    }

    btn.textContent = `Adding ${toAdd.length}…`;

    // Persist directly via bulk endpoint — bypasses saveDB() / save_albums() path
    const r2 = await fetch(`${API_BASE}/api/album/add-bulk`, {
      method:  'POST',
      headers: { 'Content-Type': 'application/json' },
      body:    JSON.stringify({ albumId: currentAlbumId, uniqueNames: toAdd }),
    });
    if (!r2.ok) throw new Error('Bulk add failed: ' + r2.status);
    const result = await r2.json();

    // Update in-memory album so the UI reflects the change
    toAdd.forEach(un => { if (!album.media.includes(un)) album.media.push(un); });

    // Reset button before closing
    btn.disabled    = false;
    btn.textContent = '⊕ Add All';

    closePhotoPicker();

    // Re-fetch and render the album gallery from the server
    applyFilters();

    const dirName = loc.split('/').filter(Boolean).pop() || loc;
    toast(`Added ${result.added} photo${result.added !== 1 ? 's' : ''} from "${dirName}" to "${album.name}"`, 'success');

  } catch (err) {
    toast('Failed: ' + err.message, 'error');
    btn.disabled    = false;
    btn.textContent = '⊕ Add All';
  }
}

// ─────────────────────────────────────────────
//  VIEW SWITCHING
// ─────────────────────────────────────────────
function setView(view, el) {
  currentView    = view;
  currentAlbumId = (view !== 'all' && view !== 'hidden' && view !== 'map') ? view : null;

  if (selectMode) { selectedUniques.clear(); updateSelectionBar(); }

  document.querySelectorAll('.nav-item, .album-nav-item').forEach(n => n.classList.remove('active'));
  if (el) el.classList.add('active');

  const albumHeader = document.getElementById('album-header');
  const galleryGrid = document.getElementById('gallery-grid');
  const loadMore    = document.getElementById('load-more-trigger');
  const filterbar   = document.getElementById('filterbar');
  const statsBar    = document.getElementById('stats-bar');
  const mapView     = document.getElementById('map-view');

  if (view === 'map') {
    if (selectMode) toggleSelectMode();
    // Show map, hide gallery elements
    albumHeader.style.display = 'none';
    galleryGrid.style.display = 'none';
    loadMore.style.display    = 'none';
    filterbar.style.display   = 'none';
    statsBar.style.display    = 'none';
    mapView.classList.add('active');
    document.getElementById('topbar-title').textContent = 'Map';
    openMapView();
    return;
  }

  // Restore gallery elements when leaving map
  galleryGrid.style.display = '';
  loadMore.style.display    = '';
  filterbar.style.display   = '';
  statsBar.style.display    = '';
  mapView.classList.remove('active');

  if (currentAlbumId) {
    const album = db.albums.find(a => a.id === currentAlbumId);
    if (album) {
      albumHeader.style.display = 'flex';
      document.getElementById('album-header-name').textContent = album.name;
      document.getElementById('album-header-count').textContent = `${album.media.length} items`;
      document.getElementById('topbar-title').textContent = album.name;
    }
  } else {
    albumHeader.style.display = 'none';
    document.getElementById('topbar-title').textContent = view === 'hidden' ? 'Hidden Media' : 'All Photos';
  }

  applyFilters();
}

function setSort(sort, el) {
  currentSort = sort;
  document.querySelectorAll('.filter-chip').forEach(c => c.classList.remove('active'));
  if (el) el.classList.add('active');
  applyFilters();
}

function toggleHidden(el) {
  el.classList.toggle('on');
  showHidden = el.classList.contains('on');
  applyFilters();
}

// ─────────────────────────────────────────────
//  MULTI-SELECT MODE
// ─────────────────────────────────────────────
function toggleSelectMode() {
  selectMode = !selectMode;
  if (!selectMode) selectedUniques.clear();

  document.getElementById('select-mode-btn').classList.toggle('active', selectMode);
  document.getElementById('select-mode-btn').textContent = selectMode ? '✕ Cancel' : '☑ Select';
  document.getElementById('selection-bar').classList.toggle('active', selectMode);

  document.querySelectorAll('.media-item').forEach(card => {
    card.classList.toggle('selectable', selectMode);
    card.classList.toggle('selected', selectMode && selectedUniques.has(card.dataset.unique));
  });

  updateSelectionBar();
}

function toggleItemSelected(uniqueName, cardEl) {
  if (selectedUniques.has(uniqueName)) selectedUniques.delete(uniqueName);
  else selectedUniques.add(uniqueName);
  if (cardEl) cardEl.classList.toggle('selected', selectedUniques.has(uniqueName));
  updateSelectionBar();
}

function selectionSelectAll() {
  filteredMedia.forEach(m => selectedUniques.add(m.uniqueName));
  document.querySelectorAll('.media-item').forEach(card => card.classList.add('selected'));
  updateSelectionBar();
}

function selectionClear() {
  selectedUniques.clear();
  document.querySelectorAll('.media-item.selected').forEach(card => card.classList.remove('selected'));
  updateSelectionBar();
}

// Refreshes the count text and which action buttons make sense for the
// current view (inside an album vs. in the Hidden view vs. elsewhere).
function updateSelectionBar() {
  const count = selectedUniques.size;
  document.getElementById('selection-count').textContent = `${count} selected`;

  const removeBtn = document.getElementById('sel-remove-album-btn');
  const hideBtn   = document.getElementById('sel-hide-btn');
  const unhideBtn = document.getElementById('sel-unhide-btn');

  removeBtn.style.display = currentAlbumId ? '' : 'none';

  if (currentView === 'hidden') {
    hideBtn.style.display   = 'none';
    unhideBtn.style.display = '';
  } else {
    hideBtn.style.display   = '';
    unhideBtn.style.display = 'none';
  }

  const disable = count === 0;
  ['sel-add-album-btn','sel-remove-album-btn','sel-hide-btn','sel-unhide-btn'].forEach(id => {
    document.getElementById(id).disabled = disable;
  });
}

// Clears the current selection but leaves select mode itself on, so the
// person can immediately start picking the next batch. Select mode is only
// exited by pressing the Select/Cancel toggle button.
function _clearSelectionKeepMode() {
  selectedUniques.clear();
  document.querySelectorAll('.media-item.selected').forEach(card => card.classList.remove('selected'));
  updateSelectionBar();
}

function openBulkAddToAlbum() {
  if (selectedUniques.size === 0) return;
  openAddToAlbum([...selectedUniques]);
}

function bulkRemoveFromAlbum() {
  if (selectedUniques.size === 0 || !currentAlbumId) return;
  const album = db.albums.find(a => a.id === currentAlbumId);
  if (!album) return;
  const removed = selectedUniques.size;
  album.media = album.media.filter(un => !selectedUniques.has(un));
  saveDB();
  toast(`Removed ${removed} item${removed === 1 ? '' : 's'} from album`, 'success');
  _clearSelectionKeepMode();
  applyFilters();          // album view — items just disappear from the grid
}

function bulkHide(hidden) {
  if (selectedUniques.size === 0) return;
  const uniqueNames = [...selectedUniques];

  fetch(`${API_BASE}/api/media/hide-bulk`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ uniqueNames, hidden })
  }).catch(() => {});

  uniqueNames.forEach(un => {
    const item = db.media.find(m => m.uniqueName === un);
    if (item) item.isHidden = hidden;
  });

  toast(`${hidden ? 'Hidden' : 'Unhidden'} ${uniqueNames.length} item${uniqueNames.length === 1 ? '' : 's'}`, 'info');

  _clearSelectionKeepMode();

  if (currentView === 'hidden' || (!showHidden && hidden)) {
    // Items should no longer appear in the current view — refetch.
    applyFilters();
  } else {
    softRefresh();
  }
}

// ─────────────────────────────────────────────
//  SCROLL TO TOP
// ─────────────────────────────────────────────
(function () {
  const btn = document.getElementById('scroll-top-btn');
  const topbar = document.getElementById('topbar');

  function currentScrollTop() {
    const main = document.getElementById('main');
    // Whichever element is actually scrolling — #main or the window/document.
    return Math.max(main.scrollTop, window.scrollY || document.documentElement.scrollTop);
  }

  function onScrollTopBtn() {
    // Reveal once the user has scrolled down past the topbar's own height —
    // i.e. the point at which the topbar would have scrolled out of view
    // if it weren't sticky.
    const threshold = (topbar?.offsetHeight || 60) + 40;
    btn.classList.toggle('visible', currentScrollTop() > threshold);
  }

  document.getElementById('main').addEventListener('scroll', onScrollTopBtn, { passive: true });
  window.addEventListener('scroll', onScrollTopBtn, { passive: true });
  onScrollTopBtn();
})();

function scrollToTop() {
  document.getElementById('main').scrollTo({ top: 0, behavior: 'smooth' });
  window.scrollTo({ top: 0, behavior: 'smooth' });
}

// ─────────────────────────────────────────────
//  INFINITE SCROLL
// ─────────────────────────────────────────────
// Shared by both the dedicated sentinel div AND the last-card observer below —
// whichever fires first advances rendering/pagination.
function _maybeLoadMore() {
  if (renderedCount < filteredMedia.length) {
    // Still have locally filtered items to render
    renderBatch();
  } else if (db._hasMore && !db._fetching) {
    // All local items rendered — fetch next page from server
    _fetchNextPage();
  }
}

function setupIntersectionObserver() {
  const trigger = document.getElementById('load-more-trigger');
  const obs = new IntersectionObserver(entries => {
    if (!entries[0].isIntersecting) return;
    _maybeLoadMore();
  }, { rootMargin: '400px' });
  obs.observe(trigger);
}

// Redundant, more direct trigger: watches the actual last rendered card
// instead of only the separate #load-more-trigger sentinel. The sentinel
// approach is normally fine, but this catches any case where the sentinel's
// position hasn't caught up with a grid reflow (e.g. right after switching
// "grid columns" to Auto, where auto-fill/minmax changes the row count for
// the same items). Re-targeted every time new cards are appended.
let _lastCardObserver = null;
let _lastObservedCard = null;

function _observeLastCard() {
  if (!_lastCardObserver) {
    _lastCardObserver = new IntersectionObserver(entries => {
      if (!entries[0].isIntersecting) return;
      _lastCardObserver.unobserve(entries[0].target);
      _maybeLoadMore();
    }, { rootMargin: '400px' });
  }
  if (_lastObservedCard) _lastCardObserver.unobserve(_lastObservedCard);
  const grid = document.getElementById('gallery-grid');
  const last = grid.lastElementChild;
  if (last) {
    _lastCardObserver.observe(last);
    _lastObservedCard = last;
  }
}

// ─────────────────────────────────────────────
//  SCROLL DATE INDICATOR
// ─────────────────────────────────────────────
(function () {
  const pill      = document.getElementById('scroll-date-pill');
  const pillText  = document.getElementById('scroll-date-text');
  let hideTimer   = null;

  function fmtDate(iso) {
    if (!iso) return null;
    const d = new Date(iso);
    if (isNaN(d)) return null;
    return d.toLocaleDateString('en-GB', { day: 'numeric', month: 'short', year: 'numeric' });
  }

  function getVisibleDateRange() {
    const cards = document.querySelectorAll('#gallery-grid .media-item');
    if (!cards.length) return null;

    const vpTop    = 0;
    const vpBottom = window.innerHeight;
    const dates    = [];

    cards.forEach(card => {
      const rect = card.getBoundingClientRect();
      // Card is at least partially in the viewport
      if (rect.bottom > vpTop && rect.top < vpBottom) {
        const un   = card.dataset.unique;
        const item = filteredMedia.find(m => m.uniqueName === un);
        const iso  = item?.metadata?.date?.modified || item?.metadata?.date?.created;
        const d    = iso ? new Date(iso) : null;
        if (d && !isNaN(d)) dates.push(d);
      }
    });

    if (!dates.length) return null;

    const oldest  = new Date(Math.min(...dates));
    const newest  = new Date(Math.max(...dates));

    const fmtOld  = fmtDate(oldest.toISOString());
    const fmtNew  = fmtDate(newest.toISOString());

    return fmtOld === fmtNew ? fmtOld : `${fmtOld} — ${fmtNew}`;
  }

  function onScroll() {
    // Don't show pill if lightbox or any modal is open
    if (document.getElementById('lightbox').classList.contains('open')) return;
    if (document.querySelector('.modal-overlay.open, #photo-picker.open')) return;

    const range = getVisibleDateRange();
    if (!range) return;

    pillText.textContent = range;
    pill.classList.add('visible');

    clearTimeout(hideTimer);
    hideTimer = setTimeout(() => pill.classList.remove('visible'), 1800);
  }

  // Attach to the scrollable container (#main) and window
  document.getElementById('main').addEventListener('scroll', onScroll, { passive: true });
  window.addEventListener('scroll', onScroll, { passive: true });
})();

// ─────────────────────────────────────────────
//  CONTEXT MENU
// ─────────────────────────────────────────────
function openCtxMenu(e, uniqueName) {
  e.stopPropagation();
  ctxTarget = db.media.find(m => m.uniqueName === uniqueName);
  if (!ctxTarget) return;

  const menu = document.getElementById('ctx-menu');
  const lbl = document.getElementById('ctx-hide-label');
  const ico = document.getElementById('ctx-hide-label-icon');
  if (ctxTarget.isHidden) { lbl.textContent = 'Unhide Image'; ico.textContent = '◉'; }
  else { lbl.textContent = 'Hide Image'; ico.textContent = '◌'; }

  menu.style.display = 'block';
  let x = e.clientX, y = e.clientY;
  if (x + 180 > window.innerWidth) x -= 180;
  if (y + 220 > window.innerHeight) y -= 220;
  menu.style.left = x + 'px';
  menu.style.top = y + 'px';
}

document.addEventListener('click', () => {
  document.getElementById('ctx-menu').style.display = 'none';
});

function ctxAddToAlbum() {
  if (!ctxTarget) return;
  openAddToAlbum(ctxTarget.uniqueName);
}

function ctxRemoveFromAlbum() {
  if (!ctxTarget || !currentAlbumId) { toast('Navigate into an album first', 'info'); return; }
  const album = db.albums.find(a => a.id === currentAlbumId);
  if (!album) return;
  const idx = album.media.indexOf(ctxTarget.uniqueName);
  if (idx > -1) { album.media.splice(idx, 1); saveDB(); renderAll(); toast('Removed from album', 'success'); }
}

function ctxToggleHide() {
  if (!ctxTarget) return;
  ctxTarget.isHidden = !ctxTarget.isHidden;

  // Persist via dedicated endpoint — never touches rest of the media table
  fetch(`${API_BASE}/api/media/hide`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ uniqueName: ctxTarget.uniqueName, hidden: ctxTarget.isHidden })
  }).catch(() => {});

  // Remove card from grid if hiding and show-hidden is off
  if (!showHidden && ctxTarget.isHidden) {
    const card = document.querySelector(`.media-item[data-unique="${ctxTarget.uniqueName}"]`);
    if (card) card.remove();
  }
  softRefresh();
  toast(ctxTarget.isHidden ? 'Image hidden' : 'Image unhidden', 'info');
}

function ctxViewMeta() {
  if (!ctxTarget) return;
  openMetaModal(ctxTarget);
}

// ─────────────────────────────────────────────
//  LIGHTBOX
// ─────────────────────────────────────────────
function openLightbox(idx) {
  lbIndex = idx;
  renderLightbox();
  document.getElementById('lightbox').classList.add('open');
}

function renderLightbox() {
  const item = filteredMedia[lbIndex];
  if (!item) return;
  const lb   = document.getElementById('lb-content');
  const name = escHtml(item.name);

  // Reset zoom state on every navigation
  lbZoomReset(true);
  document.getElementById('lb-zoom-bar').classList.remove('visible');

  if (item.type === 'video') {
    const prev = lb.querySelector('video');
    if (prev) { prev.pause(); prev.src = ''; prev.load(); }
    const videoSrc  = `${API_BASE}/api/video/${encodeURIComponent(item.uniqueName)}`;
    const videoMime = _videoMime(item.name);
    const ext       = item.name.split('.').pop().toLowerCase();
    const unsupported = ['avi','mkv','wmv','flv','ts','mts'];
    const needsWarning = unsupported.includes(ext);
    lb.innerHTML = `
      <div id="lb-video-wrap">
        <video id="lb-video" controls
               preload="${config.video_preload || 'metadata'}"
               ${config.video_autoplay ? 'autoplay muted' : ''}
               playsinline
               onerror="document.getElementById('lb-video-err').style.display='flex'">
          <source src="${videoSrc}" type="${videoMime}">
        </video>
        <div id="lb-video-err" style="display:none;flex-direction:column;align-items:center;
             gap:8px;color:var(--text-muted);font-size:12px;text-align:center;padding:20px">
          <span style="font-size:32px">⚠</span>
          <span>This browser cannot play <strong style="color:var(--text)">.${ext.toUpperCase()}</strong> files.</span>
          <span>Convert to MP4/WebM, or open directly in a media player.</span>
          <a href="${videoSrc}" download="${escHtml(item.name)}"
             style="color:var(--accent);text-decoration:none;border:1px solid var(--accent);
                    padding:6px 16px;border-radius:4px;margin-top:4px">⬇ Download file</a>
        </div>
        ${needsWarning ? `<div style="font-size:11px;color:var(--danger);opacity:0.8;text-align:center">
          ⚠ .${ext.toUpperCase()} may not play in browsers — MP4 or WebM recommended</div>` : ''}
      </div>`;
  } else {
    lb.innerHTML = `
      <img id="lb-thumb" src="${thumbUrl(item.uniqueName)}" alt="${name}"
           style="filter:blur(12px);transform:scale(1.03);transition:filter 0.35s,transform 0.35s">
      <img id="lb-full"  src="${imgUrl(item.uniqueName)}"  alt="${name}"
           style="opacity:0"
           onload="
             this.style.opacity='1';
             this.style.transition='opacity 0.35s';
             var t=document.getElementById('lb-thumb');
             if(t){t.style.filter='none';t.style.transform='scale(1)';}
             document.getElementById('lb-zoom-bar').classList.add('visible');
             lbInitZoom();
           "
           onerror="
             this.remove();
             var t=document.getElementById('lb-thumb');
             if(t){t.style.filter='none';t.style.transform='scale(1)';}
           ">`;
  }

  document.getElementById('lb-filename').textContent = item.name;
  document.getElementById('lb-nav-counter').textContent =
    `${lbIndex + 1} / ${filteredMedia.length}`;

  // Build rich details panel
  _renderLbDetails(item);
}

// ─────────────────────────────────────────────
//  MINI MAP PREVIEWS (lightbox details + metadata modal)
// ─────────────────────────────────────────────
// Small, non-interactive Leaflet maps embedded inline wherever we show a
// Location section. Keyed by container id since more than one could
// theoretically exist (lightbox panel + metadata modal).
let _miniMaps = {};

function _destroyMiniMap(containerId) {
  if (_miniMaps[containerId]) {
    try { _miniMaps[containerId].remove(); } catch {}
    delete _miniMaps[containerId];
  }
}

function _renderMiniMap(containerId, lat, lng) {
  _destroyMiniMap(containerId);
  const el = document.getElementById(containerId);
  if (!el || lat == null || lng == null || isNaN(lat) || isNaN(lng)) return;

  const map = L.map(containerId, {
    center:            [lat, lng],
    zoom:              13,
    zoomControl:       false,
    dragging:          false,
    scrollWheelZoom:   false,
    doubleClickZoom:   false,
    boxZoom:           false,
    keyboard:          false,
    touchZoom:         false,
    attributionControl:false,
  });
  L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', { maxZoom: 19 }).addTo(map);
  L.marker([lat, lng]).addTo(map);
  _miniMaps[containerId] = map;

  // Container may be inside a panel that was display:none at insert time —
  // recompute tile layout once it's actually visible.
  setTimeout(() => { try { map.invalidateSize(); } catch {} }, 60);
}

function _renderLbDetails(item) {
  const m   = item.metadata || {};
  const det = document.getElementById('lb-details');

  function row(key, val) {
    if (val === null || val === undefined || val === '') return '';
    return `<div class="lb-detail-row">
      <span class="lb-detail-key">${key}</span>
      <span class="lb-detail-val">${escHtml(String(val))}</span>
    </div>`;
  }
  function section(title, rows) {
    const content = rows.join('');
    if (!content) return '';
    return `<div class="lb-detail-section">
      <div class="lb-detail-section-title">${title}</div>
      ${content}
    </div>`;
  }

  const fileSize = m.file?.size
    ? (m.file.size >= 1048576
        ? (m.file.size / 1048576).toFixed(1) + ' MB'
        : (m.file.size / 1024).toFixed(0) + ' KB')
    : null;

  const fmtDate = iso => {
    if (!iso) return null;
    try { return new Date(iso).toLocaleString(); } catch { return iso; }
  };

  let html = '';

  if (item.type === 'video') {
    html += section('Video', [
      row('Resolution', m.video?.resolution),
      row('Codec',      m.video?.codec),
      row('Duration',   m.video?.duration_fmt),
      row('FPS',        m.video?.fps),
    ]);
    html += section('File', [
      row('Format',   m.file?.format),
      row('Size',     fileSize),
      row('Path',     m.file?.path),
      row('Subfolder',m.file?.subfolder || null),
    ]);
    html += section('Date', [
      row('Created',  fmtDate(m.date?.created)),
      row('Modified', fmtDate(m.date?.modified)),
    ]);
  } else {
    html += section('Image', [
      row('Resolution',   m.image?.resolution),
      row('Color Space',  m.image?.color_space),
      row('Orientation',  m.image?.orientation),
    ]);
    html += section('Camera', [
      row('Make',         m.camera?.make),
      row('Model',        m.camera?.model),
      row('Lens',         m.camera?.lens),
      row('Aperture',     m.camera?.aperture),
      row('Shutter',      m.camera?.shutter_speed),
      row('ISO',          m.camera?.iso),
      row('Focal Length', m.camera?.focal_length),
    ]);
    html += section('File', [
      row('Format',   m.file?.format),
      row('Size',     fileSize),
      row('Path',     m.file?.path),
      row('Subfolder',m.file?.subfolder || null),
    ]);
    html += section('Date', [
      row('Created',  fmtDate(m.date?.created)),
      row('Modified', fmtDate(m.date?.modified)),
    ]);
    if (m.location?.latitude) {
      const lat = parseFloat(m.location.latitude);
      const lng = parseFloat(m.location.longitude);
      const mapHtml = (!isNaN(lat) && !isNaN(lng))
        ? `<div class="lb-detail-minimap" id="lb-detail-minimap"></div>`
        : '';
      html += section('Location', [
        mapHtml,
        row('Latitude',  m.location?.latitude),
        row('Longitude', m.location?.longitude),
        row('City',      m.location?.city),
        row('Country',   m.location?.country),
      ]);
    }
    if (m.software?.editor) {
      html += section('Software', [
        row('Editor',  m.software?.editor),
        row('Version', m.software?.version),
      ]);
    }
  }

  det.innerHTML = html || '<p style="font-size:11px;color:var(--text-muted)">No metadata available.</p>';

  _destroyMiniMap('lb-detail-minimap');
  if (m.location?.latitude) {
    _renderMiniMap('lb-detail-minimap', parseFloat(m.location.latitude), parseFloat(m.location.longitude));
  }
}

// Guess MIME type for video src attribute
function _videoMime(filename) {
  const ext = filename.split('.').pop().toLowerCase();
  const map = {
    mp4:  'video/mp4',
    m4v:  'video/mp4',
    mov:  'video/mp4',       // MOV from iPhone/Mac is H.264 in MP4 container — Chrome plays it
    webm: 'video/webm',
    '3gp':'video/3gpp',
    avi:  'video/x-msvideo',
    mkv:  'video/x-matroska',
    wmv:  'video/x-ms-wmv',
    flv:  'video/x-flv',
    ts:   'video/mp2t',
    mts:  'video/mp2t',
  };
  return map[ext] || 'video/mp4';
}

// ─────────────────────────────────────────────
//  LIGHTBOX ZOOM & PAN
// ─────────────────────────────────────────────
let _lbScale   = 1;
let _lbPanX    = 0;
let _lbPanY    = 0;
const LB_MIN   = 0.25;
const LB_MAX   = 10;

function _lbImg() { return document.getElementById('lb-full'); }
function _lbBox() { return document.getElementById('lb-content'); }

function _lbApply(animate) {
  const img = _lbImg();
  if (!img) return;
  img.style.transition = animate ? 'transform 0.2s ease' : 'none';
  img.style.transform  = `translate(${_lbPanX}px, ${_lbPanY}px) scale(${_lbScale})`;
  // Clamp pan when zoomed out back toward fit
  if (_lbScale <= 1) { _lbPanX = 0; _lbPanY = 0; img.style.transform = `scale(${_lbScale})`; }
  // Update zoom label
  document.getElementById('lb-zoom-level').textContent = Math.round(_lbScale * 100) + '%';
  // Update cursor and zoomed class
  _lbBox().classList.toggle('zoomed', _lbScale > 1);
}

function lbInitZoom() {
  _lbScale = 1; _lbPanX = 0; _lbPanY = 0;
  _lbApply(false);
  _lbAttachEvents();
}

function lbZoom(delta, cx, cy) {
  const prev = _lbScale;
  _lbScale   = Math.min(LB_MAX, Math.max(LB_MIN, _lbScale + delta * _lbScale));
  // Zoom toward cursor position if provided
  if (cx !== undefined && prev !== _lbScale) {
    const ratio = _lbScale / prev;
    _lbPanX = cx + (_lbPanX - cx) * ratio;
    _lbPanY = cy + (_lbPanY - cy) * ratio;
  }
  _lbApply(true);
}

function lbZoomReset(silent) {
  _lbScale = 1; _lbPanX = 0; _lbPanY = 0;
  if (!silent) _lbApply(true);
  document.getElementById('lb-zoom-level').textContent = '100%';
}

function lbZoom100() {
  const img = _lbImg();
  if (!img) return;
  // Compute natural size ratio vs displayed size
  const rect = img.getBoundingClientRect();
  const ratio = img.naturalWidth / (rect.width / _lbScale);
  _lbPanX = 0; _lbPanY = 0;
  _lbScale = Math.min(LB_MAX, Math.max(LB_MIN, ratio));
  _lbApply(true);
}

// ── Attach mouse wheel + drag pan events ─────────────────────────────────────
function _lbAttachEvents() {
  const box = _lbBox();

  // Mouse wheel zoom
  box._lbWheel = (e) => {
    e.preventDefault();
    const rect  = box.getBoundingClientRect();
    const cx    = e.clientX - rect.left - rect.width  / 2;
    const cy    = e.clientY - rect.top  - rect.height / 2;
    const delta = e.deltaY < 0 ? 0.15 : -0.15;
    lbZoom(delta, cx, cy);
  };
  box.addEventListener('wheel', box._lbWheel, { passive: false });

  // Drag pan
  let dragging = false, startX = 0, startY = 0, startPX = 0, startPY = 0;

  box._lbPointerDown = (e) => {
    if (_lbScale <= 1) return;
    if (e.button !== undefined && e.button !== 0) return;
    dragging = true;
    startX = e.clientX; startY = e.clientY;
    startPX = _lbPanX;  startPY = _lbPanY;
    box.classList.add('panning');
    e.preventDefault();
  };
  box._lbPointerMove = (e) => {
    if (!dragging) return;
    _lbPanX = startPX + (e.clientX - startX);
    _lbPanY = startPY + (e.clientY - startY);
    _lbApply(false);
  };
  box._lbPointerUp = () => {
    dragging = false;
    box.classList.remove('panning');
  };

  box.addEventListener('mousedown',  box._lbPointerDown);
  window.addEventListener('mousemove', box._lbPointerMove);
  window.addEventListener('mouseup',   box._lbPointerUp);

  // Touch pinch-to-zoom
  let lastDist = 0, lastMidX = 0, lastMidY = 0;
  box._lbTouchStart = (e) => {
    if (e.touches.length === 2) {
      const [a, b] = e.touches;
      lastDist = Math.hypot(b.clientX - a.clientX, b.clientY - a.clientY);
      const rect = box.getBoundingClientRect();
      lastMidX = (a.clientX + b.clientX) / 2 - rect.left - rect.width  / 2;
      lastMidY = (a.clientY + b.clientY) / 2 - rect.top  - rect.height / 2;
    }
  };
  box._lbTouchMove = (e) => {
    if (e.touches.length === 2) {
      e.preventDefault();
      const [a, b] = e.touches;
      const dist = Math.hypot(b.clientX - a.clientX, b.clientY - a.clientY);
      if (lastDist > 0) {
        const factor = dist / lastDist - 1;
        lbZoom(factor, lastMidX, lastMidY);
      }
      lastDist = dist;
    }
  };
  box.addEventListener('touchstart', box._lbTouchStart, { passive: true });
  box.addEventListener('touchmove',  box._lbTouchMove,  { passive: false });

  // Double-click: toggle fit ↔ 2×
  box._lbDblClick = (e) => {
    if (_lbScale > 1) {
      lbZoomReset();
    } else {
      const rect = box.getBoundingClientRect();
      lbZoom(1, e.clientX - rect.left - rect.width / 2, e.clientY - rect.top - rect.height / 2);
    }
  };
  box.addEventListener('dblclick', box._lbDblClick);
}

// Clean up zoom events when lightbox closes or navigates
function _lbDetachEvents() {
  const box = _lbBox();
  if (!box) return;
  if (box._lbWheel)       { box.removeEventListener('wheel', box._lbWheel); }
  if (box._lbPointerDown) { box.removeEventListener('mousedown', box._lbPointerDown); }
  if (box._lbPointerMove) { window.removeEventListener('mousemove', box._lbPointerMove); }
  if (box._lbPointerUp)   { window.removeEventListener('mouseup',   box._lbPointerUp); }
  if (box._lbTouchStart)  { box.removeEventListener('touchstart', box._lbTouchStart); }
  if (box._lbTouchMove)   { box.removeEventListener('touchmove',  box._lbTouchMove); }
  if (box._lbDblClick)    { box.removeEventListener('dblclick',   box._lbDblClick); }
}

function closeLightbox() {
  _lbDetachEvents();
  const vid = document.getElementById('lb-content')?.querySelector('video');
  if (vid) { vid.pause(); vid.src = ''; vid.load(); }
  document.getElementById('lightbox').classList.remove('open');
  _destroyMiniMap('lb-detail-minimap');
}

function lbNav(dir) {
  _lbDetachEvents();
  lbIndex = (lbIndex + dir + filteredMedia.length) % filteredMedia.length;
  renderLightbox();
}

document.addEventListener('keydown', e => {
  if (!document.getElementById('lightbox').classList.contains('open')) return;
  if (e.key === 'ArrowLeft')  lbNav(-1);
  if (e.key === 'ArrowRight') lbNav(1);
  if (e.key === 'Escape')     closeLightbox();
  if (e.key === '+' || e.key === '=') lbZoom(+0.25);
  if (e.key === '-')                  lbZoom(-0.25);
  if (e.key === '0')                  lbZoomReset();
  if (e.key === '1')                  lbZoom100();
});

// ─────────────────────────────────────────────
//  METADATA MODAL
// ─────────────────────────────────────────────
function openMetaModal(item) {
  const m = item.metadata || {};
  document.getElementById('meta-modal-title').textContent = item.name;

  const sections = [
    { title: 'File', rows: [
      ['Name', item.name],
      ['Format', m.file?.format],
      ['Size', m.file?.size ? (m.file.size / 1024 / 1024).toFixed(2) + ' MB' : null],
      ['Path', m.file?.path],
      ['Subfolder', m.file?.subfolder || null],
      ['Relative Path', m.file?.relative_path || null],
      ['Source Root', m.file?.source_root || null],
    ]},
    { title: 'Image', rows: [
      ['Dimensions', m.image?.resolution],
      ['Color Space', m.image?.color_space],
      ['Orientation', m.image?.orientation],
    ]},
    { title: 'Camera', rows: [
      ['Make', m.camera?.make],
      ['Model', m.camera?.model],
      ['Lens', m.camera?.lens],
      ['ISO', m.camera?.iso],
      ['Aperture', m.camera?.aperture],
      ['Shutter Speed', m.camera?.shutter_speed],
      ['Focal Length', m.camera?.focal_length],
    ]},
    { title: 'Date', rows: [
      ['Created', m.date?.created],
      ['Modified', m.date?.modified],
    ]},
    { title: 'Location', rows: [
      ['Latitude', m.location?.latitude],
      ['Longitude', m.location?.longitude],
      ['City', m.location?.city],
      ['Country', m.location?.country],
    ]},
    { title: 'Software', rows: [
      ['Editor', m.software?.editor],
      ['Version', m.software?.version],
    ]},
  ];

  let html = '';
  sections.forEach(sec => {
    const rows = sec.rows.filter(r => r[1] != null && r[1] !== '');
    if (rows.length === 0) return;
    html += `<div class="meta-section"><div class="meta-section-title">${sec.title}</div>`;
    if (sec.title === 'Location' && !isNaN(parseFloat(m.location?.latitude)) && !isNaN(parseFloat(m.location?.longitude))) {
      html += `<div class="meta-minimap" id="meta-minimap"></div>`;
    }
    rows.forEach(([k, v]) => {
      html += `<div class="meta-row"><span class="meta-key">${k}</span><span class="meta-val">${escHtml(String(v))}</span></div>`;
    });
    html += '</div>';
  });

  document.getElementById('meta-content').innerHTML = html || '<p style="color:var(--text-muted);font-size:12px">No metadata available.</p>';
  document.getElementById('meta-modal').classList.add('open');

  _destroyMiniMap('meta-minimap');
  if (m.location?.latitude) {
    _renderMiniMap('meta-minimap', parseFloat(m.location.latitude), parseFloat(m.location.longitude));
  }
}

// ─────────────────────────────────────────────
//  ALBUM MODALS
// ─────────────────────────────────────────────
let pendingAddUnique = null;   // string (single item) or string[] (bulk selection)

// uniqueNameOrNames: either a single uniqueName string (existing per-item flow)
// or an array of uniqueNames (bulk multi-select flow). A checkbox reflects
// "checked" when EVERY selected item is already in that album; for a bulk
// selection that's only partially in an album, the checkbox starts unchecked
// (mirroring how a single fresh add would work) rather than guessing intent.
function openAddToAlbum(uniqueNameOrNames) {
  pendingAddUnique = uniqueNameOrNames;
  const uniques = Array.isArray(uniqueNameOrNames) ? uniqueNameOrNames : [uniqueNameOrNames];
  const list = document.getElementById('album-list-select');
  if (db.albums.length === 0) {
    list.innerHTML = '<p style="color:var(--text-muted);font-size:12px;padding:8px 0">No albums yet. Create one first.</p>';
  } else {
    list.innerHTML = db.albums.map(a => {
      const allIn = uniques.every(un => a.media.includes(un));
      return `
      <label class="album-check-item">
        <input type="checkbox" value="${a.id}" ${allIn ? 'checked' : ''}>
        ${escHtml(a.name)} <span style="color:var(--text-muted);margin-left:auto">${a.media.length}</span>
      </label>
    `;
    }).join('');
  }
  document.getElementById('add-album-modal').classList.add('open');
}

function confirmAddToAlbum() {
  if (!pendingAddUnique) return;
  const uniques = Array.isArray(pendingAddUnique) ? pendingAddUnique : [pendingAddUnique];
  const checks = document.getElementById('album-list-select').querySelectorAll('input[type=checkbox]');
  checks.forEach(cb => {
    const album = db.albums.find(a => a.id === cb.value);
    if (!album) return;
    if (cb.checked) {
      uniques.forEach(un => { if (!album.media.includes(un)) album.media.push(un); });
    } else {
      uniques.forEach(un => { const i = album.media.indexOf(un); if (i > -1) album.media.splice(i, 1); });
    }
  });
  saveDB();
  softRefresh();
  closeModal('add-album-modal');
  toast(uniques.length > 1 ? `Updated album membership for ${uniques.length} items` : 'Album membership updated', 'success');
  if (uniques.length > 1) _clearSelectionKeepMode();
}

function openCreateAlbum() {
  document.getElementById('new-album-name').value = '';
  const sel = document.getElementById('new-album-folder');
  if (sel) {
    sel.innerHTML = ['<option value="">No folder</option>']
      .concat((db.folders || []).map(f => `<option value="${f.id}">${escHtml(f.name)}</option>`))
      .join('');
  }
  document.getElementById('create-album-modal').classList.add('open');
  setTimeout(() => document.getElementById('new-album-name').focus(), 100);
}

function confirmCreateAlbum() {
  const name = document.getElementById('new-album-name').value.trim();
  if (!name) { toast('Enter an album name', 'error'); return; }
  const folderSel  = document.getElementById('new-album-folder');
  const folder_id  = folderSel ? (folderSel.value || null) : null;
  const id = 'album_' + Date.now();
  db.albums.push({ name, id, media: [], folder_id });
  saveDB();
  renderAll();
  closeModal('create-album-modal');
  toast(`Album "${name}" created`, 'success');
}

document.getElementById('new-album-name').addEventListener('keydown', e => {
  if (e.key === 'Enter') confirmCreateAlbum();
});

function deleteCurrentAlbum() {
  if (!currentAlbumId) return;
  const album = db.albums.find(a => a.id === currentAlbumId);
  if (!album) return;
  _openDangerConfirm(
    'Delete Album',
    `Delete album <strong>"${escHtml(album.name)}"</strong>?<br><br>` +
    `Media files are not affected — only the album itself will be removed.`,
    () => {
      db.albums = db.albums.filter(a => a.id !== currentAlbumId);
      saveDB();
      currentAlbumId = null;
      setView('all', document.querySelector('[data-view="all"]'));
      renderAll();
      toast('Album deleted', 'info');
    }
  );
}

// ── Inline rename from album header ───────────────────────────────────────────
function startInlineRename() {
  if (!currentAlbumId) return;
  const album     = db.albums.find(a => a.id === currentAlbumId);
  if (!album) return;
  const nameEl    = document.getElementById('album-header-name');
  const oldName   = album.name;

  // Replace the display div content with an input
  nameEl.innerHTML = '';
  const input = document.createElement('input');
  input.className = 'album-rename-input';
  input.value     = oldName;
  nameEl.appendChild(input);
  input.focus();
  input.select();

  function commit() {
    const newName = input.value.trim();
    if (newName && newName !== oldName) {
      album.name = newName;
      saveDB();
      // Update header name and sidebar without rebuilding grid
      document.getElementById('album-header-name').textContent = newName;
      document.getElementById('topbar-title').textContent = newName;
      softRefresh();
      toast(`Renamed to "${newName}"`, 'success');
    } else {
      nameEl.textContent = oldName;
    }
  }

  input.addEventListener('blur',    commit);
  input.addEventListener('keydown', e => {
    if (e.key === 'Enter')  { input.blur(); }
    if (e.key === 'Escape') { input.value = oldName; input.blur(); }
  });
}

// ── Rename from sidebar pencil icon ───────────────────────────────────────────
let pendingRenameAlbumId = null;

function openRenameAlbum(albumId) {
  const album = db.albums.find(a => a.id === albumId);
  if (!album) return;
  pendingRenameAlbumId = albumId;
  const input = document.getElementById('rename-album-name');
  input.value = album.name;
  document.getElementById('rename-album-modal').classList.add('open');
  setTimeout(() => { input.focus(); input.select(); }, 100);
}

function confirmRenameAlbum() {
  if (!pendingRenameAlbumId) return;
  const album = db.albums.find(a => a.id === pendingRenameAlbumId);
  if (!album) return;
  const trimmed = document.getElementById('rename-album-name').value.trim();
  if (!trimmed) { toast('Name cannot be empty', 'error'); return; }
  if (trimmed !== album.name) {
    album.name = trimmed;
    saveDB();
    softRefresh();
    toast(`Renamed to "${trimmed}"`, 'success');
  }
  closeModal('rename-album-modal');
}

document.getElementById('rename-album-name')?.addEventListener('keydown', e => {
  if (e.key === 'Enter') confirmRenameAlbum();
});

// ─────────────────────────────────────────────
//  FOLDERS
// ─────────────────────────────────────────────
function openCreateFolder() {
  document.getElementById('new-folder-name').value = '';
  document.getElementById('create-folder-modal').classList.add('open');
  setTimeout(() => document.getElementById('new-folder-name').focus(), 100);
}

async function confirmCreateFolder() {
  const name = document.getElementById('new-folder-name').value.trim();
  if (!name) { toast('Enter a folder name', 'error'); return; }
  try {
    const r = await fetch(`${API_BASE}/api/folder/create`, {
      method:  'POST',
      headers: { 'Content-Type': 'application/json' },
      body:    JSON.stringify({ name })
    });
    if (!r.ok) throw new Error();
    const folder = await r.json();
    db.folders.push(folder);
    renderAlbumNav();
    closeModal('create-folder-modal');
    toast(`Folder "${name}" created`, 'success');
  } catch {
    toast('Failed to create folder', 'error');
  }
}

document.getElementById('new-folder-name')?.addEventListener('keydown', e => {
  if (e.key === 'Enter') confirmCreateFolder();
});

let pendingRenameFolderId = null;

function openRenameFolder(folderId) {
  const folder = db.folders.find(f => f.id === folderId);
  if (!folder) return;
  pendingRenameFolderId = folderId;
  const input = document.getElementById('rename-folder-name');
  input.value = folder.name;
  document.getElementById('rename-folder-modal').classList.add('open');
  setTimeout(() => { input.focus(); input.select(); }, 100);
}

async function confirmRenameFolder() {
  if (!pendingRenameFolderId) return;
  const folder = db.folders.find(f => f.id === pendingRenameFolderId);
  if (!folder) return;
  const trimmed = document.getElementById('rename-folder-name').value.trim();
  if (!trimmed) { toast('Name cannot be empty', 'error'); return; }
  if (trimmed === folder.name) { closeModal('rename-folder-modal'); return; }
  try {
    const r = await fetch(`${API_BASE}/api/folder/rename`, {
      method:  'POST',
      headers: { 'Content-Type': 'application/json' },
      body:    JSON.stringify({ folderId: pendingRenameFolderId, name: trimmed })
    });
    if (!r.ok) throw new Error();
    folder.name = trimmed;
    renderAlbumNav();
    closeModal('rename-folder-modal');
    toast(`Renamed to "${trimmed}"`, 'success');
  } catch {
    toast('Failed to rename folder', 'error');
  }
}

document.getElementById('rename-folder-name')?.addEventListener('keydown', e => {
  if (e.key === 'Enter') confirmRenameFolder();
});

// Deletes a folder. Always confirms via the danger modal first. If the
// folder still contains albums, the backend refuses the initial (unforced)
// request with 409 — we surface that as a second, more specific danger-modal
// warning naming the albums, then retry with force. Media files are never
// affected either way.
function deleteFolder(folderId, force = false) {
  const folder = db.folders.find(f => f.id === folderId);
  if (!folder) return;

  if (!force) {
    _openDangerConfirm(
      'Delete Folder',
      `Delete folder <strong>"${escHtml(folder.name)}"</strong>?<br><br>` +
      `If it contains albums, those albums will be deleted too. ` +
      `Media files themselves will NOT be affected.`,
      () => _deleteFolderRequest(folderId, false)
    );
    return;
  }
  _deleteFolderRequest(folderId, force);
}

async function _deleteFolderRequest(folderId, force) {
  const folder = db.folders.find(f => f.id === folderId);
  if (!folder) return;
  try {
    const r    = await fetch(`${API_BASE}/api/folder/delete`, {
      method:  'POST',
      headers: { 'Content-Type': 'application/json' },
      body:    JSON.stringify({ folderId, force })
    });
    const data = await r.json().catch(() => ({}));

    if (r.status === 409 && data.needs_confirmation) {
      const names  = (data.album_names || []).map(escHtml).join(', ');
      const plural = data.album_count !== 1;
      const msg = `Folder <strong>"${escHtml(folder.name)}"</strong> contains ${data.album_count} ` +
        `album${plural ? 's' : ''}${names ? ` (${names})` : ''}.<br><br>` +
        `Deleting the folder will also delete ${plural ? 'these albums' : 'this album'}. ` +
        `Media files themselves will NOT be deleted.`;
      _openDangerConfirm('Delete Folder', msg, () => _deleteFolderRequest(folderId, true));
      return;
    }

    if (!r.ok) { toast(data.error || 'Failed to delete folder', 'error'); return; }

    const removedAlbumIds = new Set(db.albums.filter(a => a.folder_id === folderId).map(a => a.id));
    db.folders = db.folders.filter(f => f.id !== folderId);
    db.albums  = db.albums.filter(a => !removedAlbumIds.has(a.id));

    if (currentAlbumId && removedAlbumIds.has(currentAlbumId)) {
      currentAlbumId = null;
      setView('all', document.querySelector('[data-view="all"]'));
    }

    renderAll();
    toast(
      data.deleted_albums
        ? `Folder deleted along with ${data.deleted_albums} album${data.deleted_albums !== 1 ? 's' : ''}`
        : 'Folder deleted',
      'info'
    );
  } catch {
    toast('Failed to delete folder', 'error');
  }
}

// ── Move an album into (or out of) a folder ───────────────────────────────
let pendingMoveAlbumId = null;

function openMoveAlbum(albumId) {
  const album = db.albums.find(a => a.id === albumId);
  if (!album) return;
  pendingMoveAlbumId = albumId;

  const sel = document.getElementById('move-album-folder-select');
  const options = ['<option value="">No folder</option>']
    .concat((db.folders || []).map(f =>
      `<option value="${f.id}" ${album.folder_id === f.id ? 'selected' : ''}>${escHtml(f.name)}</option>`
    ));
  sel.innerHTML = options.join('');
  document.getElementById('move-album-modal').classList.add('open');
}

async function confirmMoveAlbum() {
  if (!pendingMoveAlbumId) return;
  const album = db.albums.find(a => a.id === pendingMoveAlbumId);
  if (!album) return;
  const folderId = document.getElementById('move-album-folder-select').value || null;

  try {
    const r = await fetch(`${API_BASE}/api/album/move`, {
      method:  'POST',
      headers: { 'Content-Type': 'application/json' },
      body:    JSON.stringify({ albumId: pendingMoveAlbumId, folderId })
    });
    if (!r.ok) {
      const d = await r.json().catch(() => ({}));
      toast(d.error || 'Failed to move album', 'error');
      return;
    }
    album.folder_id = folderId;
    renderAlbumNav();
    closeModal('move-album-modal');
    toast(folderId ? 'Album moved to folder' : 'Album removed from folder', 'success');
  } catch {
    toast('Failed to move album', 'error');
  }
}

// ── Shared "danger" confirmation modal (delete album / delete folder) ─────
// A single reusable popup styled in the danger/red palette, replacing
// window.confirm() for destructive actions. Call _openDangerConfirm with a
// title, an HTML message, and a callback to run only if the user confirms.
let _dangerConfirmCallback = null;

function _openDangerConfirm(title, messageHtml, onConfirm) {
  document.getElementById('danger-confirm-title').textContent = title;
  document.getElementById('danger-confirm-message').innerHTML = messageHtml;
  _dangerConfirmCallback = onConfirm;
  document.getElementById('danger-confirm-modal').classList.add('open');
}

function _runDangerConfirm() {
  const cb = _dangerConfirmCallback;
  _dangerConfirmCallback = null;
  closeModal('danger-confirm-modal');
  if (cb) cb();
}

function closeModal(id) {
  document.getElementById(id).classList.remove('open');
  if (id === 'danger-confirm-modal') _dangerConfirmCallback = null;
  if (id === 'meta-modal') _destroyMiniMap('meta-minimap');
}
document.querySelectorAll('.modal-overlay').forEach(m => {
  m.addEventListener('click', e => {
    if (e.target === m) {
      m.classList.remove('open');
      if (m.id === 'danger-confirm-modal') _dangerConfirmCallback = null;
      if (m.id === 'meta-modal') _destroyMiniMap('meta-minimap');
    }
  });
});

// ─────────────────────────────────────────────
//  SYNC
// ─────────────────────────────────────────────
async function triggerSync() {
  // Check if already running
  try {
    const check = await fetch(`${API_BASE}/api/sync/status`);
    if (check.ok) {
      const st = await check.json();
      if (st.running) { openSyncOverlay(); _syncPoll(); return; }
    }
  } catch { /* backend not running */ }

  // Start sync
  let started = false;
  try {
    const r = await fetch(`${API_BASE}/api/sync`, { method: 'POST' });
    if (r.status === 409) { openSyncOverlay(); _syncPoll(); return; } // already running
    if (!r.ok) throw new Error('Backend returned ' + r.status);
    started = true;
  } catch (err) {
    toast('Sync failed — is app.py running?', 'error');
    return;
  }

  if (!started) return;
  openSyncOverlay();

  // Prefer SSE stream; fall back to polling if EventSource unavailable
  if (typeof EventSource !== 'undefined') {
    _syncStream();
  } else {
    _syncPoll();
  }
}

function openSyncOverlay() {
  document.getElementById('sync-overlay').classList.add('show');
  document.getElementById('sync-title').textContent   = 'Synchronising media library…';
  document.getElementById('sync-spinner').classList.remove('done');
  document.getElementById('sync-close-btn').classList.remove('visible');
  document.getElementById('sync-added').textContent   = '0';
  document.getElementById('sync-scanned').textContent = '0';
  document.getElementById('sync-removed').textContent = '0';
  document.getElementById('sync-source').textContent  = '';
  document.getElementById('sync-file').textContent    = '';
  document.getElementById('sync-log').innerHTML       = '';
}

function closeSyncOverlay() {
  document.getElementById('sync-overlay').classList.remove('show');
}

function _syncApplyProgress(data) {
  if (data.scanned    !== undefined) document.getElementById('sync-scanned').textContent   = data.scanned;
  if (data.added      !== undefined) document.getElementById('sync-added').textContent     = data.added;
  if (data.current_source)           document.getElementById('sync-source').textContent    = data.current_source;
  if (data.current_file)             document.getElementById('sync-file').textContent      = '↳ ' + data.current_file;
}

function _syncAppendLog(line) {
  const el  = document.getElementById('sync-log');
  const div = document.createElement('div');
  div.textContent = line;
  el.appendChild(div);
  el.scrollTop = el.scrollHeight;
}

async function _syncOnComplete(data) {
  document.getElementById('sync-spinner').classList.add('done');
  if (data.error) {
    document.getElementById('sync-title').textContent = '✗ Sync failed';
    _syncAppendLog('Error: ' + data.error);
    toast('Sync failed: ' + data.error, 'error');
  } else {
    const added   = data.result?.added   ?? data.added   ?? 0;
    const removed = data.result?.removed ?? 0;
    const total   = data.result?.total   ?? 0;
    document.getElementById('sync-removed').textContent = removed;
    document.getElementById('sync-title').textContent = `✓ Sync complete — ${added} new`;
    toast(`Sync complete — ${added} new, ${removed} removed, ${total} total`, 'success');
  }
  document.getElementById('sync-close-btn').classList.add('visible');
  // Reload gallery with fresh data
  await loadDB();
  renderAll();
  populateAllFilters();
}

// ── SSE stream (preferred) ────────────────────────────────────────────────────
function _syncStream() {
  const es = new EventSource(`${API_BASE}/api/sync/stream`);

  es.addEventListener('progress', e => {
    try { _syncApplyProgress(JSON.parse(e.data)); } catch {}
  });
  es.addEventListener('log', e => {
    try { _syncAppendLog(JSON.parse(e.data)); } catch {}
  });
  es.addEventListener('complete', e => {
    es.close();
    try { _syncOnComplete(JSON.parse(e.data)); } catch { _syncOnComplete({}); }
  });
  es.addEventListener('heartbeat', () => { /* keep-alive, nothing to do */ });
  es.onerror = () => {
    es.close();
    // SSE connection dropped mid-sync — fall back to polling
    _syncPoll();
  };
}

// ── Polling fallback (when SSE unavailable or connection drops) ──────────────
let _syncPollTimer = null;
function _syncPoll() {
  if (_syncPollTimer) return;
  _syncPollTimer = setInterval(async () => {
    try {
      const r    = await fetch(`${API_BASE}/api/sync/status`);
      const data = await r.json();
      _syncApplyProgress(data);
      // Append any new log lines
      (data.log || []).forEach(line => {
        const logEl = document.getElementById('sync-log');
        if (!logEl.querySelector(`[data-line="${CSS.escape(line)}"]`)) {
          const d = document.createElement('div');
          d.textContent      = line;
          d.dataset.line     = line;
          logEl.appendChild(d);
          logEl.scrollTop    = logEl.scrollHeight;
        }
      });
      if (data.done) {
        clearInterval(_syncPollTimer);
        _syncPollTimer = null;
        _syncOnComplete(data);
      }
    } catch {
      clearInterval(_syncPollTimer);
      _syncPollTimer = null;
      toast('Lost connection to backend during sync', 'error');
      document.getElementById('sync-close-btn').classList.add('visible');
    }
  }, 1500);
}

// ─────────────────────────────────────────────
//  PERSIST (saves to localStorage as fallback)
// ─────────────────────────────────────────────
function saveDB() {
  // Only save albums via POST /api/db — albums are always fully loaded in memory.
  // Media mutations (hide/unhide) use /api/media/hide directly.
  // Never send db.media here — it's a partial page and would overwrite the full DB.
  fetch(`${API_BASE}/api/db`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ albums: db.albums })
  }).catch(() => {});
  updateFooter();
  renderAlbumNav();
  updateStats();
}

// ─────────────────────────────────────────────
//  TOAST
// ─────────────────────────────────────────────
function toast(msg, type = 'info') {
  const icons = { success: '✓', error: '✕', info: '◆' };
  const el = document.createElement('div');
  el.className = `toast ${type}`;
  el.innerHTML = `<span>${icons[type] || '◆'}</span> ${escHtml(msg)}`;
  document.getElementById('toast-container').appendChild(el);
  setTimeout(() => el.remove(), 3500);
}

// ─────────────────────────────────────────────
//  SETTINGS PANEL
// ─────────────────────────────────────────────
let _settingsCurrent = {};   // live copy while panel is open

const ALL_IMAGE_FORMATS = ['jpg','jpeg','png','heic','heif','webp','tiff','bmp','gif'];
const ALL_VIDEO_FORMATS = ['mp4','mov','avi','mkv','webm','m4v','3gp','wmv','flv','ts','mts'];

function settingsToggle(el) {
  el.classList.toggle('on');
}

async function openSettings() {
  try {
    const r = await fetch(`${API_BASE}/api/config`);
    _settingsCurrent = r.ok ? await r.json() : {};
  } catch { _settingsCurrent = {}; }

  _settingsPopulate(_settingsCurrent);
  document.getElementById('settings-status').textContent = '';
  document.getElementById('settings-overlay').classList.add('open');
  _refreshCacheSize();
}

async function _refreshCacheSize() {
  const el = document.getElementById('s-cache-size');
  el.textContent = 'Calculating…';
  try {
    const r = await fetch(`${API_BASE}/api/cache/size`);
    if (!r.ok) throw new Error('Server returned ' + r.status);
    const data = await r.json();
    el.textContent = `${data.total_mb} MB used`;
  } catch {
    el.textContent = '—';
  }
}

async function clearCache() {
  if (!confirm('Delete all cached thumbnails and temporary files?\n\nThis will not affect your original photos/videos — thumbnails are regenerated automatically the next time they\'re needed.')) return;

  const btn = document.getElementById('s-clear-cache-btn');
  btn.disabled = true;
  btn.textContent = 'Clearing…';
  try {
    const r = await fetch(`${API_BASE}/api/cache/clear`, { method: 'POST' });
    if (!r.ok) throw new Error('Server returned ' + r.status);
    const data = await r.json();
    toast(`Cleared ${data.freed_mb} MB`, 'success');
    _refreshCacheSize();
  } catch {
    toast('Could not clear cache — is app.py running?', 'error');
  } finally {
    btn.disabled = false;
    btn.textContent = 'Clear Cache';
  }
}

function closeSettings() {
  document.getElementById('settings-overlay').classList.remove('open');
}

function _settingsPopulate(c) {
  // Helpers
  const sel  = (id, val) => { const el = document.getElementById(id); if (el) el.value = val ?? el.options[0]?.value; };
  const tog  = (id, val) => { const el = document.getElementById(id); if (el) el.classList.toggle('on', !!val); };
  const num  = (id, val) => { const el = document.getElementById(id); if (el) el.value = val ?? ''; };
  const txt  = (id, val) => { const el = document.getElementById(id); if (el) el.value = val ?? ''; };

  // Appearance
  sel('s-theme',           c.theme            ?? 'dark');
  sel('s-style',           c.style            ?? 'classic');
  sel('s-font-size',       c.font_size        ?? 'small');
  sel('s-grid-columns',    String(c.grid_columns ?? 4));
  sel('s-card-size',       c.card_size         ?? 'medium');
  tog('s-show-filename',   c.show_filename_on_card  ?? true);
  tog('s-show-date',       c.show_date_on_card      ?? true);
  tog('s-show-subfolder',  c.show_subfolder_on_card ?? true);
  // Sorting
  sel('s-default-sort',        c.default_sort       ?? 'date-desc');
  sel('s-default-date-field',  c.default_date_field ?? 'modified');
  tog('s-show-hidden-default', c.show_hidden_default ?? false);
  // Performance
  num('s-lazy-load-batch',      c.lazy_load_batch      ?? 50);
  num('s-media-page-size',      c.media_page_size      ?? 500);
  num('s-thumbnail-size',       c.thumbnail_size       ?? 400);
  num('s-thumbnail-quality',    c.thumbnail_quality    ?? 60);
  txt('s-thumbnail-cache-path', c.thumbnail_cache_path ?? 'thumb');
  // Media types — chips
  _buildFormatChips('s-image-formats', ALL_IMAGE_FORMATS, c.supported_image_formats ?? ALL_IMAGE_FORMATS);
  _buildFormatChips('s-video-formats', ALL_VIDEO_FORMATS, c.supported_video_formats ?? ALL_VIDEO_FORMATS);
  sel('s-video-preload',   c.video_preload   ?? 'metadata');
  tog('s-video-autoplay',  c.video_autoplay  ?? false);
  // Sync
  tog('s-follow-symlinks',  c.follow_symlinks  ?? true);
  tog('s-skip-hidden-dirs', c.skip_hidden_dirs ?? true);
  num('s-max-scan-depth',   c.max_scan_depth   ?? 0);
  sel('s-dedup-method',     c.dedup_method     ?? 'both');
  // Metadata
  tog('s-show-gps',           c.show_gps_in_metadata   ?? true);
  tog('s-extract-video-meta', c.extract_video_metadata ?? true);
  // Server
  num('s-api-port',      c.api_port         ?? 5000);
  sel('s-log-level',     c.log_level        ?? 'INFO');
  num('s-log-retention', c.log_retention_days ?? 30);
}

function _buildFormatChips(containerId, allFormats, activeFormats) {
  const active = new Set((activeFormats || []).map(f => f.toLowerCase()));
  const wrap   = document.getElementById(containerId);
  wrap.innerHTML = '';
  allFormats.forEach(fmt => {
    const chip = document.createElement('span');
    chip.className = 'settings-chip' + (active.has(fmt) ? ' active' : '');
    chip.textContent = fmt;
    chip.onclick = () => chip.classList.toggle('active');
    wrap.appendChild(chip);
  });
}

function _getToggle(id)   { return document.getElementById(id)?.classList.contains('on') ?? false; }
function _getSelect(id)   { return document.getElementById(id)?.value ?? ''; }
function _getNumber(id)   { return parseInt(document.getElementById(id)?.value ?? '0', 10); }
function _getText(id)     { return document.getElementById(id)?.value?.trim() ?? ''; }
function _getChips(id)    { return [...document.querySelectorAll(`#${id} .settings-chip.active`)].map(c => c.textContent); }

async function saveSettings() {
  const payload = {
    // Appearance
    theme:                    _getSelect('s-theme'),
    style:                    _getSelect('s-style'),
    font_size:                _getSelect('s-font-size'),
    grid_columns:             _getSelect('s-grid-columns') === 'auto' ? 'auto' : parseInt(_getSelect('s-grid-columns')),
    card_size:                _getSelect('s-card-size'),
    show_filename_on_card:    _getToggle('s-show-filename'),
    show_date_on_card:        _getToggle('s-show-date'),
    show_subfolder_on_card:   _getToggle('s-show-subfolder'),
    // Sorting
    default_sort:             _getSelect('s-default-sort'),
    default_date_field:       _getSelect('s-default-date-field'),
    show_hidden_default:      _getToggle('s-show-hidden-default'),
    // Performance
    lazy_load_batch:          _getNumber('s-lazy-load-batch'),
    media_page_size:          _getNumber('s-media-page-size'),
    thumbnail_size:           _getNumber('s-thumbnail-size'),
    thumbnail_quality:        _getNumber('s-thumbnail-quality'),
    thumbnail_cache_path:     _getText('s-thumbnail-cache-path'),
    // Media types
    supported_image_formats:  _getChips('s-image-formats'),
    supported_video_formats:  _getChips('s-video-formats'),
    video_autoplay:           _getToggle('s-video-autoplay'),
    video_preload:            _getSelect('s-video-preload'),
    // Sync
    follow_symlinks:          _getToggle('s-follow-symlinks'),
    skip_hidden_dirs:         _getToggle('s-skip-hidden-dirs'),
    max_scan_depth:           _getNumber('s-max-scan-depth'),
    dedup_method:             _getSelect('s-dedup-method'),
    // Metadata
    show_gps_in_metadata:     _getToggle('s-show-gps'),
    extract_video_metadata:   _getToggle('s-extract-video-meta'),
    // Server
    api_port:                 _getNumber('s-api-port'),
    log_level:                _getSelect('s-log-level'),
    log_retention_days:       _getNumber('s-log-retention'),
  };

  const status = document.getElementById('settings-status');
  try {
    const r = await fetch(`${API_BASE}/api/config`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
    if (!r.ok) throw new Error('Server returned ' + r.status);
    // Apply settings that take effect immediately (no restart needed)
    _applyImmediateSettings(payload);
    config = { ...config, ...payload };
    closeSettings();
    toast('Settings saved', 'success');
  } catch {
    status.textContent = '⚠ Could not save — is app.py running?';
    status.style.color = 'var(--danger)';
  }
}

function _applyImmediateSettings(s) {
  // Grid columns
  const grid = document.getElementById('gallery-grid');
  if (s.grid_columns === 'auto') {
    grid.style.gridTemplateColumns = 'repeat(auto-fill, minmax(180px, 1fr))';
  } else {
    grid.style.gridTemplateColumns = `repeat(${s.grid_columns}, 1fr)`;
  }

  // Card size → row height
  const heights = { small: '160px', medium: '220px', large: '300px' };
  grid.style.gridAutoRows = heights[s.card_size] || '220px';

  // Show/hide card overlay elements — toggle CSS class on body
  document.body.classList.toggle('hide-card-filename',  !s.show_filename_on_card);
  document.body.classList.toggle('hide-card-date',      !s.show_date_on_card);
  document.body.classList.toggle('hide-card-subfolder', !s.show_subfolder_on_card);

  // Theme
  _applyTheme(s.theme, s.style, s.font_size);

  // Sort chips — sync active chip to new default if not already changed
  if (s.default_sort !== currentSort) {
    const chip = document.querySelector(`.filter-chip[data-sort="${s.default_sort}"]`);
    if (chip) setSort(s.default_sort, chip);
  }

  // Show hidden
  if (s.show_hidden_default !== showHidden) {
    showHidden = s.show_hidden_default;
    const tog = document.getElementById('show-hidden-toggle');
    tog.classList.toggle('on', showHidden);
    applyFilters();
  }
}

const KNOWN_STYLES = ['classic', 'modern', 'terminal', 'sunset', 'nordic'];
const KNOWN_FONT_SIZES = ['small', 'medium', 'large'];

function _applyTheme(theme, style, fontSize) {
  const root = document.documentElement;
  const isDark = theme === 'dark' || (theme === 'system' && window.matchMedia('(prefers-color-scheme: dark)').matches);
  root.setAttribute('data-mode', isDark ? 'dark' : 'light');
  root.setAttribute('data-style', KNOWN_STYLES.includes(style) ? style : 'classic');
  root.setAttribute('data-font-size', KNOWN_FONT_SIZES.includes(fontSize) ? fontSize : 'small');
  _updateThemeModeToggleIcon(isDark);
}

function _updateThemeModeToggleIcon(isDark) {
  const btn = document.getElementById('theme-mode-toggle-btn');
  if (!btn) return;
  // Icon shows the mode a click will switch TO.
  btn.textContent = isDark ? '☀️' : '🌙';
  btn.title = isDark ? 'Switch to light theme' : 'Switch to dark theme';
}

// Quick dark/light toggle for the current style — no settings panel, updates
// configuration.json directly via a partial PATCH-style save.
async function toggleThemeMode() {
  const btn = document.getElementById('theme-mode-toggle-btn');
  const currentlyDark = document.documentElement.getAttribute('data-mode') === 'dark';
  const newTheme = currentlyDark ? 'light' : 'dark';
  const previousTheme = config.theme;

  // Apply immediately for a snappy UI, before the save round-trip completes.
  _applyTheme(newTheme, config.style, config.font_size);
  if (btn) btn.disabled = true;

  try {
    const r = await fetch(`${API_BASE}/api/config`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ theme: newTheme }),
    });
    if (!r.ok) throw new Error('Server returned ' + r.status);
    config.theme = newTheme;
  } catch {
    // Revert on failure so the UI doesn't silently disagree with configuration.json
    _applyTheme(previousTheme, config.style, config.font_size);
    toast('Could not save theme — is app.py running?', 'error');
  } finally {
    if (btn) btn.disabled = false;
  }
}

// Apply settings on boot from loaded config
function applyConfigToUI(cfg) {
  if (!cfg) return;
  _applyImmediateSettings(cfg);
}

// ─────────────────────────────────────────────
//  LOCATIONS MANAGER
// ─────────────────────────────────────────────
let locationsData = [];   // working copy while modal is open

async function openLocationsManager() {
  try {
    const r = await fetch(`${API_BASE}/api/locations`);
    if (!r.ok) throw new Error('Backend returned ' + r.status);
    locationsData = await r.json();
  } catch {
    toast('Could not load media.json — is app.py running?', 'error');
    return;
  }
  renderLocationsList();
  document.getElementById('loc-new-name').value = '';
  document.getElementById('loc-new-path').value = '';
  document.getElementById('loc-new-vis').checked = true;
  document.getElementById('locations-modal').classList.add('open');
}

function renderLocationsList() {
  const list = document.getElementById('locations-list');
  list.innerHTML = '';

  if (locationsData.length === 0) {
    list.innerHTML = '';   // CSS :empty will show placeholder
    return;
  }

  locationsData.forEach((loc, idx) => {
    const row = document.createElement('div');
    row.className = 'loc-row' + (loc.visibility === false ? ' loc-hidden-row' : '');
    row.dataset.idx = idx;

    row.innerHTML = `
      <div class="loc-fields">
        <input class="loc-name-input" type="text"
               value="${escHtml(loc.name || '')}"
               placeholder="Label"
               oninput="locationsData[${idx}].name = this.value">
        <input class="loc-path-input" type="text"
               value="${escHtml(loc.path || '')}"
               placeholder="Absolute path"
               oninput="locationsData[${idx}].path = this.value">
        <label class="loc-vis-toggle">
          <input type="checkbox" ${loc.visibility !== false ? 'checked' : ''}
                 onchange="locationsData[${idx}].visibility = this.checked; this.closest('.loc-row').classList.toggle('loc-hidden-row', !this.checked)">
          Scan during Sync
        </label>
        ${(loc.synced_count || 0) > 0
          ? `<span class="loc-synced-badge">⬡ <strong>${loc.synced_count}</strong> synced file${loc.synced_count !== 1 ? 's' : ''}</span>`
          : ''}
      </div>
      <div class="loc-actions">
        <button class="loc-delete-btn" onclick="deleteLocation(${idx})">✕ Remove</button>
      </div>`;

    list.appendChild(row);
  });
}

function deleteLocation(idx) {
  const loc = locationsData[idx];
  if (!loc) return;

  const count = loc.synced_count || 0;

  if (count === 0) {
    // Nothing indexed yet for this location — safe to just drop it from
    // the working list. Still needs "Save Changes" to persist.
    _openDangerConfirm(
      'Remove Location',
      `Remove <strong>"${escHtml(loc.name || loc.path)}"</strong> from the scan list?<br><br>` +
      `Nothing has been indexed from it yet, so no data will be discarded — your files are not affected.`,
      () => {
        locationsData.splice(idx, 1);
        renderLocationsList();
      }
    );
    return;
  }

  const plural = count !== 1;
  _openDangerConfirm(
    'Remove Location',
    `<strong>"${escHtml(loc.name || loc.path)}"</strong> contains <strong>${count}</strong> indexed file${plural ? 's' : ''}.<br><br>` +
    `If you remove this location, ${plural ? 'these files' : 'this file'} will be removed from Luminary's database. ` +
    `They will no longer appear in your library or be included in future syncs.<br><br>` +
    `Your original files on your computer will <strong>not</strong> be deleted. ` +
    `Only Luminary's record of them will be removed.<br><br>` +
    `To track ${plural ? 'them' : 'it'} again later, you'll need to add this location back and run a sync.<br><br>` +
    `Do you want to continue?`,
    () => _deleteLocationRequest(idx)
  );
}

async function _deleteLocationRequest(idx) {
  const loc = locationsData[idx];
  if (!loc) return;
  try {
    const r = await fetch(`${API_BASE}/api/location/delete`, {
      method:  'POST',
      headers: { 'Content-Type': 'application/json' },
      body:    JSON.stringify({ path: loc.path }),
    });
    const data = await r.json().catch(() => ({}));
    if (!r.ok) { toast(data.error || 'Failed to remove location', 'error'); return; }

    locationsData.splice(idx, 1);
    renderLocationsList();
    populateLocationFilter();
    toast(
      data.deleted
        ? `Location removed — discarded ${data.deleted} indexed file${data.deleted !== 1 ? 's' : ''}`
        : 'Location removed',
      'info'
    );
  } catch {
    toast('Could not remove location — is app.py running?', 'error');
  }
}

function addLocation() {
  const name = document.getElementById('loc-new-name').value.trim();
  const path = document.getElementById('loc-new-path').value.trim();
  const vis  = document.getElementById('loc-new-vis').checked;

  if (!path) { toast('Path is required', 'error'); return; }

  locationsData.push({ name: name || path, path, visibility: vis });
  renderLocationsList();

  document.getElementById('loc-new-name').value = '';
  document.getElementById('loc-new-path').value = '';
  document.getElementById('loc-new-vis').checked = true;
  toast('Location added — click Save Changes to write to media.json', 'info');
}

async function saveLocations() {
  // Validate — each entry must have a path
  const invalid = locationsData.filter(l => !l.path || !l.path.trim());
  if (invalid.length) {
    toast('All locations must have a path', 'error');
    return;
  }

  try {
    const r = await fetch(`${API_BASE}/api/locations`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(locationsData),
    });
    if (!r.ok) throw new Error('Backend returned ' + r.status);
    closeModal('locations-modal');
    populateLocationFilter();  // refresh dropdown with updated labels
    toast('media.json saved — run Sync to index changes', 'success');
  } catch {
    toast('Could not save — is app.py running?', 'error');
  }
}

// ─────────────────────────────────────────────
//  FOLDER BROWSER  (Browse… next to Absolute path)
// ─────────────────────────────────────────────
let folderBrowserPath = null;   // currently listed directory (null = start screen, e.g. Windows drive list)

function openFolderBrowser() {
  // Start from whatever's already typed in the path field, if it looks
  // like something worth listing; otherwise let the backend pick a default
  // (home directory, or the drive list on Windows).
  const typed = document.getElementById('loc-new-path').value.trim();
  document.getElementById('folder-browser-modal').classList.add('open');
  loadFolderBrowser(typed || null);
}

async function loadFolderBrowser(path) {
  const listEl = document.getElementById('folder-browser-list');
  const pathEl = document.getElementById('folder-browser-path');
  listEl.innerHTML = '<div class="folder-browser-msg">Loading…</div>';

  try {
    const url = path ? `${API_BASE}/api/browse?path=${encodeURIComponent(path)}`
                      : `${API_BASE}/api/browse`;
    const r = await fetch(url);
    const data = await r.json();

    if (!r.ok) {
      listEl.innerHTML = `<div class="folder-browser-msg">${escHtml(data.error || 'Could not list that folder')}</div>`;
      return;
    }

    folderBrowserPath = data.path || null;
    pathEl.textContent = data.path || 'Select a starting point';

    listEl.innerHTML = '';

    if (data.parent !== null && data.parent !== undefined) {
      const up = document.createElement('div');
      up.className = 'folder-browser-item folder-browser-up';
      up.textContent = '⬆ .. (up a level)';
      up.onclick = () => loadFolderBrowser(data.parent);
      listEl.appendChild(up);
    }

    if (data.dirs.length === 0) {
      const empty = document.createElement('div');
      empty.className = 'folder-browser-msg';
      empty.textContent = 'No subfolders here';
      listEl.appendChild(empty);
    } else {
      data.dirs.forEach(d => {
        const item = document.createElement('div');
        item.className = 'folder-browser-item';
        item.textContent = '📁 ' + d.name;
        item.onclick = () => loadFolderBrowser(d.path);
        listEl.appendChild(item);
      });
    }
  } catch {
    listEl.innerHTML = '<div class="folder-browser-msg">Could not reach the backend — is app.py running?</div>';
  }
}

function confirmFolderBrowser() {
  if (!folderBrowserPath) {
    toast('Choose a folder first', 'error');
    return;
  }
  document.getElementById('loc-new-path').value = folderBrowserPath;
  closeModal('folder-browser-modal');
}

// ─────────────────────────────────────────────
//  PHOTO PICKER
// ─────────────────────────────────────────────
let pickerSelected = new Set();
let pickerAllMedia = [];          // current page of results shown in grid
let pickerTotal    = 0;           // total matching on server
let pickerOffset   = 0;           // current offset into server results
let pickerHasMore  = false;
let pickerFetching = false;

async function openPhotoPicker() {
  if (!currentAlbumId) return;
  const album = db.albums.find(a => a.id === currentAlbumId);
  if (!album) return;

  pickerSelected = new Set();
  pickerAllMedia = [];
  pickerOffset   = 0;
  pickerHasMore  = false;
  pickerFetching = false;

  document.getElementById('photo-picker-title').textContent =
    `Add Photos to "${album.name}"`;
  document.getElementById('photo-picker-search').value   = '';
  document.getElementById('photo-picker-location').value = '';
  document.getElementById('picker-confirm-btn').disabled = true;
  document.getElementById('picker-confirm-btn').textContent = 'Add Selected';
  document.getElementById('photo-picker-sel-count').classList.remove('visible');
  const addAllBtn = document.getElementById('picker-add-all-btn');
  if (addAllBtn) {
    addAllBtn.style.display = 'none';
    addAllBtn.disabled      = false;
    addAllBtn.textContent   = '⊕ Add All';
  }

  await populatePickerLocationFilter();

  document.getElementById('photo-picker').classList.add('open');

  await _pickerFetch(true);   // initial load
}

// Fetch a page of media for the picker, applying current picker filters.
// replace=true clears the grid first (used on open / filter change).
async function _pickerFetch(replace = false) {
  if (pickerFetching) return;
  pickerFetching = true;

  const album  = db.albums.find(a => a.id === currentAlbumId);
  const q      = document.getElementById('photo-picker-search').value.trim();
  const loc    = document.getElementById('photo-picker-location').value;
  const params = new URLSearchParams({
    offset: replace ? 0 : pickerOffset,
    limit:  200,
    sort:   'date-desc',
  });
  if (q)   params.set('q', q);
  if (loc) params.set('location', loc);

  try {
    const r    = await fetch(`${API_BASE}/api/media?${params.toString()}`);
    const data = await r.json();
    const items = data.items || [];

    if (replace) {
      pickerAllMedia = items;
      pickerOffset   = items.length;
    } else {
      pickerAllMedia.push(...items);
      pickerOffset += items.length;
    }
    pickerTotal   = data.total;
    pickerHasMore = data.has_more;

    renderPickerGrid(replace ? pickerAllMedia : items, album, !replace);
  } catch { /* network error */ }

  pickerFetching = false;
}

function closePhotoPicker() {
  document.getElementById('photo-picker').classList.remove('open');
}

function renderPickerGrid(mediaList, album, append = false) {
  const grid = document.getElementById('photo-picker-grid');
  if (!append) grid.innerHTML = '';

  if (!append && mediaList.length === 0) {
    grid.innerHTML = '<div style="grid-column:1/-1;text-align:center;color:var(--text-muted);padding:60px;font-size:12px">No photos found</div>';
    return;
  }

  const alreadyIn = new Set(album ? album.media : []);

  mediaList.forEach(item => {
    const inAlbum    = alreadyIn.has(item.uniqueName);
    const isSelected = pickerSelected.has(item.uniqueName);

    const div = document.createElement('div');
    div.className = 'picker-item' +
      (isSelected ? ' selected'   : '') +
      (inAlbum    ? ' already-in' : '');
    div.dataset.unique = item.uniqueName;

    div.innerHTML = `
      <img src="${thumbUrl(item.uniqueName)}" loading="lazy" alt="${escHtml(item.name)}"
           onerror="this.style.opacity='0'">
      <div class="picker-check">✓</div>
      <div class="picker-item-name">${escHtml(item.name)}</div>
      ${inAlbum ? '<span class="picker-already-badge">Added</span>' : ''}`;

    if (!inAlbum) {
      div.addEventListener('click', () => togglePickerItem(item.uniqueName, div));
    }

    grid.appendChild(div);
  });

  // If more pages exist, add a load-more sentinel at the bottom
  _pickerAttachSentinel();
}

let _pickerObserver = null;

function _pickerAttachSentinel() {
  const grid = document.getElementById('photo-picker-grid');
  // Remove existing sentinel
  grid.querySelector('.picker-sentinel')?.remove();
  if (!pickerHasMore) return;

  const sentinel = document.createElement('div');
  sentinel.className = 'picker-sentinel';
  sentinel.style.cssText = 'grid-column:1/-1;height:40px';
  grid.appendChild(sentinel);

  if (_pickerObserver) _pickerObserver.disconnect();
  _pickerObserver = new IntersectionObserver(entries => {
    if (entries[0].isIntersecting) _pickerFetch(false);
  }, { root: grid, rootMargin: '100px' });
  _pickerObserver.observe(sentinel);
}

async function filterPickerGrid() {
  // Re-fetch from offset 0 with new filters
  pickerAllMedia = [];
  pickerOffset   = 0;
  pickerHasMore  = false;
  if (_pickerObserver) { _pickerObserver.disconnect(); _pickerObserver = null; }
  await _pickerFetch(true);
}

function togglePickerItem(uniqueName, el) {
  if (pickerSelected.has(uniqueName)) {
    pickerSelected.delete(uniqueName);
    el.classList.remove('selected');
  } else {
    pickerSelected.add(uniqueName);
    el.classList.add('selected');
  }
  updatePickerCount();
}

function updatePickerCount() {
  const n     = pickerSelected.size;
  const badge = document.getElementById('photo-picker-sel-count');
  const btn   = document.getElementById('picker-confirm-btn');
  if (n > 0) {
    badge.textContent = `${n} selected`;
    badge.classList.add('visible');
    btn.disabled = false;
    btn.textContent = `Add ${n} Photo${n !== 1 ? 's' : ''}`;
  } else {
    badge.classList.remove('visible');
    btn.disabled = true;
    btn.textContent = 'Add Selected';
  }
}


function pickerSelectAll() {
  // Select all currently rendered non-already-in items
  document.querySelectorAll('.picker-item:not(.already-in)').forEach(el => {
    const un = el.dataset.unique;
    if (un) { pickerSelected.add(un); el.classList.add('selected'); }
  });
  updatePickerCount();
}

function pickerClearAll() {
  pickerSelected.clear();
  document.querySelectorAll('.picker-item.selected').forEach(el =>
    el.classList.remove('selected'));
  updatePickerCount();
}

function confirmPhotoPicker() {
  if (pickerSelected.size === 0 || !currentAlbumId) return;
  const album = db.albums.find(a => a.id === currentAlbumId);
  if (!album) return;

  let added = 0;
  pickerSelected.forEach(un => {
    if (!album.media.includes(un)) { album.media.push(un); added++; }
  });

  saveDB();
  closePhotoPicker();
  applyFilters();
  toast(`Added ${added} photo${added !== 1 ? 's' : ''} to "${album.name}"`, 'success');
}

// Close picker on backdrop click
document.getElementById('photo-picker').addEventListener('click', function(e) {
  if (e.target === this) closePhotoPicker();
});

// ─────────────────────────────────────────────
//  MAP VIEW
// ─────────────────────────────────────────────
let _leafletMap     = null;
let _clusterGroup   = null;
let _mapLoaded      = false;

async function openMapView() {
  if (!_leafletMap) {
    _leafletMap = L.map('map-container', {
      center:      [20, 0],
      zoom:        3,
      zoomControl: true,
    });
    L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
      attribution: '© <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>',
      maxZoom: 19,
    }).addTo(_leafletMap);

    // Close panel when clicking the map background
    _leafletMap.on('click', () => closeMapClusterPanel());
  }

  setTimeout(() => _leafletMap.invalidateSize(), 50);

  if (_mapLoaded) return;
  _mapLoaded = true;

  try {
    const r    = await fetch(`${API_BASE}/api/media/gps`);
    const data = await r.json();
    const total = data.total ?? data.total_count ?? 0;
    _renderMapMarkers(data.items || [], data.gps_count ?? 0, total);
  } catch {
    document.getElementById('map-gps-banner').classList.add('visible');
    document.getElementById('map-gps-text').textContent = 'Could not load GPS data — is app.py running?';
  }
}

function _renderMapMarkers(items, gpsCount, total) {
  // GPS coverage banner
  const banner  = document.getElementById('map-gps-banner');
  const bannerT = document.getElementById('map-gps-text');
  if (items.length === 0) {
    bannerT.textContent = 'No photos with GPS coordinates found.';
    banner.classList.add('visible');
    return;
  }
  if (gpsCount < total) {
    bannerT.textContent =
      `⊙  ${gpsCount.toLocaleString()} of ${total.toLocaleString()} photos have GPS coordinates`;
    banner.classList.add('visible');
  } else {
    banner.classList.remove('visible');
  }

  if (_clusterGroup) _leafletMap.removeLayer(_clusterGroup);

  _clusterGroup = L.markerClusterGroup({
    maxClusterRadius:        80,
    disableClusteringAtZoom: 50,
    spiderfyOnMaxZoom:       false,
    showCoverageOnHover:     false,
    zoomToBoundsOnClick:     false,  // we handle click ourselves

    iconCreateFunction(cluster) {
      // Single most-recent thumbnail + compact count badge
      const children = cluster.getAllChildMarkers();
      const newest   = children.reduce((best, m) =>
        (!best || (m.options._date || '') > (best.options._date || '')) ? m : best, null);
      const thumbUrl = newest?.options._thumbUrl || '';
      const count    = cluster.getChildCount();
      const badge    = count > 9999 ? '9999+' : String(count);

      const html = `
        <div class="map-cluster-wrap">
          <div class="map-cluster-thumb">
            ${thumbUrl
              ? `<img src="${thumbUrl}" loading="lazy" alt="">`
              : `<div style="width:100%;height:100%;background:var(--surface2)"></div>`}
          </div>
          <div class="map-cluster-badge">${badge}</div>
        </div>`;

      return L.divIcon({
        html,
        className: '',
        iconSize:  [52, 52],
        iconAnchor:[26, 26],
      });
    },
  });

  // Cluster click → open side panel, sorted newest first
  _clusterGroup.on('clusterclick', e => {
    e.originalEvent?.stopPropagation();
    const markers     = e.layer.getAllChildMarkers();
    markers.sort((a, b) => (b.options._date || '').localeCompare(a.options._date || ''));
    const clusterItems = markers.map(m => m.options._item);
    const title = markers.length === 1 ? clusterItems[0].name : `${markers.length} Photos`;
    openMapClusterPanel(clusterItems, title);
  });

  // Build individual markers — small thumbnail, single item
  items.forEach(item => {
    // Use a small thumbnail (80px) to keep network load minimal
    const thumbUrl = `${API_BASE}/api/thumb/${encodeURIComponent(item.uniqueName)}?size=80&quality=65`;

    const icon = L.divIcon({
      html:      `<div class="map-thumb-marker"><img src="${thumbUrl}" loading="lazy" alt=""></div>`,
      className: '',
      iconSize:  [44, 44],
      iconAnchor:[22, 22],
    });

    const marker = L.marker([item.lat, item.lng], {
      icon,
      title:     item.name,
      _thumbUrl: thumbUrl,
      _date:     item.date || '',
      _item:     item,
    });

    // Single marker click → fetch full record then open lightbox directly
    marker.on('click', e => {
      e.originalEvent?.stopPropagation();
      closeMapClusterPanel();
      _openMapItem(item, 0, [item]);
    });

    _clusterGroup.addLayer(marker);
  });

  _leafletMap.addLayer(_clusterGroup);

  // Fit to all markers on first load
  const bounds = L.latLngBounds(items.map(i => [i.lat, i.lng]));
  _leafletMap.fitBounds(bounds, { padding: [40, 40], maxZoom: 14 });
}

// ── Cluster side panel ────────────────────────────────────────────────────────
let _panelItems    = [];
let _panelRendered = 0;
let _panelObserver = null;   // lazy IMAGE loader
let _panelSentinel = null;   // scroll sentinel for incremental card rendering

const PANEL_BATCH = 24;      // cards per scroll batch (3-column × 8 rows)

function openMapClusterPanel(items, title) {
  const panel = document.getElementById('map-cluster-panel');
  const grid  = document.getElementById('map-cluster-panel-grid');
  document.getElementById('map-cluster-panel-title').textContent =
    `${title}${items.length > 1 ? ' · ' + items.length + ' photos' : ''}`;

  // Disconnect previous observers
  if (_panelObserver) { _panelObserver.disconnect(); _panelObserver = null; }
  grid.innerHTML = '';

  // Sort newest first
  _panelItems = items.slice().sort((a, b) =>
    (b.date || '').localeCompare(a.date || ''));
  _panelRendered = 0;

  // IntersectionObserver for lazy IMAGE loading — fires when each card enters the grid viewport
  _panelObserver = new IntersectionObserver((entries) => {
    entries.forEach(entry => {
      if (!entry.isIntersecting) return;
      const img = entry.target.querySelector('img[data-src]');
      if (img) {
        img.src = img.dataset.src;
        img.removeAttribute('data-src');
        img.onload  = () => img.classList.add('loaded');
        img.onerror = () => img.classList.add('loaded');  // show even on error
      }
      _panelObserver.unobserve(entry.target);
    });
  }, { root: grid, rootMargin: '160px' });

  _panelRenderBatch();
  panel.classList.add('open');
}

function _panelRenderBatch() {
  const grid = document.getElementById('map-cluster-panel-grid');

  // Remove old scroll sentinel
  if (_panelSentinel?.parentNode) {
    _panelSentinel.parentNode.removeChild(_panelSentinel);
    _panelSentinel = null;
  }

  const end = Math.min(_panelRendered + PANEL_BATCH, _panelItems.length);

  for (let i = _panelRendered; i < end; i++) {
    const item     = _panelItems[i];
    const thumbSrc = `${API_BASE}/api/thumb/${encodeURIComponent(item.uniqueName)}?size=160&quality=70`;
    const card     = document.createElement('div');
    card.className = 'map-panel-thumb';

    // Use data-src so the image only loads when the card enters the viewport
    card.innerHTML = `
      <img data-src="${thumbSrc}" src="" alt="${escHtml(item.name)}">
      ${item.type === 'video' ? '<div class="map-panel-play">▶</div>' : ''}`;

    card.addEventListener('click', () => _openMapItem(item, i, _panelItems));
    grid.appendChild(card);
    _panelObserver.observe(card);   // trigger lazy load when this card is visible
  }

  _panelRendered = end;

  // Attach a sentinel at the bottom to trigger the next batch
  if (_panelRendered < _panelItems.length) {
    _panelSentinel = document.createElement('div');
    _panelSentinel.style.cssText = 'grid-column:1/-1;height:1px';

    const sentinelObs = new IntersectionObserver((entries) => {
      if (entries[0].isIntersecting) {
        sentinelObs.disconnect();
        _panelRenderBatch();
      }
    }, { root: grid, rootMargin: '80px' });

    grid.appendChild(_panelSentinel);
    sentinelObs.observe(_panelSentinel);
  }
}

function closeMapClusterPanel() {
  document.getElementById('map-cluster-panel').classList.remove('open');
  if (_panelObserver) { _panelObserver.disconnect(); _panelObserver = null; }
  _panelItems    = [];
  _panelRendered = 0;
  _panelSentinel = null;
}

// Open a map item in the lightbox.
// Fetches the full media record on demand if not already in memory.
function _openMapItem(item, panelIdx, panelList) {
  // Build filteredMedia from panelList so ← → navigation works in lightbox
  const list = panelList || [item];

  // Check in-memory first — may already be a full record
  const resolve = (it) =>
    db.media.find(m => m.uniqueName === it.uniqueName) || { ...it, metadata: {} };

  const tryOpen = () => {
    filteredMedia = list.map(resolve);
    lbIndex = panelIdx;
    openLightbox(panelIdx);
  };

  // If the item's full record is not in memory, fetch it first so metadata panel works
  const inMemory = db.media.find(m => m.uniqueName === item.uniqueName);
  if (inMemory) {
    tryOpen();
    return;
  }

  fetch(`${API_BASE}/api/media/by-id/${encodeURIComponent(item.uniqueName)}`)
    .then(r => r.ok ? r.json() : null)
    .then(full => {
      if (full && !db.media.find(m => m.uniqueName === full.uniqueName)) {
        db.media.push(full);
      }
      tryOpen();
    })
    .catch(() => tryOpen());  // open with stub on network error
}

// Reset map when media is re-synced so it reloads fresh GPS data
function _resetMap() {
  _mapLoaded = false;
  closeMapClusterPanel();
  if (_clusterGroup) {
    _leafletMap?.removeLayer(_clusterGroup);
    _clusterGroup = null;
  }
}

// ─────────────────────────────────────────────
//  BOOT
// ─────────────────────────────────────────────
init();