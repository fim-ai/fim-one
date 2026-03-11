import { getApiBaseUrl, ACCESS_TOKEN_KEY, REFRESH_TOKEN_KEY, USER_KEY } from "./constants"
import type { UserInfo, TokenResponse, LoginRequest, LoginWithCodeRequest, RegisterRequest, ChangePasswordRequest, SetPasswordRequest, SetupRequest } from "@/types/auth"
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
  DbConnectorCreate,
  SchemaTable,
  SchemaTableUpdate,
  SchemaColumnUpdate,
  TestConnectionResponse,
  IntrospectResponse,
  QueryResponse,
  AIAnnotateResponse,
} from "@/types/connector"
import type { AdminUser, AdminConversation, AdminMessage, StorageStats, InviteCode, AdminMCPServer, IntegrationHealth, AdminModelsResponse, AdminModelCreate, AdminModelUpdate, AdminUserFile, AdminGlobalAgentInfo, AdminAllMcpServer, AdminOrganization, OrgMember } from "@/types/admin"
import type { MCPServerResponse, MCPServerCreate, MCPServerUpdate } from "@/types/mcp-server"
import type { ModelConfigResponse, ModelConfigCreate, ModelConfigUpdate } from "@/types/model_config"

// --- Auth failure callback ---
let authFailureCallback: (() => void) | null = null
let authFailureFired = false
let authFailurePending = false // fired before callback was registered (hard refresh race)

export function setAuthFailureCallback(cb: (() => void) | null) {
  authFailureCallback = cb
  if (cb === null) {
    // cleanup on unmount — reset all state
    authFailureFired = false
    authFailurePending = false
  } else {
    // new session — reset fired flag; replay if already pending
    authFailureFired = false
    if (authFailurePending) {
      authFailurePending = false
      cb()
    }
  }
}

function fireAuthFailure() {
  if (authFailureFired) return
  authFailureFired = true
  if (authFailureCallback) {
    authFailureCallback()
  } else {
    authFailurePending = true // callback not yet registered; will fire when it is
  }
}

// --- Maintenance mode callback ---
let _isMaintenance = false
let _maintenanceCallback: (() => void) | null = null

export function setMaintenanceCallback(cb: (() => void) | null) {
  _maintenanceCallback = cb
  // If 503 already fired before this callback was registered, activate immediately
  if (_isMaintenance && cb) cb()
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
  localStorage.removeItem(USER_KEY)
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
    public errorCode: string | null = null,
    public errorArgs: Record<string, unknown> = {},
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

  if (res.status === 401) {
    if (token) {
      const refreshed = await refreshAccessToken()
      if (refreshed) {
        headers["Authorization"] = `Bearer ${refreshed.access_token}`
        res = await fetch(`${getApiBaseUrl()}${path}`, { ...options, headers })
      } else {
        fireAuthFailure()
        return new Promise<T>(() => {}) // silently hang — auth callback redirects to login
      }
    } else {
      // No token at all — only redirect if not on a public page (login/setup/oauth callback)
      const isPublicPath =
        typeof window !== "undefined" &&
        ["/login", "/setup", "/auth", "/onboarding"].some((p) =>
          window.location.pathname.startsWith(p),
        )
      if (!isPublicPath) {
        fireAuthFailure()
        return new Promise<T>(() => {}) // silently hang — auth callback redirects to login
      }
    }
  }

  if (res.status === 503) {
    _isMaintenance = true
    _maintenanceCallback?.()
    return new Promise<T>(() => {}) // silently hang — maintenance overlay covers the UI
  }

  if (!res.ok) {
    const body = await res.json().catch(() => ({}))
    const detail = body.detail ?? body.error ?? res.statusText
    const message = typeof detail === "string" ? detail : JSON.stringify(detail)
    throw new ApiError(
      res.status,
      message,
      body.error_code ?? null,
      body.error_args ?? {},
    )
  }

  // 204 No Content (and any response with no body) has nothing to parse
  if (res.status === 204 || res.headers.get("content-length") === "0") {
    return undefined as unknown as T
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

  sendVerificationCode: (email: string, locale?: string) =>
    apiFetch<{ message: string; expires_in: number }>("/api/auth/send-verification-code", {
      method: "POST",
      body: JSON.stringify({ email, locale }),
    }),

  sendLoginCode: (email: string, locale?: string) =>
    apiFetch<{ message: string; expires_in: number }>("/api/auth/send-login-code", {
      method: "POST",
      body: JSON.stringify({ email, locale }),
    }),

  loginWithCode: (body: LoginWithCodeRequest) =>
    apiFetch<TokenResponse>("/api/auth/login-with-code", {
      method: "POST",
      body: JSON.stringify(body),
    }),

  refresh: (refreshToken: string) =>
    apiFetch<TokenResponse>("/api/auth/refresh", {
      method: "POST",
      body: JSON.stringify({ refresh_token: refreshToken }),
    }),

  updateProfile: (body: { system_instructions?: string | null; display_name?: string | null; preferred_language?: string | null; onboarding_completed?: boolean; avatar?: string | null; username?: string | null }) =>
    apiFetch<ApiResponse<UserInfo>>("/api/auth/profile", {
      method: "PATCH",
      body: JSON.stringify(body),
    }).then((r) => r.data),

  uploadAvatar: async (file: File): Promise<UserInfo> => {
    const token = getAccessToken()
    const formData = new FormData()
    formData.append("file", file)
    const headers: Record<string, string> = {}
    if (token) headers["Authorization"] = `Bearer ${token}`
    const res = await fetch(`${getApiBaseUrl()}/api/auth/avatar`, {
      method: "POST",
      headers,
      body: formData,
    })
    if (!res.ok) {
      const body = await res.json().catch(() => ({}))
      throw new ApiError(
        res.status,
        body.detail || res.statusText,
        body.error_code ?? null,
        body.error_args ?? {},
      )
    }
    const json: ApiResponse<UserInfo> = await res.json()
    return json.data
  },

  removeAvatar: () =>
    apiFetch<ApiResponse<UserInfo>>("/api/auth/avatar", {
      method: "DELETE",
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

  sendResetCode: (locale?: string) =>
    apiFetch<{ message: string; expires_in: number }>("/api/auth/send-reset-code", {
      method: "POST",
      body: JSON.stringify({ locale }),
    }),

  sendForgotCode: (email: string, locale?: string) =>
    apiFetch<{ message: string; expires_in: number }>("/api/auth/send-forgot-code", {
      method: "POST",
      body: JSON.stringify({ email, locale }),
    }),

  verifyForgotCode: (body: { email: string; code: string }) =>
    apiFetch<{ success: boolean; data: { reset_token: string } }>("/api/auth/verify-forgot-code", {
      method: "POST",
      body: JSON.stringify(body),
    }),

  forgotPassword: (body: { email: string; reset_token: string; new_password: string }) =>
    apiFetch<{ success: boolean; data: { message: string } }>("/api/auth/forgot-password", {
      method: "POST",
      body: JSON.stringify(body),
    }),

  resetPassword: (body: { code: string; new_password: string }) =>
    apiFetch<ApiResponse<{ message: string }>>("/api/auth/reset-password", {
      method: "POST",
      body: JSON.stringify(body),
    }).then((r) => r.data),

  unbindOAuth: (provider: string) =>
    apiFetch<ApiResponse<UserInfo>>(`/api/auth/oauth-bindings/${provider}`, {
      method: "DELETE",
    }).then((r) => r.data),

  deleteAccount: () =>
    apiFetch<{ deleted: boolean }>("/api/auth/account", {
      method: "DELETE",
    }),

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

  export: async (id: string, format: "md" | "txt" | "docx" | "pdf", detail: "full" | "summary" = "full", locale: string = "en"): Promise<void> => {
    const token = getAccessToken()
    const headers: Record<string, string> = {}
    if (token) headers["Authorization"] = `Bearer ${token}`
    const res = await fetch(
      `${getApiBaseUrl()}/api/conversations/${id}/export?format=${format}&detail=${detail}&locale=${locale}`,
      { headers },
    )
    if (!res.ok) {
      const body = await res.json().catch(() => ({}))
      throw new ApiError(res.status, body.detail || res.statusText, body.error_code ?? null, body.error_args ?? {})
    }
    const blob = await res.blob()
    const contentDisposition = res.headers.get("content-disposition")
    // Prefer RFC 5987 filename* (UTF-8 encoded) over plain filename
    const utf8Match = contentDisposition?.match(/filename\*=UTF-8''(.+?)(?:;|$)/)
    const asciiMatch = contentDisposition?.match(/filename="?([^";\n]+)"?/)
    const filename = utf8Match ? decodeURIComponent(utf8Match[1]) : asciiMatch?.[1] ?? `export.${format}`
    const url = URL.createObjectURL(blob)
    const a = document.createElement("a")
    a.href = url
    a.download = filename
    document.body.appendChild(a)
    a.click()
    document.body.removeChild(a)
    URL.revokeObjectURL(url)
  },
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
      throw new ApiError(
        res.status,
        body.detail || res.statusText,
        body.error_code ?? null,
        body.error_args ?? {},
      )
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
      throw new ApiError(
        res.status,
        body.detail || res.statusText,
        body.error_code ?? null,
        body.error_args ?? {},
      )
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

  // Database connector create
  createDbConnector: (body: DbConnectorCreate) =>
    apiFetch<ApiResponse<ConnectorResponse>>("/api/connectors", {
      method: "POST",
      body: JSON.stringify(body),
    }).then((r) => r.data),

  // Database connector endpoints
  testConnection: (connectorId: string) =>
    apiFetch<ApiResponse<TestConnectionResponse>>(
      `/api/connectors/${connectorId}/test-connection`,
      { method: "POST" },
    ).then((r) => r.data),

  testConnectionAdhoc: (dbConfig: Record<string, unknown>, connectorId?: string) =>
    apiFetch<ApiResponse<TestConnectionResponse>>(
      `/api/connectors/test-connection`,
      {
        method: "POST",
        body: JSON.stringify({
          db_config: dbConfig,
          ...(connectorId && { connector_id: connectorId }),
        }),
      },
    ).then((r) => r.data),

  introspect: (connectorId: string) =>
    apiFetch<ApiResponse<IntrospectResponse>>(
      `/api/connectors/${connectorId}/introspect`,
      { method: "POST" },
    ).then((r) => r.data),

  getSchemas: (connectorId: string) =>
    apiFetch<ApiResponse<SchemaTable[]>>(
      `/api/connectors/${connectorId}/schemas`,
    ).then((r) => r.data),

  updateSchema: (connectorId: string, schemaId: string, body: SchemaTableUpdate) =>
    apiFetch<ApiResponse<SchemaTable>>(
      `/api/connectors/${connectorId}/schemas/${schemaId}`,
      { method: "PUT", body: JSON.stringify(body) },
    ).then((r) => r.data),

  updateSchemaColumn: (
    connectorId: string,
    schemaId: string,
    columnId: string,
    body: SchemaColumnUpdate,
  ) =>
    apiFetch<ApiResponse<SchemaTable>>(
      `/api/connectors/${connectorId}/schemas/${schemaId}/columns/${columnId}`,
      { method: "PUT", body: JSON.stringify(body) },
    ).then((r) => r.data),

  bulkUpdateSchemas: (connectorId: string, body: { schemas: SchemaTableUpdate[] }) =>
    apiFetch<ApiResponse<{ updated: number }>>(
      `/api/connectors/${connectorId}/schemas/bulk`,
      { method: "PUT", body: JSON.stringify(body) },
    ).then((r) => r.data),

  executeQuery: (connectorId: string, body: { sql: string }) =>
    apiFetch<ApiResponse<QueryResponse>>(
      `/api/connectors/${connectorId}/query`,
      { method: "POST", body: JSON.stringify(body) },
    ).then((r) => r.data),

  aiAnnotate: (connectorId: string, body?: { table_ids?: string[] }) =>
    apiFetch<ApiResponse<AIAnnotateResponse>>(
      `/api/connectors/${connectorId}/ai/annotate`,
      { method: "POST", body: JSON.stringify(body || {}) },
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

// --- Admin extended types (v0.8) ---
export interface AdminLoginHistoryEntry {
  id: string; user_id: string | null; username: string | null; email: string | null
  ip_address: string | null; user_agent: string | null; success: boolean
  failure_reason: string | null; created_at: string
}
export interface AdminLoginStats {
  total_attempts: number; successful: number; failed: number
  unique_ips: number; unique_users: number; recent_failures: number
}
export interface AdminIpRule {
  id: string; ip_address: string; rule_type: string; note: string | null
  is_active: boolean; created_at: string
}
export interface AdminActiveSession {
  user_id: string; username: string | null; email: string | null
  is_admin: boolean; refresh_token_expires_at: string | null
}
export interface AdminApiKeyInfo {
  id: string; name: string; key_prefix: string; scopes: string | null
  is_active: boolean; user_id: string | null; expires_at: string | null
  last_used_at: string | null; total_requests: number; created_at: string
}
export interface AdminApiKeyCreated extends AdminApiKeyInfo {
  key: string  // raw key, only returned on creation
}
export interface AdminAgentInfo {
  id: string; name: string; description: string | null; model_name: string | null
  tools: string | null; kb_ids: string | null; enable_planning: boolean
  user_id: string; username: string | null; email: string | null; created_at: string
}
export interface AdminKBInfo {
  id: string; name: string; description: string | null; embedding_model: string | null
  chunk_size: number; document_count: number; total_chunks: number
  user_id: string; username: string | null; email: string | null; created_at: string
}
export interface AdminKBDoc {
  id: string; filename: string; file_size: number | null; chunk_count: number
  status: string; error_message: string | null; created_at: string
}
export interface AdminKBDetail extends AdminKBInfo {
  documents: AdminKBDoc[]
}
export interface AdminSensitiveWord {
  id: string; word: string; category: string
  is_active: boolean; created_at: string
}
export interface AdminUsageEntry {
  user_id: string; username: string | null; email: string | null
  total_tokens: number; conversation_count: number; token_quota: number | null
}
export interface AdminTrendEntry {
  date: string; total_tokens: number; conversation_count: number; active_users: number
}
export interface AdminAnnouncement {
  id: string; title: string; content: string; level: string; is_active: boolean
  starts_at: string | null; ends_at: string | null; target_group: string | null; created_at: string
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

  deleteUser: (userId: string) =>
    apiFetch<AdminUser>(`/api/admin/users/${userId}`, { method: "DELETE" }),

  forceLogoutAll: () =>
    apiFetch<{ invalidated: number }>("/api/admin/actions/force-logout-all", { method: "POST" }),

  connectorStats: () =>
    apiFetch<ConnectorStats>("/api/admin/connector-stats"),

  // Feature 6 -- per-user force logout
  forceLogoutUser: (userId: string) =>
    apiFetch<{ ok: boolean }>(`/api/admin/users/${userId}/force-logout`, { method: 'POST' }),

  // Feature 2 -- API health
  getSystemHealth: () =>
    apiFetch<IntegrationHealth[]>('/api/admin/system/health'),

  // Feature 1 -- token quota
  setUserQuota: (userId: string, quota: number | null) =>
    apiFetch<{ ok: boolean }>(`/api/admin/users/${userId}/quota`, {
      method: 'PATCH',
      body: JSON.stringify({ token_quota: quota }),
    }),

  // Feature 3 -- conversation moderation
  listAllConversations: (params?: { page?: number; size?: number; user_id?: string; q?: string }) => {
    const sp = new URLSearchParams()
    if (params?.page) sp.set('page', String(params.page))
    if (params?.size) sp.set('size', String(params.size))
    if (params?.user_id) sp.set('user_id', params.user_id)
    if (params?.q) sp.set('q', params.q)
    return apiFetch<{ items: AdminConversation[]; total: number; page: number; size: number }>(`/api/admin/conversations?${sp}`)
  },
  adminDeleteConversation: (convId: string) =>
    apiFetch(`/api/admin/conversations/${convId}`, { method: 'DELETE' }),
  getConversationMessages: (convId: string) =>
    apiFetch<AdminMessage[]>(`/api/admin/conversations/${convId}/messages`),

  // Feature 5 -- storage
  getStorageStats: () =>
    apiFetch<StorageStats>('/api/admin/storage'),
  clearUserStorage: (userId: string) =>
    apiFetch(`/api/admin/storage/user/${userId}`, { method: 'DELETE' }),
  cleanOrphanedStorage: () =>
    apiFetch<{ ok: boolean }>('/api/admin/storage/orphaned', { method: 'DELETE' }),

  listUserFiles: (userId: string, page = 1, size = 50) =>
    apiFetch<PaginatedResponse<AdminUserFile>>(`/api/admin/storage/user/${userId}/files?page=${page}&size=${size}`),

  downloadUserFile: async (userId: string, fileId: string, filename: string): Promise<void> => {
    const token = getAccessToken()
    const headers: Record<string, string> = {}
    if (token) headers["Authorization"] = `Bearer ${token}`
    const res = await fetch(
      `${getApiBaseUrl()}/api/admin/storage/user/${userId}/files/${fileId}`,
      { headers },
    )
    if (!res.ok) {
      const body = await res.json().catch(() => ({}))
      throw new ApiError(res.status, body.detail || res.statusText, body.error_code ?? null, body.error_args ?? {})
    }
    const blob = await res.blob()
    const url = URL.createObjectURL(blob)
    const a = document.createElement("a")
    a.href = url
    a.download = filename
    document.body.appendChild(a)
    a.click()
    document.body.removeChild(a)
    URL.revokeObjectURL(url)
  },

  // Feature 7 -- global MCP servers
  listGlobalMcpServers: () =>
    apiFetch<AdminMCPServer[]>('/api/admin/mcp-servers'),
  createGlobalMcpServer: (data: MCPServerCreate) =>
    apiFetch<AdminMCPServer>('/api/admin/mcp-servers', {
      method: 'POST',
      body: JSON.stringify(data),
    }),
  updateGlobalMcpServer: (id: string, data: MCPServerUpdate) =>
    apiFetch<AdminMCPServer>(`/api/admin/mcp-servers/${id}`, {
      method: 'PUT',
      body: JSON.stringify(data),
    }),
  deleteGlobalMcpServer: (id: string) =>
    apiFetch(`/api/admin/mcp-servers/${id}`, { method: 'DELETE' }),
  testGlobalMcpServer: (id: string) =>
    apiFetch<{ ok: boolean; tool_count?: number; error?: string }>(`/api/admin/mcp-servers/${id}/test`, { method: 'POST' }),

  // Feature 4 -- invite codes
  listInviteCodes: () =>
    apiFetch<InviteCode[]>('/api/admin/invite-codes'),
  createInviteCode: (data: { note?: string; max_uses?: number; expires_at?: string }) =>
    apiFetch<InviteCode>('/api/admin/invite-codes', {
      method: 'POST',
      body: JSON.stringify(data),
    }),
  revokeInviteCode: (id: string) =>
    apiFetch(`/api/admin/invite-codes/${id}`, { method: 'DELETE' }),

  // Admin model management
  listModels: () =>
    apiFetch<AdminModelsResponse>('/api/admin/models'),
  createModel: (data: AdminModelCreate) =>
    apiFetch<import("@/types/model_config").ModelConfigResponse>('/api/admin/models', {
      method: 'POST',
      body: JSON.stringify(data),
    }),
  updateModel: (id: string, data: AdminModelUpdate) =>
    apiFetch<import("@/types/model_config").ModelConfigResponse>(`/api/admin/models/${id}`, {
      method: 'PUT',
      body: JSON.stringify(data),
    }),
  deleteModel: (id: string) =>
    apiFetch(`/api/admin/models/${id}`, { method: 'DELETE' }),
  toggleModelActive: (id: string, is_active: boolean) =>
    apiFetch<{ ok: boolean }>(`/api/admin/models/${id}/active`, {
      method: 'PATCH',
      body: JSON.stringify({ is_active }),
    }),
  setModelRole: (id: string, role: string | null) =>
    apiFetch<{ ok: boolean }>(`/api/admin/models/${id}/role`, {
      method: 'PATCH',
      body: JSON.stringify({ role }),
    }),

  // --- Security: Login History ---
  getLoginHistory: (params?: { page?: number; size?: number; success?: boolean }) => {
    const sp = new URLSearchParams()
    if (params?.page) sp.set('page', String(params.page))
    if (params?.size) sp.set('size', String(params.size))
    if (params?.success !== undefined) sp.set('success', String(params.success))
    return apiFetch<{ items: AdminLoginHistoryEntry[]; total: number; page: number; size: number; pages: number }>(`/api/admin/login-history?${sp}`)
  },
  getLoginStats: () =>
    apiFetch<AdminLoginStats>('/api/admin/login-history/stats'),

  // --- Security: IP Rules ---
  listIpRules: () =>
    apiFetch<AdminIpRule[]>('/api/admin/ip-rules'),
  createIpRule: (data: { ip_address: string; rule_type: string; note?: string }) =>
    apiFetch<AdminIpRule>('/api/admin/ip-rules', { method: 'POST', body: JSON.stringify(data) }),
  toggleIpRule: (id: string, is_active: boolean) =>
    apiFetch<AdminIpRule>(`/api/admin/ip-rules/${id}/active`, { method: 'PATCH', body: JSON.stringify({ is_active }) }),
  deleteIpRule: (id: string) =>
    apiFetch(`/api/admin/ip-rules/${id}`, { method: 'DELETE' }),

  // --- Security: Active Sessions ---
  listActiveSessions: () =>
    apiFetch<AdminActiveSession[]>('/api/admin/sessions'),

  // --- API Keys ---
  listApiKeys: (params?: { page?: number; size?: number }) => {
    const sp = new URLSearchParams()
    if (params?.page) sp.set('page', String(params.page))
    if (params?.size) sp.set('size', String(params.size))
    return apiFetch<{ items: AdminApiKeyInfo[]; total: number; page: number; size: number; pages: number }>(`/api/admin/api-keys?${sp}`)
  },
  createApiKey: (data: { name: string; user_id?: string; scopes?: string; expires_at?: string }) =>
    apiFetch<AdminApiKeyCreated>('/api/admin/api-keys', { method: 'POST', body: JSON.stringify(data) }),
  toggleApiKey: (id: string, is_active: boolean) =>
    apiFetch<AdminApiKeyInfo>(`/api/admin/api-keys/${id}/active`, {
      method: 'PATCH',
      body: JSON.stringify({ is_active }),
    }),
  deleteApiKey: (id: string) =>
    apiFetch(`/api/admin/api-keys/${id}`, { method: 'DELETE' }),

  // --- Resources: Agents ---
  listAllAgents: (params?: { page?: number; size?: number; q?: string }) => {
    const sp = new URLSearchParams()
    if (params?.page) sp.set('page', String(params.page))
    if (params?.size) sp.set('size', String(params.size))
    if (params?.q) sp.set('q', params.q)
    return apiFetch<{ items: AdminAgentInfo[]; total: number; page: number; size: number; pages: number }>(`/api/admin/agents?${sp}`)
  },
  adminDeleteAgent: (agentId: string) =>
    apiFetch(`/api/admin/agents/${agentId}`, { method: 'DELETE' }),

  // --- Resources: Knowledge Bases ---
  listAllKBs: (params?: { page?: number; size?: number; q?: string }) => {
    const sp = new URLSearchParams()
    if (params?.page) sp.set('page', String(params.page))
    if (params?.size) sp.set('size', String(params.size))
    if (params?.q) sp.set('q', params.q)
    return apiFetch<{ items: AdminKBInfo[]; total: number; page: number; size: number; pages: number }>(`/api/admin/knowledge-bases?${sp}`)
  },
  getKBDetail: (kbId: string) =>
    apiFetch<AdminKBDetail>(`/api/admin/knowledge-bases/${kbId}`),
  adminDeleteKB: (kbId: string) =>
    apiFetch(`/api/admin/knowledge-bases/${kbId}`, { method: 'DELETE' }),

  // --- Content Moderation ---
  listSensitiveWords: (params?: { category?: string }) => {
    const sp = new URLSearchParams()
    if (params?.category) sp.set('category', params.category)
    return apiFetch<AdminSensitiveWord[]>(`/api/admin/sensitive-words?${sp}`)
  },
  createSensitiveWord: (data: { word: string; category?: string }) =>
    apiFetch<AdminSensitiveWord>('/api/admin/sensitive-words', { method: 'POST', body: JSON.stringify(data) }),
  batchImportWords: (data: { words: string[]; category?: string }) =>
    apiFetch<{ added: number }>('/api/admin/sensitive-words/batch', { method: 'POST', body: JSON.stringify(data) }),
  deleteSensitiveWord: (id: string) =>
    apiFetch(`/api/admin/sensitive-words/${id}`, { method: 'DELETE' }),
  toggleSensitiveWord: (wordId: string, isActive: boolean) =>
    apiFetch(`/api/admin/sensitive-words/${wordId}/toggle`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ is_active: isActive }),
    }),
  checkText: (data: { text: string }) =>
    apiFetch<{ matched: { word: string; category: string }[]; clean: boolean }>('/api/admin/sensitive-words/check', { method: 'POST', body: JSON.stringify(data) }),

  // --- Analytics ---
  getUsageAnalytics: (params?: { period?: string; top_n?: number }) => {
    const sp = new URLSearchParams()
    if (params?.period) sp.set('period', params.period)
    if (params?.top_n) sp.set('top_n', String(params.top_n))
    return apiFetch<AdminUsageEntry[]>(`/api/admin/analytics/usage?${sp}`)
  },
  getUsageTrends: () =>
    apiFetch<AdminTrendEntry[]>('/api/admin/analytics/trends'),

  // --- Announcements ---
  listAnnouncements: () =>
    apiFetch<AdminAnnouncement[]>('/api/admin/announcements'),
  createAnnouncement: (data: { title: string; content: string; level?: string; starts_at?: string; ends_at?: string }) =>
    apiFetch<AdminAnnouncement>('/api/admin/announcements', { method: 'POST', body: JSON.stringify(data) }),
  updateAnnouncement: (id: string, data: Partial<{ title: string; content: string; level: string; is_active: boolean; starts_at: string | null; ends_at: string | null }>) =>
    apiFetch<AdminAnnouncement>(`/api/admin/announcements/${id}`, { method: 'PUT', body: JSON.stringify(data) }),
  deleteAnnouncement: (id: string) =>
    apiFetch(`/api/admin/announcements/${id}`, { method: 'DELETE' }),

  // --- Global Agents ---
  listGlobalAgents: (page = 1, size = 20, q?: string) => {
    const sp = new URLSearchParams({ page: String(page), size: String(size) })
    if (q) sp.set('q', q)
    return apiFetch<{ items: AdminGlobalAgentInfo[]; total: number; page: number; size: number; pages: number }>(`/api/admin/global-agents?${sp}`)
  },
  cloneAgentToGlobal: (agentId: string) =>
    apiFetch<AdminGlobalAgentInfo>(`/api/admin/global-agents/clone/${agentId}`, { method: 'POST' }),
  updateGlobalAgent: (id: string, data: Record<string, unknown>) =>
    apiFetch<AdminGlobalAgentInfo>(`/api/admin/global-agents/${id}`, { method: 'PUT', body: JSON.stringify(data) }),
  deleteGlobalAgent: (id: string) =>
    apiFetch(`/api/admin/global-agents/${id}`, { method: 'DELETE' }),
  toggleGlobalAgent: (id: string) =>
    apiFetch<AdminGlobalAgentInfo>(`/api/admin/global-agents/${id}/toggle`, { method: 'POST' }),

  // --- MCP clone ---
  cloneMcpToGlobal: (serverId: string) =>
    apiFetch<AdminMCPServer>(`/api/admin/mcp-servers/clone/${serverId}`, { method: 'POST' }),
  listAllMcpServers: (page = 1, size = 20, q?: string) => {
    const sp = new URLSearchParams({ page: String(page), size: String(size) })
    if (q) sp.set('q', q)
    return apiFetch<{ items: AdminAllMcpServer[]; total: number; page: number; size: number; pages: number }>(`/api/admin/all-mcp-servers?${sp}`)
  },

  // --- Organizations (admin) ---
  listOrganizations: (page = 1, size = 20, q?: string) => {
    const sp = new URLSearchParams({ page: String(page), size: String(size) })
    if (q) sp.set('q', q)
    return apiFetch<{ items: AdminOrganization[]; total: number; page: number; size: number; pages: number }>(`/api/admin/organizations?${sp}`)
  },
  adminDeleteOrganization: (orgId: string) =>
    apiFetch(`/api/admin/organizations/${orgId}`, { method: 'DELETE' }),

  // --- Organizations (regular CRUD) ---
  createOrganization: (data: { name: string; description?: string; icon?: string }) =>
    apiFetch<AdminOrganization>('/api/orgs', { method: 'POST', body: JSON.stringify(data) }),
  updateOrganization: (orgId: string, data: { name?: string; description?: string; icon?: string }) =>
    apiFetch<AdminOrganization>(`/api/orgs/${orgId}`, { method: 'PUT', body: JSON.stringify(data) }),
  deleteOrganization: (orgId: string) =>
    apiFetch(`/api/orgs/${orgId}`, { method: 'DELETE' }),

  // --- Organization members ---
  listOrgMembers: (orgId: string) =>
    apiFetch<{ data: OrgMember[] }>(`/api/orgs/${orgId}/members`).then(r => r.data ?? []),
  addOrgMember: (orgId: string, data: { username_or_email: string; role: string }) =>
    apiFetch<OrgMember>(`/api/orgs/${orgId}/members`, { method: 'POST', body: JSON.stringify(data) }),
  updateOrgMemberRole: (orgId: string, userId: string, data: { role: string }) =>
    apiFetch<OrgMember>(`/api/orgs/${orgId}/members/${userId}`, { method: 'PATCH', body: JSON.stringify(data) }),
  removeOrgMember: (orgId: string, userId: string) =>
    apiFetch(`/api/orgs/${orgId}/members/${userId}`, { method: 'DELETE' }),
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

  setRole: (id: string, role: "general" | "fast" | null) =>
    apiFetch<ApiResponse<ModelConfigResponse>>(`/api/models/${id}`, {
      method: "PUT",
      body: JSON.stringify({ role }),
    }).then((r) => r.data),
}

// --- Organizations API (user-facing) ---
export interface UserOrg {
  id: string
  name: string
  slug: string
  description: string | null
  icon: string | null
  owner_id: string
  is_active: boolean
  member_count: number
  created_at: string
  my_role: "owner" | "admin" | "member"
}

export interface OrgMember {
  user_id: string
  username: string | null
  display_name: string | null
  email: string | null
  avatar: string | null
  role: "owner" | "admin" | "member"
  joined_at: string
}

export const orgApi = {
  list: () =>
    apiFetch<{ data: UserOrg[] }>("/api/orgs").then(r => r.data ?? []),

  create: (body: { name: string; slug: string; description?: string | null; icon?: string | null }) =>
    apiFetch<{ data: UserOrg }>("/api/orgs", {
      method: "POST",
      body: JSON.stringify(body),
    }).then(r => r.data),

  update: (orgId: string, body: { name?: string; description?: string | null; icon?: string | null }) =>
    apiFetch<{ data: UserOrg }>(`/api/orgs/${orgId}`, {
      method: "PUT",
      body: JSON.stringify(body),
    }).then(r => r.data),

  delete: (orgId: string) =>
    apiFetch<void>(`/api/orgs/${orgId}`, { method: "DELETE" }),

  listMembers: (orgId: string) =>
    apiFetch<{ data: OrgMember[] }>(`/api/orgs/${orgId}/members`).then(r => r.data ?? []),

  addMember: (orgId: string, body: { username_or_email: string; role: string }) =>
    apiFetch<{ data: OrgMember }>(`/api/orgs/${orgId}/members`, {
      method: "POST",
      body: JSON.stringify(body),
    }).then(r => r.data),

  changeRole: (orgId: string, userId: string, role: string) =>
    apiFetch<{ data: OrgMember }>(`/api/orgs/${orgId}/members/${userId}`, {
      method: "PATCH",
      body: JSON.stringify({ role }),
    }).then(r => r.data),

  removeMember: (orgId: string, userId: string) =>
    apiFetch<void>(`/api/orgs/${orgId}/members/${userId}`, { method: "DELETE" }),
}
