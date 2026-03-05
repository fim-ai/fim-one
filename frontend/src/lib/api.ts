import { getApiBaseUrl, ACCESS_TOKEN_KEY, REFRESH_TOKEN_KEY } from "./constants"
import type { UserInfo, TokenResponse, LoginRequest, RegisterRequest, ChangePasswordRequest, SetPasswordRequest, SetupRequest } from "@/types/auth"
import type {
  ConversationResponse,
  ConversationDetail,
  ConversationCreate,
  PaginatedResponse,
} from "@/types/conversation"
import type { AgentResponse, AgentCreate, AgentUpdate, AICreateAgentResult, AIRefineAgentResult } from "@/types/agent"
import type { FileUploadResponse, FileListItem } from "@/types/file"
import type {
  KBResponse,
  KBCreate,
  KBUpdate,
  KBDocumentResponse,
  KBRetrieveResult,
  ChunkResponse,
  PaginatedChunks,
  PaginatedDocuments,
  ChunkUpdate,
  DocumentCreate,
} from "@/types/kb"
import type {
  ConnectorResponse,
  ConnectorCreate,
  ConnectorUpdate,
  ConnectorActionCreate,
  ConnectorActionUpdate,
  ConnectorActionResponse,
  OpenAPIImportRequest,
  AIGenerateActionsRequest,
  AIRefineActionRequest,
  AIActionResult,
  AICreateConnectorResult,
} from "@/types/connector"
import type { AdminUser } from "@/types/admin"
import type { MCPServerResponse, MCPServerCreate, MCPServerUpdate } from "@/types/mcp-server"
import type { ModelConfigResponse, ModelConfigCreate, ModelConfigUpdate } from "@/types/model_config"

// --- Auth failure callback ---
let authFailureCallback: (() => void) | null = null
export function setAuthFailureCallback(cb: (() => void) | null) {
  authFailureCallback = cb
}

// --- Token helpers ---
function getAccessToken(): string | null {
  if (typeof window === "undefined") return null
  return localStorage.getItem(ACCESS_TOKEN_KEY)
}

function getRefreshToken(): string | null {
  if (typeof window === "undefined") return null
  return localStorage.getItem(REFRESH_TOKEN_KEY)
}

function clearTokens(): void {
  if (typeof window === "undefined") return
  localStorage.removeItem(ACCESS_TOKEN_KEY)
  localStorage.removeItem(REFRESH_TOKEN_KEY)
  localStorage.removeItem("fim_user")
}

// --- Token refresh (singleton) ---
let refreshPromise: Promise<TokenResponse | null> | null = null

async function refreshAccessToken(): Promise<TokenResponse | null> {
  if (refreshPromise) return refreshPromise

  refreshPromise = (async () => {
    const rt = getRefreshToken()
    if (!rt) return null
    try {
      const res = await fetch(`${getApiBaseUrl()}/api/auth/refresh`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ refresh_token: rt }),
      })
      if (!res.ok) {
        clearTokens()
        return null
      }
      const data: TokenResponse = await res.json()
      localStorage.setItem(ACCESS_TOKEN_KEY, data.access_token)
      localStorage.setItem(REFRESH_TOKEN_KEY, data.refresh_token)
      return data
    } catch {
      clearTokens()
      return null
    } finally {
      refreshPromise = null
    }
  })()

  return refreshPromise
}

// --- Core fetch ---
export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
  ) {
    super(message)
  }
}

export async function apiFetch<T>(
  path: string,
  options: RequestInit = {},
): Promise<T> {
  const token = getAccessToken()
  const headers: Record<string, string> = {
    "Content-Type": "application/json",
    ...((options.headers as Record<string, string>) || {}),
  }
  if (token) headers["Authorization"] = `Bearer ${token}`

  let res = await fetch(`${getApiBaseUrl()}${path}`, { ...options, headers })

  if (res.status === 401 && token) {
    const refreshed = await refreshAccessToken()
    if (refreshed) {
      headers["Authorization"] = `Bearer ${refreshed.access_token}`
      res = await fetch(`${getApiBaseUrl()}${path}`, { ...options, headers })
    } else {
      authFailureCallback?.()
      throw new ApiError(401, "Session expired")
    }
  }

  if (!res.ok) {
    const body = await res.json().catch(() => ({}))
    const detail = body.detail ?? body.error ?? res.statusText
    const message = typeof detail === "string" ? detail : JSON.stringify(detail)
    throw new ApiError(res.status, message)
  }

  return res.json() as Promise<T>
}

// --- Auth API ---
export const authApi = {
  login: (body: LoginRequest) =>
    apiFetch<TokenResponse>("/api/auth/login", {
      method: "POST",
      body: JSON.stringify(body),
    }),

  register: (body: RegisterRequest) =>
    apiFetch<TokenResponse>("/api/auth/register", {
      method: "POST",
      body: JSON.stringify(body),
    }),

  refresh: (refreshToken: string) =>
    apiFetch<TokenResponse>("/api/auth/refresh", {
      method: "POST",
      body: JSON.stringify({ refresh_token: refreshToken }),
    }),

  updateProfile: (body: { system_instructions?: string | null; display_name?: string | null; email?: string | null; preferred_language?: string | null }) =>
    apiFetch<ApiResponse<UserInfo>>("/api/auth/profile", {
      method: "PATCH",
      body: JSON.stringify(body),
    }).then((r) => r.data),

  changePassword: (body: ChangePasswordRequest) =>
    apiFetch<ApiResponse<{ message: string }>>("/api/auth/change-password", {
      method: "POST",
      body: JSON.stringify(body),
    }).then((r) => r.data),

  setPassword: (body: SetPasswordRequest) =>
    apiFetch<ApiResponse<UserInfo>>("/api/auth/set-password", {
      method: "POST",
      body: JSON.stringify(body),
    }).then((r) => r.data),

  unbindOAuth: (provider: string) =>
    apiFetch<ApiResponse<UserInfo>>(`/api/auth/oauth-bindings/${provider}`, {
      method: "DELETE",
    }).then((r) => r.data),

  me: () =>
    apiFetch<ApiResponse<UserInfo>>("/api/auth/me").then((r) => r.data),

  setupStatus: () =>
    apiFetch<{ initialized: boolean }>("/api/auth/setup-status"),

  setup: (body: SetupRequest) =>
    apiFetch<TokenResponse>("/api/auth/setup", {
      method: "POST",
      body: JSON.stringify(body),
    }),
}

// --- Conversation API ---
interface ApiResponse<T> {
  success: boolean
  data: T
  error: string | null
}

export const conversationApi = {
  list: (page = 1, size = 50, q?: string) => {
    let url = `/api/conversations?page=${page}&size=${size}`
    if (q) url += `&q=${encodeURIComponent(q)}`
    return apiFetch<PaginatedResponse<ConversationResponse>>(url)
  },

  create: (body: ConversationCreate) =>
    apiFetch<ApiResponse<ConversationResponse>>("/api/conversations", {
      method: "POST",
      body: JSON.stringify(body),
    }).then((r) => r.data),

  get: (id: string) =>
    apiFetch<ApiResponse<ConversationDetail>>(
      `/api/conversations/${id}`,
    ).then((r) => r.data),

  update: (id: string, body: { title?: string; status?: string; starred?: boolean }) =>
    apiFetch<ApiResponse<ConversationResponse>>(
      `/api/conversations/${id}`,
      { method: "PATCH", body: JSON.stringify(body) },
    ).then((r) => r.data),

  delete: (id: string) =>
    apiFetch<ApiResponse<{ deleted: string }>>(
      `/api/conversations/${id}`,
      { method: "DELETE" },
    ),

  batchDelete: (ids: string[]) =>
    apiFetch<ApiResponse<{ deleted: number }>>(
      `/api/conversations/batch`,
      { method: "DELETE", body: JSON.stringify({ ids }) },
    ),
}

// --- Agent API ---
export const agentApi = {
  list: (page = 1, size = 50, status?: string) => {
    let url = `/api/agents?page=${page}&size=${size}`
    if (status) url += `&status=${status}`
    return apiFetch<PaginatedResponse<AgentResponse>>(url)
  },

  create: (body: AgentCreate) =>
    apiFetch<ApiResponse<AgentResponse>>("/api/agents", {
      method: "POST",
      body: JSON.stringify(body),
    }).then((r) => r.data),

  get: (id: string) =>
    apiFetch<ApiResponse<AgentResponse>>(`/api/agents/${id}`).then((r) => r.data),

  update: (id: string, body: AgentUpdate) =>
    apiFetch<ApiResponse<AgentResponse>>(`/api/agents/${id}`, {
      method: "PUT",
      body: JSON.stringify(body),
    }).then((r) => r.data),

  delete: (id: string) =>
    apiFetch<ApiResponse<{ deleted: string }>>(`/api/agents/${id}`, {
      method: "DELETE",
    }),

  publish: (id: string) =>
    apiFetch<ApiResponse<AgentResponse>>(`/api/agents/${id}/publish`, {
      method: "POST",
    }).then((r) => r.data),

  unpublish: (id: string) =>
    apiFetch<ApiResponse<AgentResponse>>(`/api/agents/${id}/unpublish`, {
      method: "POST",
    }).then((r) => r.data),

  aiCreateAgent: (body: { instruction: string }) =>
    apiFetch<ApiResponse<AICreateAgentResult>>(`/api/agents/ai/create`, {
      method: "POST",
      body: JSON.stringify(body),
    }).then((r) => r.data),

  aiRefineAgent: (agentId: string, body: { instruction: string }) =>
    apiFetch<ApiResponse<AIRefineAgentResult>>(`/api/agents/${agentId}/ai/refine`, {
      method: "POST",
      body: JSON.stringify(body),
    }).then((r) => r.data),
}

// --- File API ---
export const fileApi = {
  upload: async (file: File): Promise<FileUploadResponse> => {
    const token = getAccessToken()
    const formData = new FormData()
    formData.append("file", file)
    const headers: Record<string, string> = {}
    if (token) headers["Authorization"] = `Bearer ${token}`
    const res = await fetch(`${getApiBaseUrl()}/api/files/upload`, {
      method: "POST",
      headers,
      body: formData,
    })
    if (!res.ok) {
      const body = await res.json().catch(() => ({}))
      throw new ApiError(res.status, body.detail || res.statusText)
    }
    const json: ApiResponse<FileUploadResponse> = await res.json()
    return json.data
  },

  list: () =>
    apiFetch<ApiResponse<FileListItem[]>>("/api/files").then((r) => r.data),

  delete: (fileId: string) =>
    apiFetch<ApiResponse<{ deleted: string }>>(`/api/files/${fileId}`, {
      method: "DELETE",
    }),
}

// --- Knowledge Base API ---
export const kbApi = {
  list: (page = 1, size = 50) =>
    apiFetch<PaginatedResponse<KBResponse>>(
      `/api/knowledge-bases?page=${page}&size=${size}`,
    ),

  create: (body: KBCreate) =>
    apiFetch<ApiResponse<KBResponse>>("/api/knowledge-bases", {
      method: "POST",
      body: JSON.stringify(body),
    }).then((r) => r.data),

  get: (id: string) =>
    apiFetch<ApiResponse<KBResponse>>(`/api/knowledge-bases/${id}`).then(
      (r) => r.data,
    ),

  update: (id: string, body: KBUpdate) =>
    apiFetch<ApiResponse<KBResponse>>(`/api/knowledge-bases/${id}`, {
      method: "PUT",
      body: JSON.stringify(body),
    }).then((r) => r.data),

  delete: (id: string) =>
    apiFetch<ApiResponse<{ deleted: string }>>(`/api/knowledge-bases/${id}`, {
      method: "DELETE",
    }),

  // Documents
  listDocuments: (kbId: string, page = 1, size = 20) =>
    apiFetch<PaginatedDocuments>(
      `/api/knowledge-bases/${kbId}/documents?page=${page}&size=${size}`,
    ),

  uploadDocument: async (kbId: string, file: File): Promise<KBDocumentResponse> => {
    const token = getAccessToken()
    const formData = new FormData()
    formData.append("file", file)
    const headers: Record<string, string> = {}
    if (token) headers["Authorization"] = `Bearer ${token}`
    const res = await fetch(
      `${getApiBaseUrl()}/api/knowledge-bases/${kbId}/documents`,
      { method: "POST", headers, body: formData },
    )
    if (!res.ok) {
      const body = await res.json().catch(() => ({}))
      throw new ApiError(res.status, body.detail || res.statusText)
    }
    const json: ApiResponse<KBDocumentResponse> = await res.json()
    return json.data
  },

  deleteDocument: (kbId: string, docId: string) =>
    apiFetch<ApiResponse<{ deleted: string }>>(
      `/api/knowledge-bases/${kbId}/documents/${docId}`,
      { method: "DELETE" },
    ),

  retryDocument: (kbId: string, docId: string) =>
    apiFetch<ApiResponse<KBDocumentResponse>>(
      `/api/knowledge-bases/${kbId}/documents/${docId}/retry`,
      { method: "POST" },
    ).then((r) => r.data),

  createDocument: (kbId: string, body: DocumentCreate) =>
    apiFetch<ApiResponse<KBDocumentResponse>>(
      `/api/knowledge-bases/${kbId}/documents/create`,
      { method: "POST", body: JSON.stringify(body) },
    ).then((r) => r.data),

  // Chunks
  listChunks: (kbId: string, docId: string, page = 1, size = 20, query = "") => {
    const params = new URLSearchParams({ page: String(page), size: String(size) })
    if (query) params.set("query", query)
    return apiFetch<PaginatedChunks>(
      `/api/knowledge-bases/${kbId}/documents/${docId}/chunks?${params}`,
    )
  },

  getChunk: (kbId: string, chunkId: string) =>
    apiFetch<ApiResponse<ChunkResponse>>(
      `/api/knowledge-bases/${kbId}/chunks/${chunkId}`,
    ).then((r) => r.data),

  updateChunk: (kbId: string, chunkId: string, body: ChunkUpdate) =>
    apiFetch<ApiResponse<ChunkResponse>>(
      `/api/knowledge-bases/${kbId}/chunks/${chunkId}`,
      { method: "PUT", body: JSON.stringify(body) },
    ).then((r) => r.data),

  deleteChunk: (kbId: string, chunkId: string) =>
    apiFetch<ApiResponse<{ deleted: string }>>(
      `/api/knowledge-bases/${kbId}/chunks/${chunkId}`,
      { method: "DELETE" },
    ).then(() => undefined),

  importUrls: (kbId: string, urls: string[]) =>
    apiFetch<ApiResponse<{ results: Array<{ url: string; status: string; doc_id?: string; error?: string }> }>>(
      `/api/knowledge-bases/${kbId}/import-urls`,
      { method: "POST", body: JSON.stringify({ urls }) },
    ).then((r) => r.data),

  // Retrieval
  retrieve: (kbId: string, query: string, topK = 5) =>
    apiFetch<ApiResponse<KBRetrieveResult[]>>(
      `/api/knowledge-bases/${kbId}/retrieve`,
      {
        method: "POST",
        body: JSON.stringify({ query, top_k: topK }),
      },
    ).then((r) => r.data),
}

// --- Connector API ---
export const connectorApi = {
  list: (page = 1, size = 50) =>
    apiFetch<PaginatedResponse<ConnectorResponse>>(
      `/api/connectors?page=${page}&size=${size}`,
    ),

  create: (body: ConnectorCreate) =>
    apiFetch<ApiResponse<ConnectorResponse>>("/api/connectors", {
      method: "POST",
      body: JSON.stringify(body),
    }).then((r) => r.data),

  get: (id: string) =>
    apiFetch<ApiResponse<ConnectorResponse>>(`/api/connectors/${id}`).then(
      (r) => r.data,
    ),

  update: (id: string, body: ConnectorUpdate) =>
    apiFetch<ApiResponse<ConnectorResponse>>(`/api/connectors/${id}`, {
      method: "PUT",
      body: JSON.stringify(body),
    }).then((r) => r.data),

  delete: (id: string) =>
    apiFetch<ApiResponse<void>>(`/api/connectors/${id}`, { method: "DELETE" }),

  // Action CRUD
  createAction: (connectorId: string, body: ConnectorActionCreate) =>
    apiFetch<ApiResponse<ConnectorActionResponse>>(
      `/api/connectors/${connectorId}/actions`,
      { method: "POST", body: JSON.stringify(body) },
    ).then((r) => r.data),

  updateAction: (
    connectorId: string,
    actionId: string,
    body: ConnectorActionUpdate,
  ) =>
    apiFetch<ApiResponse<ConnectorActionResponse>>(
      `/api/connectors/${connectorId}/actions/${actionId}`,
      { method: "PUT", body: JSON.stringify(body) },
    ).then((r) => r.data),

  deleteAction: (connectorId: string, actionId: string) =>
    apiFetch<ApiResponse<void>>(
      `/api/connectors/${connectorId}/actions/${actionId}`,
      { method: "DELETE" },
    ),

  // OpenAPI import
  importOpenAPI: (body: OpenAPIImportRequest) =>
    apiFetch<ApiResponse<ConnectorResponse>>("/api/connectors/import-openapi", {
      method: "POST",
      body: JSON.stringify(body),
    }).then((r) => r.data),

  importOpenAPIToConnector: (connectorId: string, body: OpenAPIImportRequest) =>
    apiFetch<ApiResponse<ConnectorResponse>>(
      `/api/connectors/${connectorId}/import-openapi`,
      { method: "POST", body: JSON.stringify(body) },
    ).then((r) => r.data),

  // AI action generation
  aiGenerateActions: (connectorId: string, body: AIGenerateActionsRequest) =>
    apiFetch<ApiResponse<AIActionResult>>(
      `/api/connectors/${connectorId}/ai/generate-actions`,
      { method: "POST", body: JSON.stringify(body) },
    ).then((r) => r.data),

  aiRefineAction: (connectorId: string, body: AIRefineActionRequest) =>
    apiFetch<ApiResponse<AIActionResult>>(
      `/api/connectors/${connectorId}/ai/refine-action`,
      { method: "POST", body: JSON.stringify(body) },
    ).then((r) => r.data),

  aiCreateConnector: (body: { instruction: string }) =>
    apiFetch<ApiResponse<AICreateConnectorResult>>(
      `/api/connectors/ai/create`,
      { method: "POST", body: JSON.stringify(body) },
    ).then((r) => r.data),
}

// --- Chat API ---
export const chatApi = {
  inject: (conversationId: string, content: string) =>
    apiFetch<{ success: boolean; id: string }>("/api/chat/inject", {
      method: "POST",
      body: JSON.stringify({ conversation_id: conversationId, content }),
    }),
  recallInject: (conversationId: string, injectId: string) =>
    apiFetch<{ success: boolean }>("/api/chat/inject/recall", {
      method: "POST",
      body: JSON.stringify({ conversation_id: conversationId, inject_id: injectId }),
    }),
}

// --- Admin Stats Types ---
interface ConnectorCallStat {
  connector_id: string
  connector_name: string
  call_count: number
}

interface ConnectorActionStat {
  action_name: string
  connector_name: string
  call_count: number
}

export interface ConnectorStats {
  total_calls: number
  today_calls: number
  success_rate: number
  avg_response_time_ms: number
  top_connectors: ConnectorCallStat[]
  top_actions: ConnectorActionStat[]
  recent_days: { date: string; count: number }[]
}

// --- Admin API ---
export const adminApi = {
  listUsers: (page = 1, size = 20, q?: string) => {
    let url = `/api/admin/users?page=${page}&size=${size}`
    if (q) url += `&q=${encodeURIComponent(q)}`
    return apiFetch<PaginatedResponse<AdminUser>>(url)
  },

  createUser: (body: { username: string; password: string; email?: string; display_name?: string; is_admin?: boolean }) =>
    apiFetch<AdminUser>("/api/admin/users", {
      method: "POST",
      body: JSON.stringify(body),
    }),

  updateUser: (userId: string, body: { display_name?: string | null; email?: string | null }) =>
    apiFetch<AdminUser>(`/api/admin/users/${userId}`, {
      method: "PATCH",
      body: JSON.stringify(body),
    }),

  toggleAdmin: (userId: string, isAdmin: boolean) =>
    apiFetch<AdminUser>(`/api/admin/users/${userId}/admin`, {
      method: "PATCH",
      body: JSON.stringify({ is_admin: isAdmin }),
    }),

  toggleActive: (userId: string, isActive: boolean) =>
    apiFetch<AdminUser>(`/api/admin/users/${userId}/active`, {
      method: "PATCH",
      body: JSON.stringify({ is_active: isActive }),
    }),

  resetPassword: (userId: string, newPassword: string) =>
    apiFetch<AdminUser>(`/api/admin/users/${userId}/reset-password`, {
      method: "POST",
      body: JSON.stringify({ new_password: newPassword }),
    }),

  connectorStats: () =>
    apiFetch<ConnectorStats>("/api/admin/connector-stats"),
}

// --- MCP Server API ---
export const mcpServerApi = {
  list: (page = 1, size = 50) =>
    apiFetch<PaginatedResponse<MCPServerResponse>>(
      `/api/mcp-servers?page=${page}&size=${size}`,
    ),

  create: (body: MCPServerCreate) =>
    apiFetch<ApiResponse<MCPServerResponse>>("/api/mcp-servers", {
      method: "POST",
      body: JSON.stringify(body),
    }).then((r) => r.data),

  get: (id: string) =>
    apiFetch<ApiResponse<MCPServerResponse>>(`/api/mcp-servers/${id}`).then((r) => r.data),

  update: (id: string, body: MCPServerUpdate) =>
    apiFetch<ApiResponse<MCPServerResponse>>(`/api/mcp-servers/${id}`, {
      method: "PUT",
      body: JSON.stringify(body),
    }).then((r) => r.data),

  delete: (id: string) =>
    apiFetch<ApiResponse<void>>(`/api/mcp-servers/${id}`, { method: "DELETE" }),

  toggleActive: (id: string, isActive: boolean) =>
    apiFetch<ApiResponse<MCPServerResponse>>(`/api/mcp-servers/${id}`, {
      method: "PUT",
      body: JSON.stringify({ is_active: isActive }),
    }).then((r) => r.data),

  capabilities: () =>
    apiFetch<{ allow_stdio: boolean }>("/api/mcp-servers/capabilities"),

  test: (id: string) =>
    apiFetch<ApiResponse<{ ok: boolean; tool_count?: number; tools?: string[]; error?: string }>>(
      `/api/mcp-servers/${id}/test`,
      { method: "POST" },
    ).then((r) => r.data),
}

// --- Model Config API ---
export const modelApi = {
  list: (category?: string) => {
    const url = category ? `/api/models?category=${category}` : "/api/models"
    return apiFetch<ApiResponse<ModelConfigResponse[]>>(url).then((r) => r.data)
  },

  get: (id: string) =>
    apiFetch<ApiResponse<ModelConfigResponse>>(`/api/models/${id}`).then((r) => r.data),

  create: (body: ModelConfigCreate) =>
    apiFetch<ApiResponse<ModelConfigResponse>>("/api/models", {
      method: "POST",
      body: JSON.stringify(body),
    }).then((r) => r.data),

  update: (id: string, body: ModelConfigUpdate) =>
    apiFetch<ApiResponse<ModelConfigResponse>>(`/api/models/${id}`, {
      method: "PUT",
      body: JSON.stringify(body),
    }).then((r) => r.data),

  delete: (id: string) =>
    apiFetch<ApiResponse<void>>(`/api/models/${id}`, { method: "DELETE" }),

  setDefault: (id: string) =>
    apiFetch<ApiResponse<ModelConfigResponse>>(`/api/models/${id}`, {
      method: "PUT",
      body: JSON.stringify({ is_default: true }),
    }).then((r) => r.data),
}
