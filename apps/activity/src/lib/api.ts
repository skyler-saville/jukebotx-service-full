export type ReactionCountDTO = {
  track_id: string;
  reaction_type: string;
  count: number;
};

export type QueueItemDTO = {
  id: string;
  position: number;
  status: string;
  requested_by: number;
  created_at: string;
  updated_at: string;
  track_id: string;
  title: string | null;
  artist_display: string | null;
  image_url: string | null;
  mp3_url: string | null;
  opus_url: string | null;
};

export type NowPlayingDTO = {
  queue_item: QueueItemDTO | null;
  started_at?: string | null;
};

export type SessionStateDTO = {
  session_id: string | null;
  guild_id: number;
  channel_id: number | null;
  status: string | null;
  created_at: string | null;
  updated_at: string | null;
  ended_at: string | null;
  now_playing: NowPlayingDTO | null;
  queue: QueueItemDTO[];
  reactions: ReactionCountDTO[];
};

export type EventEnvelope<T> = {
  schema_version: string;
  event_type: string;
  data: T;
};

export type DiscordActivityExchangeRequest = {
  proof: string;
};

export type DiscordActivityExchangeResponse = {
  token: string;
  token_type: string;
  expires_in: number;
  user_id: string;
  username: string;
  guild_ids: string[];
};

export type TokenStorage = {
  getToken: () => string | null;
  setToken: (token: string) => void;
  clearToken: () => void;
};

export const createTokenStorage = (key = "jukebotx_activity_token"): TokenStorage => {
  let memoryToken: string | null = null;
  const hasStorage = typeof window !== "undefined" && typeof window.localStorage !== "undefined";

  return {
    getToken: () => {
      if (hasStorage) {
        return window.localStorage.getItem(key);
      }
      return memoryToken;
    },
    setToken: (token: string) => {
      if (hasStorage) {
        window.localStorage.setItem(key, token);
      } else {
        memoryToken = token;
      }
    },
    clearToken: () => {
      if (hasStorage) {
        window.localStorage.removeItem(key);
      }
      memoryToken = null;
    },
  };
};

export class ApiError extends Error {
  readonly status: number;
  readonly details: unknown;

  constructor(message: string, status: number, details?: unknown) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.details = details;
  }
}

export type WebSocketFactory = (
  url: string,
  options: { headers: Record<string, string> }
) => WebSocket;

export type ApiClientOptions = {
  baseUrl?: string;
  tokenStorage?: TokenStorage;
  fetcher?: typeof fetch;
};

const defaultBaseUrl =
  typeof import.meta !== "undefined" &&
  import.meta.env &&
  typeof import.meta.env.PUBLIC_API_BASE_URL === "string"
    ? import.meta.env.PUBLIC_API_BASE_URL
    : "";

const buildUrl = (baseUrl: string, path: string): string => {
  if (!baseUrl) {
    return path;
  }
  return new URL(path, baseUrl).toString();
};

const buildWebSocketUrl = (baseUrl: string, path: string): string => {
  const url = buildUrl(baseUrl, path);
  return url.replace(/^http:/, "ws:").replace(/^https:/, "wss:");
};

const withQueryParams = (path: string, params: Record<string, string | number | undefined>): string => {
  const entries = Object.entries(params).filter(([, value]) => value !== undefined);
  if (!entries.length) {
    return path;
  }
  const search = new URLSearchParams(entries.map(([key, value]) => [key, String(value)])).toString();
  return `${path}?${search}`;
};

export class ActivityApiClient {
  private readonly baseUrl: string;
  private readonly tokenStorage: TokenStorage;
  private readonly fetcher: typeof fetch;

  constructor(options: ApiClientOptions = {}) {
    this.baseUrl = options.baseUrl ?? defaultBaseUrl;
    this.tokenStorage = options.tokenStorage ?? createTokenStorage();
    this.fetcher = options.fetcher ?? fetch;
  }

  getToken(): string | null {
    return this.tokenStorage.getToken();
  }

  setToken(token: string): void {
    this.tokenStorage.setToken(token);
  }

  clearToken(): void {
    this.tokenStorage.clearToken();
  }

  async exchangeDiscordActivity(
    proof: string
  ): Promise<DiscordActivityExchangeResponse> {
    const payload: DiscordActivityExchangeRequest = { proof };
    const response = await this.request<DiscordActivityExchangeResponse>(
      "/v1/auth/discord/exchange",
      {
        method: "POST",
        body: JSON.stringify(payload),
      },
      { authorize: false }
    );
    this.setToken(response.token);
    return response;
  }

  async getActivityState(
    guildId: number,
    channelId: number,
    limit?: number
  ): Promise<EventEnvelope<SessionStateDTO>> {
    const path = withQueryParams(
      `/guilds/${guildId}/channels/${channelId}/activity/state`,
      { limit }
    );
    return this.request<EventEnvelope<SessionStateDTO>>(path, { method: "GET" });
  }

  async getActivityQueue(
    guildId: number,
    channelId: number,
    limit?: number
  ): Promise<EventEnvelope<QueueItemDTO[]>> {
    const path = withQueryParams(
      `/guilds/${guildId}/channels/${channelId}/activity/queue`,
      { limit }
    );
    return this.request<EventEnvelope<QueueItemDTO[]>>(path, { method: "GET" });
  }

  async getActivityNowPlaying(
    guildId: number,
    channelId: number
  ): Promise<EventEnvelope<NowPlayingDTO>> {
    const path = `/guilds/${guildId}/channels/${channelId}/activity/now-playing`;
    return this.request<EventEnvelope<NowPlayingDTO>>(path, { method: "GET" });
  }

  async getActivityReactions(
    guildId: number,
    channelId: number
  ): Promise<EventEnvelope<ReactionCountDTO[]>> {
    const path = `/guilds/${guildId}/channels/${channelId}/activity/reactions`;
    return this.request<EventEnvelope<ReactionCountDTO[]>>(path, { method: "GET" });
  }

  async getActiveSessionState(
    guildId: number,
    limit?: number
  ): Promise<SessionStateDTO> {
    const path = withQueryParams("/v1/sessions/active", { guild_id: guildId, limit });
    return this.request<SessionStateDTO>(path, { method: "GET" });
  }

  async getSessionState(
    sessionId: string,
    limit?: number
  ): Promise<SessionStateDTO> {
    const path = withQueryParams(`/v1/sessions/${sessionId}/state`, { limit });
    return this.request<SessionStateDTO>(path, { method: "GET" });
  }

  openSessionEvents(
    sessionId: string,
    options: {
      webSocketFactory: WebSocketFactory;
      token?: string;
      baseUrl?: string;
    }
  ): WebSocket {
    const token = options.token ?? this.getToken();
    if (!token) {
      throw new Error("Missing JWT token for session events.");
    }
    const wsBaseUrl = options.baseUrl ?? this.baseUrl;
    const wsUrl = buildWebSocketUrl(wsBaseUrl, `/v1/sessions/${sessionId}/events`);
    return options.webSocketFactory(wsUrl, {
      headers: {
        Authorization: `Bearer ${token}`,
      },
    });
  }

  private async request<T>(
    path: string,
    init: RequestInit,
    options: { authorize?: boolean } = {}
  ): Promise<T> {
    const authorize = options.authorize ?? true;
    const headers = new Headers(init.headers ?? {});

    if (authorize) {
      const token = this.getToken();
      if (!token) {
        throw new Error("Missing JWT token for authorized request.");
      }
      headers.set("Authorization", `Bearer ${token}`);
    }

    if (init.body && !headers.has("Content-Type")) {
      headers.set("Content-Type", "application/json");
    }

    const response = await this.fetcher(buildUrl(this.baseUrl, path), {
      ...init,
      headers,
    });

    if (!response.ok) {
      let details: unknown = null;
      try {
        details = await response.json();
      } catch (error) {
        details = { error: String(error) };
      }
      throw new ApiError("API request failed", response.status, details);
    }

    return (await response.json()) as T;
  }
}
