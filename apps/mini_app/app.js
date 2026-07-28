const tg = window.Telegram?.WebApp;
const isTelegramContext = Boolean(tg?.initData || tg?.initDataUnsafe?.user);

tg?.ready();
tg?.expand();

const demoCatalog = {
  bots: [
    {
      id: "super",
      name: "Super Bot",
      description: "Downloads, rankings, castigos e utilidades do grupo.",
      commands: [
        {
          name: "menu",
          category: "Interface",
          description: "Abre o painel de comandos",
          aliases: ["help"],
          adminOnly: false,
          usage: ""
        },
        {
          name: "ranking",
          category: "Rankings",
          description: "Ranking semanal de links repetidos",
          aliases: [],
          adminOnly: false,
          usage: ""
        },
        {
          name: "retry",
          category: "Utilidades",
          description: "Tenta novamente um download que falhou",
          aliases: [],
          adminOnly: false,
          usage: "/retry em resposta ao erro"
        }
      ]
    },
    {
      id: "comandos",
      name: "Comandos Bot",
      description: "Comandos personalizados, GIFs e backlog.",
      commands: [
        {
          name: "list",
          category: "Comandos personalizados",
          description: "Lista comandos personalizados",
          aliases: [],
          adminOnly: false,
          usage: ""
        },
        {
          name: "gifstats",
          category: "GIFs",
          description: "Mostra estatísticas das bases de GIFs",
          aliases: [],
          adminOnly: false,
          usage: ""
        },
        {
          name: "backlog",
          category: "Backlog",
          description: "Lista, cria e gerencia sugestões",
          aliases: [],
          adminOnly: false,
          usage: "/backlog texto|done|merda|lixeira|limpar"
        }
      ]
    }
  ]
};

const categoryMeta = {
  Interface: { icon: "layout-dashboard", tone: "blue" },
  Rankings: { icon: "trophy", tone: "amber" },
  Castigo: { icon: "shield-alert", tone: "red" },
  Diversao: { icon: "sparkles", tone: "green" },
  Diversão: { icon: "sparkles", tone: "green" },
  Utilidades: { icon: "wrench", tone: "blue" },
  Administracao: { icon: "settings", tone: "amber" },
  Administração: { icon: "settings", tone: "amber" },
  "Comandos personalizados": { icon: "message-square-code", tone: "blue" },
  GIFs: { icon: "image", tone: "green" },
  Backlog: { icon: "clipboard-list", tone: "amber" }
};

const botMeta = {
  super: { icon: "download", label: "Super Bot" },
  comandos: { icon: "messages-square", label: "Comandos Bot" }
};

const state = {
  bots: [],
  botId: readPreference("fmcpt.bot") || "super",
  category: "category:Todos",
  query: "",
  view: "commands",
  adminMode: "create",
  selectedCommand: null,
  authorized: false
};

const elements = {
  adminModeTabs: document.querySelector("#adminModeTabs"),
  adminNavButton: document.querySelector("#adminNavButton"),
  adminView: document.querySelector("#adminView"),
  botDescription: document.querySelector("#botDescription"),
  botIcon: document.querySelector("#botIcon"),
  botSwitcher: document.querySelector("#botSwitcher"),
  botTitle: document.querySelector("#botTitle"),
  categoryTabs: document.querySelector("#categoryTabs"),
  clearSearch: document.querySelector("#clearSearch"),
  closeApp: document.querySelector("#closeApp"),
  closeSheet: document.querySelector("#closeSheet"),
  commandCount: document.querySelector("#commandCount"),
  commandList: document.querySelector("#commandList"),
  commandsView: document.querySelector("#commandsView"),
  commandSheet: document.querySelector("#commandSheet"),
  connectionStatus: document.querySelector("#connectionStatus"),
  createCommandForm: document.querySelector("#createCommandForm"),
  updateCommandForm: document.querySelector("#updateCommandForm"),
  deleteCommandForm: document.querySelector("#deleteCommandForm"),
  categoryForm: document.querySelector("#categoryForm"),
  backlogForm: document.querySelector("#backlogForm"),
  resultsCount: document.querySelector("#resultsCount"),
  searchInput: document.querySelector("#searchInput"),
  sheetAccess: document.querySelector("#sheetAccess"),
  sheetAliases: document.querySelector("#sheetAliases"),
  sheetAliasesRow: document.querySelector("#sheetAliasesRow"),
  sheetBackdrop: document.querySelector("#sheetBackdrop"),
  sheetCategory: document.querySelector("#sheetCategory"),
  sheetCategoryDetail: document.querySelector("#sheetCategoryDetail"),
  sheetCommandName: document.querySelector("#sheetCommandName"),
  sheetDescription: document.querySelector("#sheetDescription"),
  sheetIcon: document.querySelector("#sheetIcon"),
  sheetPreview: document.querySelector("#sheetPreview"),
  sheetType: document.querySelector("#sheetType"),
  sheetUsage: document.querySelector("#sheetUsage"),
  sheetUsageRow: document.querySelector("#sheetUsageRow"),
  toast: document.querySelector("#toast"),
  toastMessage: document.querySelector("#toastMessage")
};

const typeMeta = {
  texto: { label: "Texto", icon: "file-text", tone: "blue" },
  foto: { label: "Imagem", icon: "image", tone: "green" },
  imagem: { label: "Imagem", icon: "image", tone: "green" },
  image: { label: "Imagem", icon: "image", tone: "green" },
  video: { label: "Video", icon: "video", tone: "red" },
  gif: { label: "GIF", icon: "badge-play", tone: "green" },
  audio: { label: "Audio", icon: "music", tone: "amber" },
  voice: { label: "Voz", icon: "mic", tone: "amber" }
};

const categoryActions = new Set(["create_category", "update_category", "delete_category"]);

let toastTimer;
let lastFocusedElement;
let sheetPreviewObjectUrl;

function readPreference(key) {
  try {
    return window.localStorage.getItem(key);
  } catch {
    return null;
  }
}

function writePreference(key, value) {
  try {
    window.localStorage.setItem(key, value);
  } catch {
    // Preferences are optional in restricted Telegram webviews.
  }
}

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function normalizeText(value) {
  return String(value ?? "").trim().toLocaleLowerCase("pt-BR");
}

function normalizeType(command) {
  return normalizeText(command.type || command.mediaType || command.tipo || "texto");
}

function mediaUrl(command) {
  return command.mediaUrl || command.previewUrl || command.thumbnailUrl || command.preview_url || "";
}

function privateMediaKey(command) {
  return command.mediaKey || command.media_key || "";
}

function privatePreviewCommand(command) {
  return command.previewCommand || command.preview_command || (command.privateMedia ? command.name : "");
}

function stillUrl(command) {
  return command.thumbnailUrl || command.previewUrl || command.posterUrl || command.mediaUrl || "";
}

function commandContent(command) {
  return command.content || command.text || command.conteudo || "";
}

function commandDescription(command) {
  return command.description || command.descricao || "";
}

function commandCategory(command) {
  return command.category || command.categoria || "Comandos personalizados";
}

function commandPreviewAlt(command) {
  return `Preview de /${command.name}`;
}

function isCustomCommand(command) {
  return Boolean(command.isCustom || command.custom);
}

function renderCommandIcon(command, size = "small") {
  const meta = commandMeta(command);
  const type = normalizeType(command);
  const preview = stillUrl(command);
  const isPlayable = ["gif", "video"].includes(type);
  const canRenderStill = preview && ["foto", "imagem", "image", "gif", "video"].includes(type);

  if (canRenderStill) {
    return `
      <span class="command-icon command-icon--media" data-tone="${meta.tone}" aria-hidden="true">
        <img src="${escapeHtml(preview)}" alt="" loading="${size === "small" ? "lazy" : "eager"}">
        ${isPlayable ? '<span class="command-icon__badge"><i data-lucide="play"></i></span>' : ""}
      </span>
    `;
  }

  return `
    <span class="command-icon" data-tone="${meta.tone}" aria-hidden="true">
      <i data-lucide="${meta.icon}"></i>
    </span>
  `;
}

function renderSheetPreview(command) {
  const type = normalizeType(command);
  const src = mediaUrl(command);
  const content = commandContent(command);

  if (["foto", "imagem", "image", "gif"].includes(type) && src) {
    return `<img src="${escapeHtml(src)}" alt="${escapeHtml(commandPreviewAlt(command))}">`;
  }

  if (type === "video" && src) {
    const poster = stillUrl(command);
    return `
      <video src="${escapeHtml(src)}" ${poster ? `poster="${escapeHtml(poster)}"` : ""} controls autoplay muted playsinline loop></video>
    `;
  }

  if (["audio", "voice"].includes(type) && src) {
    return `<audio src="${escapeHtml(src)}" controls autoplay></audio>`;
  }

  if (type === "texto" && content) {
    return `<pre class="sheet-preview__text">${escapeHtml(content)}</pre>`;
  }

  return "";
}

function renderSheetPreviewFromSource(command, src) {
  const type = normalizeType(command);
  if (["foto", "imagem", "image", "gif"].includes(type)) {
    return `<img src="${escapeHtml(src)}" alt="${escapeHtml(commandPreviewAlt(command))}">`;
  }
  if (type === "video") {
    return `<video src="${escapeHtml(src)}" controls autoplay muted playsinline loop></video>`;
  }
  if (["audio", "voice"].includes(type)) {
    return `<audio src="${escapeHtml(src)}" controls autoplay></audio>`;
  }
  return "";
}

function clearSheetPreviewObjectUrl() {
  if (sheetPreviewObjectUrl) {
    URL.revokeObjectURL(sheetPreviewObjectUrl);
    sheetPreviewObjectUrl = null;
  }
}

async function loadPrivateSheetPreview(command) {
  const key = privateMediaKey(command);
  const previewCommand = privatePreviewCommand(command);
  if (!key && !previewCommand) return;
  elements.sheetPreview.hidden = false;
  elements.sheetPreview.innerHTML = `
    <div class="sheet-preview__loading">
      <i data-lucide="loader-circle"></i>
      <span>Carregando preview</span>
    </div>
  `;
  refreshIcons();
  try {
    const endpoint = key
      ? `./api/media/${encodeURIComponent(key)}`
      : `./api/preview/${encodeURIComponent(previewCommand)}`;
    const response = await fetch(endpoint, {
      cache: "no-store",
      headers: authHeaders()
    });
    if (!response.ok) throw new Error("preview_load_failed");
    const blob = await response.blob();
    if (state.selectedCommand !== command) return;
    clearSheetPreviewObjectUrl();
    sheetPreviewObjectUrl = URL.createObjectURL(blob);
    elements.sheetPreview.innerHTML = renderSheetPreviewFromSource(command, sheetPreviewObjectUrl);
    elements.sheetPreview.hidden = !elements.sheetPreview.innerHTML;
  } catch {
    if (state.selectedCommand === command) {
      elements.sheetPreview.innerHTML = `
        <div class="sheet-preview__loading">
          <i data-lucide="file-warning"></i>
          <span>Preview indisponível</span>
        </div>
      `;
      refreshIcons();
    }
  }
}

function refreshIcons() {
  window.lucide?.createIcons({
    attrs: {
      "aria-hidden": "true",
      "stroke-width": 2
    }
  });
}

function applyTheme() {
  const preferredTheme = window.matchMedia?.("(prefers-color-scheme: dark)").matches
    ? "dark"
    : "light";
  const theme = tg?.colorScheme || preferredTheme;
  document.documentElement.dataset.theme = theme;

  const themeColor = theme === "dark" ? "#10171b" : "#f3f6f7";
  document.querySelector('meta[name="theme-color"]')?.setAttribute("content", themeColor);
}

function currentBot() {
  return state.bots.find((bot) => bot.id === state.botId) || state.bots[0];
}

function customCommandToFrontend(name, info) {
  return {
    name,
    category: info.category || info.categoria || "Comandos personalizados",
    description: info.description || info.descricao || "Comando personalizado",
    aliases: info.aliases || [],
    adminOnly: false,
    usage: `/${name}`,
    type: info.type || info.tipo || "texto",
    content: info.content || info.conteudo || "",
    mediaUrl: info.mediaUrl || info.media_url || "",
    previewUrl: info.previewUrl || info.preview_url || "",
    thumbnailUrl: info.thumbnailUrl || info.thumbnail_url || "",
    privateMedia: Boolean(info.privateMedia || info.private_media),
    mediaKey: info.mediaKey || info.media_key || "",
    previewCommand: info.previewCommand || info.preview_command || "",
    isCustom: true
  };
}

function normalizeCatalog(catalog) {
  const bots = (catalog.bots || []).map((bot) => {
    const customCommands = bot.customCommands || {};
    const customList = Array.isArray(customCommands)
      ? customCommands.map((command) => ({ ...command, isCustom: true }))
      : Object.entries(customCommands).map(([name, info]) => customCommandToFrontend(name, info || {}));
    return {
      ...bot,
      commands: [...(bot.commands || []), ...customList]
    };
  });
  return { ...catalog, bots };
}

function commandMeta(command) {
  if (isCustomCommand(command)) {
    return typeMeta[normalizeType(command)] || categoryMeta[commandCategory(command)] || { icon: "terminal", tone: "blue" };
  }

  return categoryMeta[commandCategory(command)] || { icon: "terminal", tone: "blue" };
}

function categoryGroups(bot) {
  const commands = bot?.commands || [];
  const groups = new Map([["category:Todos", { label: "Todos", count: commands.length }]]);
  const typeGroups = new Map();
  commands.filter(isCustomCommand).forEach((command) => {
    const type = normalizeType(command);
    const typeLabel = typeMeta[type]?.label || type;
    typeGroups.set(type, {
      label: typeLabel,
      count: (typeGroups.get(type)?.count || 0) + 1
    });
    const category = commandCategory(command);
    const key = `category:${category}`;
    groups.set(key, {
      label: category,
      count: (groups.get(key)?.count || 0) + 1
    });
  });
  (bot?.customCategories || []).forEach((category) => {
    const key = `category:${category}`;
    if (!groups.has(key)) {
      groups.set(key, { label: category, count: 0 });
    }
  });
  return [
    ...Array.from(groups.entries()),
    ...Array.from(typeGroups.entries()).map(([type, group]) => [`type:${type}`, group])
  ];
}

function filteredCommands() {
  const bot = currentBot();
  if (!bot) return [];

  const query = state.query.trim().toLocaleLowerCase("pt-BR");
  return bot.commands.filter((command) => {
    const [filterKind, ...filterParts] = state.category.split(":");
    const filterValue = filterParts.join(":");
    const matchesCategory =
      state.category === "category:Todos" ||
      (isCustomCommand(command) && filterKind === "category" && commandCategory(command) === filterValue) ||
      (isCustomCommand(command) && filterKind === "type" && normalizeType(command) === filterValue);
    const searchableText = [
      command.name,
      commandCategory(command),
      commandDescription(command),
      commandContent(command),
      ...(command.aliases || [])
    ]
      .join(" ")
      .toLocaleLowerCase("pt-BR");
    return matchesCategory && (!query || searchableText.includes(query));
  });
}

function renderBotSwitcher() {
  elements.botSwitcher.innerHTML = state.bots
    .map((bot) => {
      const meta = botMeta[bot.id] || { icon: "bot", label: bot.name };
      const active = bot.id === state.botId;
      return `
        <button
          class="${active ? "active" : ""}"
          data-bot="${escapeHtml(bot.id)}"
          type="button"
          aria-pressed="${active}"
        >
          <i data-lucide="${meta.icon}"></i>
          <span>${escapeHtml(meta.label)}</span>
        </button>
      `;
    })
    .join("");
}

function renderOverview() {
  const bot = currentBot();
  if (!bot) return;

  const meta = botMeta[bot.id] || { icon: "bot" };
  elements.botTitle.textContent = bot.name;
  elements.botDescription.textContent = bot.description;
  elements.commandCount.textContent = bot.commands.length;
  elements.botIcon.classList.toggle("is-command-bot", bot.id === "comandos");
  elements.botIcon.innerHTML = `<i data-lucide="${meta.icon}"></i>`;
}

function renderCategoryTabs() {
  const bot = currentBot();
  const groups = categoryGroups(bot);
  const availableCategories = groups.map(([category]) => category);

  if (!availableCategories.includes(state.category)) {
    state.category = "category:Todos";
  }

  elements.categoryTabs.innerHTML = groups
    .map(([filter, group]) => {
      const active = filter === state.category;
      const kind = filter.startsWith("type:") ? "type" : "category";
      const icon = kind === "type" ? typeMeta[filter.slice(5)]?.icon : null;
      return `
        <button
          class="${active ? "active" : ""}"
          data-category="${escapeHtml(filter)}"
          type="button"
          aria-pressed="${active}"
        >
          ${icon ? `<i data-lucide="${icon}"></i>` : ""}
          ${escapeHtml(group.label)}
          <span>${group.count}</span>
        </button>
      `;
    })
    .join("");
}

function renderCommands() {
  const commands = filteredCommands();
  const resultLabel = commands.length === 1 ? "1 resultado" : `${commands.length} resultados`;
  elements.resultsCount.textContent = resultLabel;
  elements.clearSearch.hidden = !state.query;

  if (!commands.length) {
    elements.commandList.innerHTML = `
      <div class="empty-state">
        <div>
          <span class="empty-state__icon"><i data-lucide="search-x"></i></span>
          <strong>Nenhum comando encontrado</strong>
          <p>Tente outro termo ou escolha uma categoria diferente.</p>
        </div>
      </div>
    `;
    refreshIcons();
    return;
  }

  elements.commandList.innerHTML = commands
    .map((command) => {
      const name = escapeHtml(command.name);
      const type = normalizeType(command);
      const typeLabel = typeMeta[type]?.label || type;
      const typeBadge = isCustomCommand(command)
        ? `<span class="admin-badge">${escapeHtml(typeLabel)}</span>`
        : "";
      return `
        <article class="command-row">
          ${renderCommandIcon(command)}
          <button
            class="command-summary"
            data-command-details="${name}"
            type="button"
            aria-label="Ver detalhes de /${name}"
          >
            <span class="command-name-row">
              <span class="command-name">/${name}</span>
              ${typeBadge}
              ${command.adminOnly ? '<span class="admin-badge">Admin</span>' : ""}
            </span>
            <p>${escapeHtml(commandDescription(command))}</p>
          </button>
        </article>
      `;
    })
    .join("");

  refreshIcons();
}

function renderNavigation() {
  const showAdmin = currentBot()?.id === "comandos";
  elements.adminNavButton.hidden = !showAdmin;
  document.querySelector(".bottom-nav")?.classList.toggle("is-single", !showAdmin);

  if (!showAdmin && state.view === "admin") {
    state.view = "commands";
  }

  elements.commandsView.hidden = state.view !== "commands";
  elements.adminView.hidden = state.view !== "admin";

  document.querySelectorAll("[data-view]").forEach((button) => {
    const active = button.dataset.view === state.view;
    button.classList.toggle("active", active);
    if (active) {
      button.setAttribute("aria-current", "page");
    } else {
      button.removeAttribute("aria-current");
    }
  });
}

function renderAdminMode() {
  elements.adminModeTabs.querySelectorAll("[data-admin-mode]").forEach((button) => {
    const active = button.dataset.adminMode === state.adminMode;
    button.classList.toggle("active", active);
    button.setAttribute("aria-selected", String(active));
  });

  document.querySelectorAll("[data-admin-form]").forEach((form) => {
    form.hidden = form.dataset.adminForm !== state.adminMode;
  });
}

function render() {
  renderBotSwitcher();
  renderOverview();
  renderCategoryTabs();
  renderCommands();
  renderNavigation();
  renderAdminMode();
  refreshIcons();
}

function selectBot(botId) {
  if (!state.bots.some((bot) => bot.id === botId)) return;

  state.botId = botId;
    state.category = "category:Todos";
  state.query = "";
  elements.searchInput.value = "";
  writePreference("fmcpt.bot", botId);

  if (botId !== "comandos") {
    state.view = "commands";
  }

  closeCommandSheet();
  tg?.HapticFeedback?.selectionChanged();
  render();
}

function selectView(view) {
  if (view === "admin" && currentBot()?.id !== "comandos") return;
  state.view = view;
  tg?.HapticFeedback?.selectionChanged();
  renderNavigation();
  window.scrollTo({ top: 0, behavior: "smooth" });
}

function selectCategory(category) {
  state.category = category;
  tg?.HapticFeedback?.selectionChanged();
  renderCategoryTabs();
  renderCommands();
  refreshIcons();
}

function setAdminMode(mode) {
  if (!["create", "update", "delete", "categories", "backlog"].includes(mode)) return;
  state.adminMode = mode;
  tg?.HapticFeedback?.selectionChanged();
  renderAdminMode();
  refreshIcons();
}

function showToast(message) {
  window.clearTimeout(toastTimer);
  elements.toastMessage.textContent = message;
  elements.toast.hidden = false;
  refreshIcons();

  toastTimer = window.setTimeout(() => {
    elements.toast.hidden = true;
  }, 3200);
}

function actionMessage(data) {
  const labels = {
    create_command: `Comando /${data.name} salvo.`,
    update_command: `Alteração de /${data.name} salva.`,
    delete_command: `Comando /${data.name} apagado.`,
    backlog_add: "Sugestão enviada ao backlog.",
    create_category: `Categoria ${data.name} salva.`,
    update_category: `Categoria ${data.name} alterada.`,
    delete_category: `Categoria ${data.name} excluída.`
  };
  return labels[data.action] || "Alteração salva.";
}

function authHeaders(extra = {}) {
  return {
    ...extra,
    "X-Telegram-Init-Data": tg?.initData || ""
  };
}

function applyCatalogUpdate(catalog) {
  if (!catalog?.bots) return;
  state.bots = normalizeCatalog(catalog).bots || [];
  render();
}

async function apiRequest(path, options = {}) {
  if (!isTelegramContext || !tg?.initData) {
    throw new Error("telegram_context_required");
  }
  const response = await fetch(path, {
    ...options,
    cache: "no-store",
    headers: authHeaders(options.headers || {})
  });
  let data = {};
  try {
    data = await response.json();
  } catch {
    data = {};
  }
  if (response.status === 403) {
    throw new Error("unauthorized");
  }
  if (!response.ok) {
    throw new Error(data.error || "request_failed");
  }
  return data;
}

async function sendAdminAction(data = {}) {
  tg?.HapticFeedback?.impactOccurred("light");
  try {
    const result = await apiRequest("./api/admin/action", {
      method: "POST",
      headers: {
        "Content-Type": "application/json"
      },
      body: JSON.stringify(data)
    });
    applyCatalogUpdate(result.catalog);
    showToast(actionMessage(data));
    return true;
  } catch (error) {
    showToast(adminErrorMessage(error));
    return false;
  }
}

async function sendCommandForm(form, mode) {
  tg?.HapticFeedback?.impactOccurred("light");
  const payload = new FormData(form);
  payload.set("mode", mode);
  const name = normalizeCommandName(payload.get("name"));
  payload.set("name", name);
  const action = mode === "update" ? "update_command" : "create_command";

  try {
    const result = await apiRequest("./api/admin/upload-command", {
      method: "POST",
      body: payload
    });
    applyCatalogUpdate(result.catalog);
    showToast(actionMessage({ action, name }));
    form.reset();
    updateMediaRequirements(form);
    return true;
  } catch (error) {
    showToast(adminErrorMessage(error));
    return false;
  }
}

function adminErrorMessage(error) {
  const messages = {
    telegram_context_required: "Abra o painel pelo Telegram.",
    unauthorized: "Acesso negado para este usuário.",
    command_exists: "Este comando já existe.",
    not_found: "Item não encontrado.",
    request_failed: "Não foi possível salvar agora."
  };
  return messages[error.message] || error.message || messages.request_failed;
}

function findCommand(name) {
  return currentBot()?.commands.find((command) => command.name === name);
}

function openCommandSheet(command) {
  if (!command) return;

  const meta = commandMeta(command);
  const type = normalizeType(command);
  const typeLabel = typeMeta[type]?.label || type;
  const category = commandCategory(command);
  state.selectedCommand = command;
  lastFocusedElement = document.activeElement;
  clearSheetPreviewObjectUrl();

  elements.sheetIcon.dataset.tone = meta.tone;
  elements.sheetIcon.outerHTML = renderCommandIcon(command, "large").replace("command-icon--media", "command-icon--media").replace("<span", '<span id="sheetIcon"');
  elements.sheetIcon = document.querySelector("#sheetIcon");
  const previewHtml = renderSheetPreview(command);
  elements.sheetPreview.innerHTML = previewHtml;
  elements.sheetPreview.hidden = !previewHtml;
  if (!previewHtml && (privateMediaKey(command) || privatePreviewCommand(command))) {
    loadPrivateSheetPreview(command);
  }
  elements.sheetCategory.textContent = category;
  elements.sheetCategoryDetail.textContent = category;
  elements.sheetCommandName.textContent = `/${command.name}`;
  elements.sheetDescription.textContent = commandDescription(command);
  elements.sheetUsage.textContent = command.usage || `/${command.name}`;
  elements.sheetType.textContent = typeLabel;
  elements.sheetAliases.textContent = (command.aliases || []).map((alias) => `/${alias}`).join(", ");
  elements.sheetAccess.textContent = command.adminOnly ? "Somente administradores" : "Todos do grupo";
  elements.sheetUsageRow.hidden = false;
  elements.sheetAliasesRow.hidden = !(command.aliases || []).length;

  elements.sheetBackdrop.hidden = false;
  elements.commandSheet.hidden = false;
  document.body.classList.add("sheet-open");
  refreshIcons();
  window.requestAnimationFrame(() => elements.closeSheet.focus());
}

function closeCommandSheet() {
  if (elements.commandSheet.hidden) return;
  clearSheetPreviewObjectUrl();
  elements.sheetBackdrop.hidden = true;
  elements.commandSheet.hidden = true;
  document.body.classList.remove("sheet-open");
  state.selectedCommand = null;
  lastFocusedElement?.focus?.();
}

function normalizeCommandName(value) {
  return String(value || "").trim().replace(/^\/+/, "").toLowerCase();
}

function updateMediaRequirements(form) {
  const type = String(form.elements.type?.value || "texto");
  const content = form.elements.content;
  const media = form.elements.media;
  if (!content || !media) return;
  const isText = type === "texto" || type === "";
  content.required = isText && form.id === "createCommandForm";
  media.required = !isText && form.id === "createCommandForm";
  media.closest(".field").hidden = isText;
}

function sendAdminShortcut(mode) {
  if (mode === "custom") {
    state.view = "commands";
    state.botId = "comandos";
    state.category = "category:Comandos personalizados";
    render();
    return;
  }
  selectView("admin");
  setAdminMode(mode);
}

elements.botSwitcher.addEventListener("click", (event) => {
  const button = event.target.closest("button[data-bot]");
  if (button) selectBot(button.dataset.bot);
});

elements.searchInput.addEventListener("input", (event) => {
  state.query = event.target.value;
  renderCommands();
});

elements.clearSearch.addEventListener("click", () => {
  state.query = "";
  elements.searchInput.value = "";
  elements.searchInput.focus();
  renderCommands();
});

elements.categoryTabs.addEventListener("click", (event) => {
  const button = event.target.closest("button[data-category]");
  if (button) selectCategory(button.dataset.category);
});

elements.commandList.addEventListener("click", (event) => {
  const detailsButton = event.target.closest("button[data-command-details]");
  if (detailsButton) {
    openCommandSheet(findCommand(detailsButton.dataset.commandDetails));
  }
});

document.querySelector(".bottom-nav").addEventListener("click", (event) => {
  const button = event.target.closest("button[data-view]");
  if (button) selectView(button.dataset.view);
});

elements.adminModeTabs.addEventListener("click", (event) => {
  const button = event.target.closest("button[data-admin-mode]");
  if (button) setAdminMode(button.dataset.adminMode);
});

document.querySelectorAll("[data-admin]").forEach((button) => {
  button.addEventListener("click", () => sendAdminShortcut(button.dataset.admin));
});

[elements.createCommandForm, elements.updateCommandForm].forEach((form) => {
  form.elements.type?.addEventListener("change", () => updateMediaRequirements(form));
  updateMediaRequirements(form);
});

elements.createCommandForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  const form = event.currentTarget;
  const data = new FormData(form);
  const name = normalizeCommandName(data.get("name"));

  if (!/^[a-z0-9_]{1,32}$/.test(name)) {
    showToast("Use até 32 letras, números ou sublinhados no nome.");
    return;
  }

  await sendCommandForm(form, "create");
});

elements.updateCommandForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  const form = event.currentTarget;
  const data = new FormData(form);
  const name = normalizeCommandName(data.get("name"));

  if (!/^[a-z0-9_]{1,32}$/.test(name)) {
    showToast("Informe um nome de comando válido.");
    return;
  }

  await sendCommandForm(form, "update");
});

elements.deleteCommandForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  const form = new FormData(event.currentTarget);
  const name = normalizeCommandName(form.get("name"));

  if (!/^[a-z0-9_]{1,32}$/.test(name)) {
    showToast("Informe um nome de comando válido.");
    return;
  }

  const success = await sendAdminAction({
    action: "delete_command",
    name
  });
  if (success) event.currentTarget.reset();
});

elements.categoryForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  const form = new FormData(event.currentTarget);
  const action = String(form.get("action") || "");
  const name = String(form.get("name") || "").trim();
  const newName = String(form.get("newName") || "").trim();

  if (!categoryActions.has(action) || !name) {
    showToast("Informe uma categoria válida.");
    return;
  }

  if (action === "update_category" && !newName) {
    showToast("Informe o novo nome da categoria.");
    return;
  }

  const success = await sendAdminAction({ action, name, newName });
  if (success) event.currentTarget.reset();
});

elements.backlogForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  const form = new FormData(event.currentTarget);
  const text = String(form.get("text") || "").trim();
  if (!text) return;

  const success = await sendAdminAction({
    action: "backlog_add",
    text
  });
  if (success) event.currentTarget.reset();
});

elements.closeSheet.addEventListener("click", closeCommandSheet);
elements.sheetBackdrop.addEventListener("click", closeCommandSheet);
elements.closeApp.addEventListener("click", () => tg?.close());

document.addEventListener("keydown", (event) => {
  if (event.key === "Escape") closeCommandSheet();
});

tg?.onEvent?.("themeChanged", applyTheme);
window.matchMedia?.("(prefers-color-scheme: dark)").addEventListener?.("change", applyTheme);

async function loadCatalog() {
  if (!isTelegramContext || !tg?.initData) {
    throw new Error("telegram_context_required");
  }

  const response = await fetch("./catalog.json", {
    cache: "no-store",
    headers: {
      "X-Telegram-Init-Data": tg.initData
    }
  });
  if (response.status === 403) {
    throw new Error("unauthorized");
  }
  if (!response.ok) {
    throw new Error("catalog_load_failed");
  }
  return response.json();
}

function renderAccessDenied(message) {
  elements.connectionStatus.textContent = "Acesso restrito";
  elements.connectionStatus.classList.remove("is-connected");
  elements.botTitle.textContent = "Acesso restrito";
  elements.botDescription.textContent = "Abra este painel pelo Telegram usando o bot em um grupo autorizado.";
  elements.commandCount.textContent = "0";
  elements.botSwitcher.innerHTML = "";
  elements.categoryTabs.innerHTML = "";
  elements.resultsCount.textContent = "0 resultados";
  elements.commandList.innerHTML = `
    <div class="empty-state">
      <div>
        <span class="empty-state__icon"><i data-lucide="lock"></i></span>
        <strong>Acesso negado</strong>
        <p>${escapeHtml(message)}</p>
      </div>
    </div>
  `;
  elements.adminNavButton.hidden = true;
  elements.adminView.hidden = true;
  elements.commandsView.hidden = false;
  refreshIcons();
}

async function initialize() {
  applyTheme();
  elements.connectionStatus.textContent = isTelegramContext
    ? "Validando acesso..."
    : "Acesso restrito";
  elements.connectionStatus.classList.toggle("is-connected", isTelegramContext);
  elements.closeApp.hidden = !isTelegramContext;

  try {
    const catalog = normalizeCatalog(await loadCatalog());
    state.authorized = true;
    elements.connectionStatus.textContent = "Conectado ao Telegram";
    elements.connectionStatus.classList.add("is-connected");
    state.bots = catalog.bots || [];
  } catch (error) {
    const messages = {
      telegram_context_required: "Este menu só funciona aberto pelo Telegram.",
      unauthorized: "Seu usuário não está em um grupo autorizado para este bot.",
      catalog_load_failed: "Não foi possível carregar o menu agora."
    };
    renderAccessDenied(messages[error.message] || messages.catalog_load_failed);
    return;
  }

  if (!state.bots.some((bot) => bot.id === state.botId)) {
    state.botId = state.bots[0]?.id || "super";
  }

  render();
}

initialize();
