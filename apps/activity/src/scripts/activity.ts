import { DiscordSDK } from "@discord/embedded-app-sdk";
import { ActivityApiClient } from "../lib/api";

const discordSdk = new DiscordSDK(import.meta.env.PUBLIC_ACTIVITY_CLIENT_ID);
const apiClient = new ActivityApiClient({
  baseUrl: import.meta.env.PUBLIC_API_BASE_URL,
});

const nowPlayingSection = document.querySelector("#now-playing");
const nowPlayingTitle = nowPlayingSection?.querySelector(".track-title");
const nowPlayingMeta = nowPlayingSection?.querySelector(".track-meta");
const nowPlayingArtwork = nowPlayingSection?.querySelector(".artwork");
const queueList = document.querySelector("#up-next .queue");
const connectButton = document.querySelector("#connect-discord");
const authStatus = document.querySelector("#auth-status");
const diagnosticsPanel = document.querySelector("#diagnostics");
const diagnosticsOutput = document.querySelector("#diagnostics-output");
let isAuthenticating = false;

async function setupDiscordSdk() {
  if (isAuthenticating) return;
  isAuthenticating = true;
  setAuthStatus("Connecting to Discord...");

  await discordSdk.ready();
  setDiagnostics("discordSdk.ready() resolved");

  const { code } = await discordSdk.commands.authorize({
    client_id: import.meta.env.PUBLIC_ACTIVITY_CLIENT_ID,
    response_type: "code",
    state: "",
    prompt: "none",
    scope: ["identify", "guilds", "applications.commands"],
  });
  setDiagnostics("authorize() ok");

  const exchange = await apiClient.exchangeDiscordActivity(code);
  setDiagnostics("exchangeDiscordActivity() ok");
  const auth = await discordSdk.commands.authenticate({
    access_token: exchange.access_token,
  });
  setDiagnostics("authenticate() ok");

  if (auth == null) {
    setAuthStatus("Discord authentication failed.");
    setDiagnostics("authenticate() returned null");
    throw new Error("Authenticate command failed");
  }

  const guildId = Number(discordSdk.guildId);
  const channelId = Number(discordSdk.channelId);
  if (!Number.isFinite(guildId) || !Number.isFinite(channelId)) {
    setAuthStatus("Missing Discord context.");
    setDiagnostics("Missing guildId or channelId");
    throw new Error("Missing guild or channel context from Discord SDK.");
  }

  setAuthStatus(`Connected to guild ${guildId}, channel ${channelId}.`);
  setDiagnostics(`Connected: guild=${guildId}, channel=${channelId}`);
  await refreshActivity(guildId, channelId);
  setInterval(() => refreshActivity(guildId, channelId), 5000);

  console.log("Discord SDK is ready");
}

function renderNowPlaying(queueItem: {
  title: string | null;
  artist_display: string | null;
  image_url: string | null;
} | null) {
  if (!nowPlayingTitle || !nowPlayingMeta || !nowPlayingArtwork) return;
  if (!queueItem) {
    nowPlayingTitle.textContent = "Nothing playing yet";
    nowPlayingMeta.textContent = "The queue is currently idle.";
    nowPlayingArtwork.style.backgroundImage = "none";
    return;
  }
  nowPlayingTitle.textContent = queueItem.title || "Untitled track";
  nowPlayingMeta.textContent = queueItem.artist_display || "Unknown artist";
  if (queueItem.image_url) {
    nowPlayingArtwork.style.backgroundImage = `url("${queueItem.image_url}")`;
  } else {
    nowPlayingArtwork.style.backgroundImage = "none";
  }
}

function renderQueue(
  items: Array<{ title: string | null; artist_display: string | null }>
) {
  if (!queueList) return;
  queueList.innerHTML = "";
  if (!items.length) {
    queueList.innerHTML = '<li class="muted">Queue is empty.</li>';
    return;
  }
  for (const item of items) {
    const li = document.createElement("li");
    li.innerHTML = `
      <span class="queue-title">${item.title || "Untitled track"}</span>
      <span class="queue-meta">${item.artist_display || "Unknown artist"}</span>
    `;
    queueList.appendChild(li);
  }
}

async function refreshActivity(guildId: number, channelId: number) {
  try {
    const [nowPlaying, queue] = await Promise.all([
      apiClient.getActivityNowPlaying(guildId, channelId),
      apiClient.getActivityQueue(guildId, channelId, 10),
    ]);
    renderNowPlaying(nowPlaying.data?.queue_item ?? null);
    renderQueue(queue.data ?? []);
    setDiagnostics("Activity data refreshed");
  } catch (error) {
    console.error("Failed to refresh activity", error);
    setDiagnostics(`Failed to refresh activity: ${stringifyError(error)}`);
  }
}

function setAuthStatus(message: string) {
  if (authStatus) {
    authStatus.textContent = message;
  }
}

function setDiagnostics(message: string) {
  if (diagnosticsOutput) {
    diagnosticsOutput.textContent = message;
  }
  if (diagnosticsPanel) {
    diagnosticsPanel.removeAttribute("hidden");
  }
}

function stringifyError(error: unknown): string {
  if (error instanceof Error) {
    return `${error.name}: ${error.message}`;
  }
  try {
    return JSON.stringify(error);
  } catch {
    return String(error);
  }
}

connectButton?.addEventListener("click", () => {
  setupDiscordSdk().catch((error) => {
    console.error("Discord connection failed", error);
    setAuthStatus("Discord connection failed. Check console for details.");
    setDiagnostics(`Discord connection failed: ${stringifyError(error)}`);
  });
});

setupDiscordSdk().catch((error) => {
  console.error("Discord connection failed", error);
  setAuthStatus("Discord connection failed. Check console for details.");
  setDiagnostics(`Discord connection failed: ${stringifyError(error)}`);
});
