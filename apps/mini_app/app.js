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
  category: "Todos",
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
  deleteCommandForm: document.querySelector("#deleteCommandForm"),
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
  sheetUsage: document.querySelector("#sheetUsage"),
  sheetUsageRow: document.querySelector("#sheetUsageRow"),
  toast: document.querySelector("#toast"),
  toastMessage: document.querySelector("#toastMessage")
};

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

function commandMeta(command) {
  return categoryMeta[command.category] || { icon: "terminal", tone: "blue" };
}

function categoryGroups(commands) {
  const groups = new Map([["Todos", commands.length]]);
  commands.forEach((command) => {
    groups.set(command.category, (groups.get(command.category) || 0) + 1);
  });
  return Array.from(groups.entries());
}

function filteredCommands() {
  const bot = currentBot();
  if (!bot) return [];

  const query = state.query.trim().toLocaleLowerCase("pt-BR");
  return bot.commands.filter((command) => {
    const matchesCategory = state.category === "Todos" || command.category === state.category;
    const searchableText = [
      command.name,
      command.category,
      command.description,
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
  const groups = categoryGroups(bot?.commands || []);
  const availableCategories = groups.map(([category]) => category);

  if (!availableCategories.includes(state.category)) {
    state.category = "Todos";
  }

  elements.categoryTabs.innerHTML = groups
    .map(([category, count]) => {
      const active = category === state.category;
      return `
        <button
          class="${active ? "active" : ""}"
          data-category="${escapeHtml(category)}"
          type="button"
          aria-pressed="${active}"
        >
          ${escapeHtml(category)}
          <span>${count}</span>
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
      const meta = commandMeta(command);
      const name = escapeHtml(command.name);
      return `
        <article class="command-row">
          <span class="command-icon" data-tone="${meta.tone}" aria-hidden="true">
            <i data-lucide="${meta.icon}"></i>
          </span>
          <button
            class="command-summary"
            data-command-details="${name}"
            type="button"
            aria-label="Ver detalhes de /${name}"
          >
            <span class="command-name-row">
              <span class="command-name">/${name}</span>
              ${command.adminOnly ? '<span class="admin-badge">Admin</span>' : ""}
            </span>
            <p>${escapeHtml(command.description)}</p>
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
  state.category = "Todos";
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
  if (!["create", "delete", "backlog"].includes(mode)) return;
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
    delete_command: `Comando /${data.name} enviado para exclusão.`,
    backlog_add: "Sugestão enviada ao backlog."
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
  elements.sheetIcon.innerHTML = `<i data-lucide="${meta.icon}"></i>`;
  elements.sheetCategory.textContent = command.category;
  elements.sheetCommandName.textContent = `/${command.name}`;
  elements.sheetDescription.textContent = command.description;
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
    content: String(form.get("content") || "").trim()
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

  const catalog = await loadCatalog();
  state.bots = catalog.bots || [];

  if (!state.bots.some((bot) => bot.id === state.botId)) {
    state.botId = state.bots[0]?.id || "super";
  }

  render();
}

initialize();
