const captureList = document.querySelector("#capture-list");
const captureCount = document.querySelector("#capture-count");
const profileElement = document.querySelector("#profile");
const searchInput = document.querySelector("#search");
const sidebarToggle = document.querySelector("#sidebar-toggle");
const layoutElement = document.querySelector(".layout");

let captures = [];
let selectedId = null;
let profileAssets = {};

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");
}

function displayValue(value) {
  if (value === null || value === undefined || value === "") return "—";
  if (typeof value === "number") return value.toLocaleString();
  return String(value);
}

function imageMarkup(url, alt, className) {
  const assetPath = profileAssets[url];
  if (!assetPath) return "";
  const source = `/api/asset?path=${encodeURIComponent(assetPath)}`;
  return `<img class="${className}" src="${source}" alt="${escapeHtml(alt)}" loading="lazy">`;
}

function itemIcon(item) {
  const assets = item.media?.content?.assets || [];
  return assets.find((asset) => asset.key === "icon")?.value;
}

function formatStatValue(stat) {
  const value = stat?.value;
  if (!value) return "—";
  if (value.type === "RANGE") {
    return `${displayValue(value.min?.value)}–${displayValue(value.max?.value)}`;
  }
  const effective = stat.details?.effective?.value;
  return displayValue(effective ?? value.value ?? value);
}

function renderCaptureList() {
  const query = searchInput.value.trim().toLocaleLowerCase();
  const filtered = captures.filter((capture) => {
    const text = [
      capture.name,
      capture.realm,
      capture.region,
      capture.class,
      capture.spec,
      ...(capture.appearances || []).map((appearance) =>
        `${appearance.class || ""} ${appearance.spec || ""}`,
      ),
    ].join(" ").toLocaleLowerCase();
    return text.includes(query);
  });

  captureCount.textContent = String(filtered.length);
  if (!filtered.length) {
    captureList.innerHTML = '<p class="message">No matching snapshots.</p>';
    return;
  }

  captureList.innerHTML = filtered.map((capture) => {
    const date = capture.captured_at
      ? new Date(capture.captured_at).toLocaleString()
      : "Legacy capture";
    return `
      <button class="capture-card ${capture.id === selectedId ? "selected" : ""}"
        type="button" data-id="${escapeHtml(capture.id)}">
        <span class="capture-name">${escapeHtml(capture.name)}</span>
        <span class="capture-meta">${escapeHtml(capture.class)} · ${escapeHtml(capture.spec || "Profile")}</span>
        <span class="capture-meta">${escapeHtml(capture.realm)} · ${escapeHtml(capture.region)}</span>
        <span class="capture-date">${escapeHtml(date)}</span>
      </button>`;
  }).join("");

  captureList.querySelectorAll("[data-id]").forEach((button) => {
    button.addEventListener("click", () => selectCapture(button.dataset.id));
  });
}

function renderGear(character) {
  const slots = Object.entries(character.gear || {});
  if (!slots.length) return '<p class="message">No equipment data in this capture.</p>';
  const rows = slots.map(([key, item]) => {
    const stats = (item.stats || [])
      .filter((stat) => !stat.is_negated)
      .map((stat) => stat.display?.display_string || stat.type?.name)
      .filter(Boolean)
      .join(", ");
    const enchants = (item.enchantments || [])
      .map((enchantment) => enchantment.display_string)
      .filter(Boolean)
      .join("; ");
    const sockets = (item.sockets || [])
      .map((socket) => socket.item?.name || socket.display_string)
      .filter(Boolean)
      .join(", ");
    const setName = item.set?.display_string || "";
    const itemLevel = item.level?.display_string || "";
    const icon = itemIcon(item);
    const extras = [enchants, sockets && `Sockets: ${sockets}`, setName]
      .filter(Boolean)
      .join(" · ");
    return `<tr>
      <td class="slot-name">${escapeHtml(item.slot?.name || key)}</td>
      <td><div class="item-name">${imageMarkup(icon, item.name, "item-icon")}<strong>${escapeHtml(item.name || "Empty")}</strong></div>
        ${item.quality?.name ? `<span class="detail-text"> · ${escapeHtml(item.quality.name)}</span>` : ""}
        ${extras ? `<div class="detail-text">${escapeHtml(extras)}</div>` : ""}
      </td>
      <td>${escapeHtml(itemLevel)}</td>
      <td class="detail-text">${escapeHtml(stats)}</td>
    </tr>`;
  }).join("");

  return `<div class="table-wrap"><table>
    <thead><tr><th>Slot</th><th>Item</th><th>Item level</th><th>Equipped stats</th></tr></thead>
    <tbody>${rows}</tbody>
  </table></div>`;
}

function renderStats(character) {
  const groups = character.stats?.groups || [];
  if (!groups.length) return '<p class="message">No stat breakdown in this capture.</p>';
  return groups.map((group) => `
    <div class="subsection">
      <h4>${escapeHtml(group.name || "Stats")}</h4>
      <div class="stats-grid">
        ${(group.stats || []).map((stat) => `
          <div class="stat-card">
            <strong>${escapeHtml(stat.name || stat.enum || stat.slug)}</strong>
            <span>${escapeHtml(formatStatValue(stat))}</span>
          </div>`).join("")}
      </div>
    </div>`).join("");
}

function renderRatings(character) {
  const ratings = character.pvp?.ratings || {};
  const cards = [];
  for (const [mode, rating] of Object.entries(ratings)) {
    if (mode === "shuffle" || mode === "blitz") {
      for (const entry of rating.specs || []) {
        cards.push({
          label: `${mode} · ${entry.specialization?.name || "All specs"}`,
          value: entry.rating,
          tier: entry.tier?.name,
          record: `${entry.winCount ?? 0}W – ${entry.lossCount ?? 0}L`,
        });
      }
    } else if (rating && typeof rating === "object") {
      cards.push({
        label: mode,
        value: rating.rating,
        tier: rating.tier?.name,
        record: `${rating.winCount ?? 0}W – ${rating.lossCount ?? 0}L`,
      });
    }
  }
  if (!cards.length) return '<p class="message">No PvP ratings in this capture.</p>';
  return `<div class="rating-grid">${cards.map((rating) => `
    <div class="rating-card">
      <strong>${escapeHtml(rating.label)}</strong>
      <span>${escapeHtml(displayValue(rating.value))}${rating.tier ? ` · ${escapeHtml(rating.tier)}` : ""}</span>
      <div class="detail-text">${escapeHtml(rating.record)}</div>
    </div>`).join("")}</div>`;
}

function renderTalents(character) {
  const specs = character.specs || [];
  if (!specs.length) return '<p class="message">No specialization or talent data in this capture.</p>';

  return `<div class="talent-toolbar">
    <label>Specialization
      <select id="talent-spec">
        ${specs.map((spec, index) => `<option value="${index}" ${spec.active ? "selected" : ""}>${escapeHtml(spec.name)}</option>`).join("")}
      </select>
    </label>
    <label>Saved loadout <select id="talent-loadout"></select></label>
  </div>
  <div id="talent-layout-status" class="message">Loading saved tree layout…</div>
  <div id="talent-layout-content"></div>`;
}

function normalizedName(value) {
  return String(value || "").toLocaleLowerCase().replace(/[^a-z0-9]/g, "");
}

function nodeOptions(node) {
  const options = [];
  for (const rank of node.ranks || []) {
    if (rank.tooltip) options.push({ rank: rank.rank, ...rank.tooltip });
    for (const choice of rank.choice_of_tooltips || []) {
      options.push({ rank: rank.rank, ...choice });
    }
  }
  return options;
}

function talentMatches(option, selected) {
  const optionIds = new Set([
    option.talent?.id,
    option.spell_tooltip?.spell?.id,
    option.spell?.id,
  ].filter((id) => id !== undefined).map(String));
  const optionName = normalizedName(option.talent?.name || option.spell_tooltip?.spell?.name);
  const nameMatch = selected.find((talent) =>
    optionName && optionName === normalizedName(talent.name),
  );
  if (nameMatch) return nameMatch;
  return selected.find((talent) => {
    const selectedIds = [talent.id, talent.talent_id, talent.spell_id]
      .filter((id) => id !== undefined)
      .map(String);
    return selectedIds.some((id) => optionIds.has(id));
  });
}

function renderTalentBoard(title, nodes, selectedTalents) {
  const positionedNodes = (nodes || []).filter((node) =>
    Number.isFinite(node.display_row) && Number.isFinite(node.display_col),
  );
  if (!positionedNodes.length) {
    return `<div class="talent-tree-board"><h4>${escapeHtml(title)}</h4><p class="message">No node geometry in this layout.</p></div>`;
  }

  const minRow = Math.min(...positionedNodes.map((node) => node.display_row));
  const maxRow = Math.max(...positionedNodes.map((node) => node.display_row));
  const minCol = Math.min(...positionedNodes.map((node) => node.display_col));
  const maxCol = Math.max(...positionedNodes.map((node) => node.display_col));
  const nodeWidth = 56;
  const nodeHeight = 56;
  const stepX = 66;
  const stepY = 78;
  const margin = 30;
  const width = margin * 2 + (maxCol - minCol) * stepX + nodeWidth;
  const height = margin * 2 + (maxRow - minRow) * stepY + nodeHeight;
  const positions = new Map(positionedNodes.map((node) => [
    node.id,
    {
      x: margin + (node.display_col - minCol) * stepX,
      y: margin + (node.display_row - minRow) * stepY,
    },
  ]));
  const selectedNodeIds = new Set();
  const matchesByNode = new Map();
  for (const node of positionedNodes) {
    const match = nodeOptions(node)
      .map((option) => ({ option, selected: talentMatches(option, selectedTalents) }))
      .find((entry) => entry.selected);
    if (match) {
      selectedNodeIds.add(node.id);
      matchesByNode.set(node.id, match);
    }
  }

  const edges = new Set();
  for (const node of positionedNodes) {
    for (const target of node.unlocks || []) {
      if (positions.has(target)) edges.add(`${node.id}:${target}`);
    }
    for (const source of node.locked_by || []) {
      if (positions.has(source)) edges.add(`${source}:${node.id}`);
    }
  }
  const edgeMarkup = [...edges].map((edge) => {
    const [sourceId, targetId] = edge.split(":").map(Number);
    const source = positions.get(sourceId);
    const target = positions.get(targetId);
    const selected = selectedNodeIds.has(sourceId) && selectedNodeIds.has(targetId);
    return `<line class="tree-edge ${selected ? "selected" : ""}"
      x1="${source.x + nodeWidth / 2}" y1="${source.y + nodeHeight / 2}"
      x2="${target.x + nodeWidth / 2}" y2="${target.y + nodeHeight / 2}"></line>`;
  }).join("");

  const nodeMarkup = positionedNodes.map((node) => {
    const position = positions.get(node.id);
    const match = matchesByNode.get(node.id);
    const options = nodeOptions(node);
    const labels = options.map((option) =>
      option.talent?.name || option.spell_tooltip?.spell?.name,
    ).filter(Boolean);
    const selectedName = match?.selected?.name
      || match?.option?.talent?.name
      || match?.option?.spell_tooltip?.spell?.name;
    const label = selectedName || labels.join(" / ") || `Node ${node.id}`;
    const description = match?.selected?.description
      || match?.option?.spell_tooltip?.description
      || options.map((option) => option.spell_tooltip?.description).find(Boolean)
      || "";
    const iconUrl = match?.selected?.icon?.url;
    const icon = imageMarkup(iconUrl, label, "tree-node-icon");
    const selected = Boolean(match);
    const titleText = description ? `${label}: ${description}` : label;
    const rank = match?.selected?.rank || match?.option?.rank;
    return `<foreignObject x="${position.x}" y="${position.y}" width="${nodeWidth}" height="${nodeHeight}">
      <div xmlns="http://www.w3.org/1999/xhtml" class="tree-node ${selected ? "selected" : ""} ${node.node_type?.type === "CHOICE" ? "choice" : ""}"
        role="img" tabindex="0" aria-label="${escapeHtml(titleText)}" title="${escapeHtml(titleText)}">
        ${icon || '<span class="tree-node-icon placeholder"></span>'}
        <span class="tree-node-text">
          <strong>${escapeHtml(label)}</strong>
          <small>${selected ? `Selected${rank > 1 ? ` · rank ${escapeHtml(rank)}` : ""}` : escapeHtml(node.node_type?.type || "Talent")}</small>
        </span>
      </div>
    </foreignObject>`;
  }).join("");

  const placedNames = new Set();
  for (const match of matchesByNode.values()) {
    if (match.selected?.name) placedNames.add(normalizedName(match.selected.name));
  }
  const unplaced = selectedTalents.filter((talent) =>
    !placedNames.has(normalizedName(talent.name)),
  );
  const unplacedMarkup = unplaced.length
    ? `<p class="message">Snapshot talents not represented as selectable tree nodes: ${unplaced.map((talent) => escapeHtml(talent.name)).join(", ")}</p>`
    : "";

  return `<div class="talent-tree-board">
    <h4>${escapeHtml(title)}</h4>
    <div class="talent-tree-scroll">
      <svg class="talent-tree-svg" role="img" aria-label="${escapeHtml(title)}"
        viewBox="0 0 ${width} ${height}" style="width: max(100%, ${width}px); height: ${height}px">
        ${edgeMarkup}${nodeMarkup}
      </svg>
    </div>
    ${unplacedMarkup}
  </div>`;
}

function renderPvPTalents(talents) {
  if (!talents?.length) return '<p class="message">No PvP talents in this loadout.</p>';
  return `<div class="pvp-talent-row">${talents.map((talent) => `
    <div class="pvp-talent">
      ${imageMarkup(talent.icon?.url, talent.name, "talent-icon")}
      <span><strong>${escapeHtml(talent.name)}</strong>
        ${talent.description ? `<small>${escapeHtml(talent.description)}</small>` : ""}
      </span>
    </div>`).join("")}</div>`;
}

function renderSelectedLayout(spec, loadout, layout) {
  const tree = layout.tree || {};
  const heroTreeName = loadout.heroTalentTree?.name;
  const heroTree = (tree.hero_talent_trees || []).find((candidate) =>
    normalizedName(candidate.name) === normalizedName(heroTreeName),
  );
  const boards = [
    renderTalentBoard("Class tree", tree.class_talent_nodes, loadout.classTalents || []),
    renderTalentBoard(`${spec.name} tree`, tree.spec_talent_nodes, loadout.specTalents || []),
  ];
  if (heroTree) {
    boards.push(renderTalentBoard(
      `${heroTree.name} hero tree`,
      heroTree.hero_talent_nodes,
      loadout.heroTalents || [],
    ));
  }
  const code = loadout.talentLoadoutCode
    ? `<p class="code-line">Loadout code: ${escapeHtml(loadout.talentLoadoutCode)}</p>`
    : "";
  const status = document.querySelector("#talent-layout-status");
  status.textContent = layout.namespace
    ? `Tree data: ${layout.namespace} · saved ${new Date(layout.captured_at).toLocaleString()}`
    : "Tree data loaded from the local archive.";
  document.querySelector("#talent-layout-content").innerHTML = `
    ${boards.join("")}
    <div class="subsection"><h4>PvP talents</h4>${renderPvPTalents(spec.pvpTalents)}</div>
    ${code}`;
}

async function setupTalentViewer(character) {
  const specSelect = document.querySelector("#talent-spec");
  const loadoutSelect = document.querySelector("#talent-loadout");
  const status = document.querySelector("#talent-layout-status");
  const content = document.querySelector("#talent-layout-content");
  if (!specSelect || !loadoutSelect || !status || !content) return;

  const populateLoadouts = () => {
    const spec = character.specs[Number(specSelect.value)];
    const loadouts = spec.loadouts || [];
    loadoutSelect.innerHTML = loadouts.map((loadout, index) => `
      <option value="${index}" ${loadout.active ? "selected" : ""}>
        ${loadout.active ? "Active · " : ""}Loadout ${index + 1}${loadout.heroTalentTree?.name ? ` · ${escapeHtml(loadout.heroTalentTree.name)}` : ""}
      </option>`).join("");
  };

  const showLayout = async () => {
    const spec = character.specs[Number(specSelect.value)];
    const loadouts = spec.loadouts || [];
    const loadout = loadouts[Number(loadoutSelect.value)] || loadouts[0];
    if (!loadout) {
      status.textContent = "This specialization has no saved loadout.";
      content.innerHTML = "";
      return;
    }

    status.textContent = "Loading saved tree layout…";
    content.innerHTML = "";
    try {
      const params = new URLSearchParams({
        class: character.class?.slug || "",
        spec: spec.id || normalizedName(spec.name),
      });
      const response = await fetch(`/api/talent-layout?${params}`);
      if (!response.ok) {
        const error = await response.json();
        throw new Error(error.error || `Layout request failed (${response.status})`);
      }
      const layout = await response.json();
      renderSelectedLayout(spec, loadout, layout);
    } catch (error) {
      status.textContent = error.message;
      content.innerHTML = '<p class="message">Character snapshot remains unchanged. Import layout metadata separately to display the node tree.</p>';
    }
  };

  specSelect.addEventListener("change", () => {
    populateLoadouts();
    showLayout();
  });
  loadoutSelect.addEventListener("change", showLayout);
  populateLoadouts();
  await showLayout();
}

function renderProfessions(character) {
  const professions = character.professions || {};
  const all = [
    ...(professions.primaries || []),
    ...(professions.secondaries || []),
  ];
  if (!all.length) return '<p class="message">No profession data in this capture.</p>';
  return `<div class="profession-grid">${all.map((profession) => `
    <div class="profession-card">
      <strong>${escapeHtml(profession.name)}</strong>
      ${(profession.tiers || []).map((tier) => `
        <span>${escapeHtml(tier.name)}: ${escapeHtml(tier.skillPoints)} / ${escapeHtml(tier.maxSkillPoints)}</span>`).join("<br>")}
    </div>`).join("")}</div>`;
}

function renderProfile(capture, profile) {
  const character = profile.character || {};
  const realm = character.realm || {};
  const identity = [
    character.race?.name,
    character.gender?.name,
    character.class?.name,
  ].filter(Boolean).join(" ");
  const appearances = capture.appearances || [];
  const updated = character.lastUpdatedTimestamp?.iso8601;
  const captureDate = capture.captured_at
    ? new Date(capture.captured_at).toLocaleString()
    : "Legacy capture · timestamp unavailable";
  const avatar = imageMarkup(character.avatar?.url, character.name, "character-avatar");

  profileElement.innerHTML = `
    <header class="profile-header">
      ${avatar}
      <div class="profile-title">
        <h2>${escapeHtml(character.name || capture.name)}</h2>
        <p class="profile-subtitle">${escapeHtml(identity)} · ${escapeHtml(realm.name || capture.realm)} · Level ${escapeHtml(character.level ?? "—")}</p>
        <div class="pill-row">
          ${character.averageItemLevel !== undefined ? `<span class="pill">Average item level ${escapeHtml(character.averageItemLevel)}</span>` : ""}
          ${character.faction?.name ? `<span class="pill">${escapeHtml(character.faction.name)}</span>` : ""}
          ${appearances.map((appearance) => `<span class="pill">Shuffle · ${escapeHtml(appearance.class)} ${escapeHtml(appearance.spec)}</span>`).join("")}
        </div>
      </div>
      <p class="capture-stamp">Captured ${escapeHtml(captureDate)}${updated ? `<br>Armory updated ${escapeHtml(updated)}` : ""}</p>
    </header>
    <section class="section"><h3>Equipped gear</h3>${renderGear(character)}</section>
    <section class="section"><h3>Character stats</h3>${renderStats(character)}</section>
    <section class="section"><h3>PvP ratings</h3>${renderRatings(character)}</section>
    <section class="section"><h3>Talent tree layouts</h3>
      <p class="section-hint">Select a specialization and saved loadout to see its captured choices positioned on the patch-specific game tree.</p>
      ${renderTalents(character)}
    </section>
    <section class="section"><h3>Professions</h3>${renderProfessions(character)}</section>
    <section class="section"><h3>Complete captured profile payload</h3>
      <details><summary>Show source JSON</summary><pre>${escapeHtml(JSON.stringify(profile, null, 2))}</pre></details>
    </section>`;
  setupTalentViewer(character);
}

async function selectCapture(id) {
  selectedId = id;
  renderCaptureList();
  profileElement.innerHTML = '<p class="message">Loading captured profile…</p>';
  try {
    const response = await fetch(`/api/capture?id=${encodeURIComponent(id)}`);
    if (!response.ok) throw new Error(`Profile request failed (${response.status})`);
    const payload = await response.json();
    const profile = payload.profile;
    profileAssets = payload.assets || {};
    const capture = captures.find((item) => item.id === id);
    if (capture) renderProfile(capture, profile);
  } catch (error) {
    profileElement.innerHTML = `<p class="message">${escapeHtml(error.message)}</p>`;
  }
}

async function initialize() {
  try {
    const response = await fetch("/api/captures");
    if (!response.ok) throw new Error(`Archive request failed (${response.status})`);
    captures = await response.json();
    captures.sort((left, right) => (right.captured || 0) - (left.captured || 0));
    renderCaptureList();
    if (captures.length) selectCapture(captures[0].id);
  } catch (error) {
    captureList.innerHTML = `<p class="message">${escapeHtml(error.message)}</p>`;
  }
}

searchInput.addEventListener("input", renderCaptureList);
sidebarToggle.addEventListener("click", () => {
  const isExpanded = sidebarToggle.getAttribute("aria-expanded") === "true";
  sidebarToggle.setAttribute("aria-expanded", String(!isExpanded));
  sidebarToggle.setAttribute(
    "aria-label",
    isExpanded ? "Expand character list" : "Collapse character list",
  );
  sidebarToggle.title = isExpanded
    ? "Expand character list"
    : "Collapse character list";
  sidebarToggle.querySelector(".sidebar-toggle-icon").textContent =
    isExpanded ? "›" : "‹";
  layoutElement.classList.toggle("sidebar-collapsed", isExpanded);
});
initialize();
