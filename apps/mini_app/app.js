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
  selectedCommand: null
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
  sheetCommandName: document.querySelector("#sheetCommandName"),
  sheetDescription: document.querySelector("#sheetDescription"),
  sheetExecute: document.querySelector("#sheetExecute"),
  sheetIcon: document.querySelector("#sheetIcon"),
  sheetPreview: document.querySelector("#sheetPreview"),
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
          <button
            class="run-command"
            data-command="${name}"
            type="button"
            title="Executar /${name}"
            aria-label="Executar /${name}"
          >
            <i data-lucide="send"></i>
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

function actionMessage(kind, data) {
  if (kind === "execute_command") {
    const bot = state.bots.find((item) => item.id === data.bot);
    return `/${data.command} enviado ao ${bot?.name || "bot"}.`;
  }

  const labels = {
    "/create": "Fluxo de criação solicitado.",
    "/list": "Lista de comandos solicitada.",
    "/gifstats": "Estatísticas de GIFs solicitadas.",
    "/backlog": "Backlog solicitado.",
    create_text_command: `Comando /${data.name} enviado para criação.`,
    update_command: `Alteração de /${data.name} enviada.`,
    delete_command: `Comando /${data.name} enviado para exclusão.`,
    backlog_add: "Sugestão enviada ao backlog.",
    create_category: `Categoria ${data.name} enviada para criação.`,
    update_category: `Categoria ${data.name} enviada para alteração.`,
    delete_category: `Categoria ${data.name} enviada para exclusão.`
  };
  return labels[data.action] || "Ação enviada ao bot.";
}

function sendPayload(kind, data = {}) {
  const payload = JSON.stringify({ kind, data });
  tg?.HapticFeedback?.impactOccurred("light");

  if (isTelegramContext && tg?.sendData) {
    try {
      tg.sendData(payload);
      showToast(actionMessage(kind, data));
      return true;
    } catch {
      showToast("Não foi possível enviar agora. Tente novamente.");
      return false;
    }
  }

  console.info("[FMCPT Mini App]", payload);
  showToast(`Demonstração: ${actionMessage(kind, data)}`);
  return true;
}

function findCommand(name) {
  return currentBot()?.commands.find((command) => command.name === name);
}

function openCommandSheet(command) {
  if (!command) return;

  const meta = commandMeta(command);
  state.selectedCommand = command;
  lastFocusedElement = document.activeElement;

  elements.sheetIcon.dataset.tone = meta.tone;
  elements.sheetIcon.outerHTML = renderCommandIcon(command, "large").replace("command-icon--media", "command-icon--media").replace("<span", '<span id="sheetIcon"');
  elements.sheetIcon = document.querySelector("#sheetIcon");
  const previewHtml = renderSheetPreview(command);
  elements.sheetPreview.innerHTML = previewHtml;
  elements.sheetPreview.hidden = !previewHtml;
  elements.sheetCategory.textContent = commandCategory(command);
  elements.sheetCommandName.textContent = `/${command.name}`;
  elements.sheetDescription.textContent = commandDescription(command);
  elements.sheetUsage.textContent = command.usage || `/${command.name}`;
  elements.sheetAliases.textContent = (command.aliases || []).map((alias) => `/${alias}`).join(", ");
  elements.sheetAccess.textContent = command.adminOnly ? "Somente administradores" : "Todos do grupo";
  elements.sheetUsageRow.hidden = false;
  elements.sheetAliasesRow.hidden = !(command.aliases || []).length;
  elements.sheetExecute.dataset.command = command.name;

  elements.sheetBackdrop.hidden = false;
  elements.commandSheet.hidden = false;
  document.body.classList.add("sheet-open");
  refreshIcons();
  window.requestAnimationFrame(() => elements.closeSheet.focus());
}

function closeCommandSheet() {
  if (elements.commandSheet.hidden) return;
  elements.sheetBackdrop.hidden = true;
  elements.commandSheet.hidden = true;
  document.body.classList.remove("sheet-open");
  state.selectedCommand = null;
  lastFocusedElement?.focus?.();
}

function executeCommand(commandName) {
  closeCommandSheet();
  sendPayload("execute_command", {
    bot: state.botId,
    command: commandName
  });
}

function normalizeCommandName(value) {
  return String(value || "").trim().replace(/^\/+/, "").toLowerCase();
}

function sendAdminShortcut(action) {
  sendPayload("admin_action", { action });
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
  const executeButton = event.target.closest("button[data-command]");
  if (executeButton) {
    executeCommand(executeButton.dataset.command);
    return;
  }

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

elements.createCommandForm.addEventListener("submit", (event) => {
  event.preventDefault();
  const form = new FormData(event.currentTarget);
  const name = normalizeCommandName(form.get("name"));

  if (!/^[a-z0-9_]{1,32}$/.test(name)) {
    showToast("Use até 32 letras, números ou sublinhados no nome.");
    return;
  }

  sendPayload("admin_action", {
    action: "create_text_command",
    name,
    description: String(form.get("description") || "").trim(),
    category: String(form.get("category") || "").trim(),
    type: String(form.get("type") || "texto"),
    content: String(form.get("content") || "").trim(),
    previewUrl: String(form.get("previewUrl") || "").trim()
  });
  event.currentTarget.reset();
});

elements.updateCommandForm.addEventListener("submit", (event) => {
  event.preventDefault();
  const form = new FormData(event.currentTarget);
  const name = normalizeCommandName(form.get("name"));

  if (!/^[a-z0-9_]{1,32}$/.test(name)) {
    showToast("Informe um nome de comando válido.");
    return;
  }

  sendPayload("admin_action", {
    action: "update_command",
    name,
    description: String(form.get("description") || "").trim(),
    category: String(form.get("category") || "").trim(),
    type: String(form.get("type") || "").trim(),
    content: String(form.get("content") || "").trim(),
    previewUrl: String(form.get("previewUrl") || "").trim()
  });
  event.currentTarget.reset();
});

elements.deleteCommandForm.addEventListener("submit", (event) => {
  event.preventDefault();
  const form = new FormData(event.currentTarget);
  const name = normalizeCommandName(form.get("name"));

  if (!/^[a-z0-9_]{1,32}$/.test(name)) {
    showToast("Informe um nome de comando válido.");
    return;
  }

  sendPayload("admin_action", {
    action: "delete_command",
    name
  });
  event.currentTarget.reset();
});

elements.categoryForm.addEventListener("submit", (event) => {
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

  sendPayload("admin_action", { action, name, newName });
  event.currentTarget.reset();
});

elements.backlogForm.addEventListener("submit", (event) => {
  event.preventDefault();
  const form = new FormData(event.currentTarget);
  const text = String(form.get("text") || "").trim();
  if (!text) return;

  sendPayload("admin_action", {
    action: "backlog_add",
    text
  });
  event.currentTarget.reset();
});

elements.sheetExecute.addEventListener("click", () => {
  if (state.selectedCommand) executeCommand(state.selectedCommand.name);
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
  if (window.FMCPT_CATALOG?.bots?.length) {
    return window.FMCPT_CATALOG;
  }

  if (window.location.protocol !== "file:") {
    try {
      const response = await fetch("./catalog.json", { cache: "no-store" });
      if (response.ok) return await response.json();
    } catch {
      // The embedded catalog keeps the interface usable when the request fails.
    }
  }

  return demoCatalog;
}

async function initialize() {
  applyTheme();
  elements.connectionStatus.textContent = isTelegramContext
    ? "Conectado ao Telegram"
    : "Modo demonstração";
  elements.connectionStatus.classList.toggle("is-connected", isTelegramContext);
  elements.closeApp.hidden = !isTelegramContext;

  const catalog = normalizeCatalog(await loadCatalog());
  state.bots = catalog.bots || [];

  if (!state.bots.some((bot) => bot.id === state.botId)) {
    state.botId = state.bots[0]?.id || "super";
  }

  render();
}

initialize();
