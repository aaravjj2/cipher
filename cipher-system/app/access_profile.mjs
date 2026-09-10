const DEVELOPER_ROLE = "developer";

const MEMBER_CAPABILITIES = Object.freeze({
  marketData: true,
  research: true,
  savedWorkspace: true,
  providerConnection: true,
  developerTools: false,
  liveOrders: false,
});

const DEVELOPER_CAPABILITIES = Object.freeze({
  ...MEMBER_CAPABILITIES,
  developerTools: true,
});

const GUEST_CAPABILITIES = Object.freeze({
  marketData: true,
  research: true,
  savedWorkspace: false,
  providerConnection: false,
  developerTools: false,
  liveOrders: false,
});

function list(value) {
  return new Set(String(value || "").split(",").map((item) => item.trim().toLowerCase()).filter(Boolean));
}

function safeSettings(value) {
  if (!value || typeof value !== "object" || Array.isArray(value)) return {};
  const result = {};
  if (typeof value.display_name === "string") result.displayName = value.display_name.slice(0, 80);
  if (typeof value.default_ticker === "string" && /^[A-Z.]{1,10}$/i.test(value.default_ticker)) {
    result.defaultTicker = value.default_ticker.toUpperCase();
  }
  if (typeof value.default_panel === "string") result.defaultPanel = value.default_panel.slice(0, 80);
  return result;
}

export function createAccessProfileResolver({ developerUserIds = "", developerEmails = "" } = {}) {
  const ids = list(developerUserIds);
  const emails = list(developerEmails);

  function authenticated(user, databaseAccess = null) {
    const userId = String(user?.userId || "").trim();
    const email = String(user?.email || "").trim().toLowerCase();
    const metadataRole = String(user?.appMetadata?.cipher_role || "").trim().toLowerCase();
    const databaseRole = String(databaseAccess?.role || "").trim().toLowerCase();
    const developer = metadataRole === DEVELOPER_ROLE
      || databaseRole === DEVELOPER_ROLE
      || ids.has(userId.toLowerCase())
      || (email && emails.has(email));
    const role = developer ? DEVELOPER_ROLE : "member";
    return {
      mode: role,
      role,
      capabilities: developer ? DEVELOPER_CAPABILITIES : MEMBER_CAPABILITIES,
      settings: developer ? safeSettings(databaseAccess?.developer_settings) : {},
    };
  }

  function guest() {
    return { mode: "guest", role: "guest", capabilities: GUEST_CAPABILITIES, settings: {} };
  }

  function operator() {
    return {
      mode: DEVELOPER_ROLE,
      role: DEVELOPER_ROLE,
      capabilities: DEVELOPER_CAPABILITIES,
      settings: { displayName: "Host", defaultPanel: "Operator Status" },
    };
  }

  return { authenticated, guest, operator };
}
