import { getApiBaseUrl, ACCESS_TOKEN_KEY, REFRESH_TOKEN_KEY, USER_KEY, MARKET_ORG_ID } from "./constants"
import type { UserInfo, TokenResponse, LoginRequest, LoginWithCodeRequest, RegisterRequest, ChangePasswordRequest, SetPasswordRequest, SetupRequest } from "@/types/auth"
import type { WorkflowResponse, WorkflowCreate, WorkflowUpdate, WorkflowRunResponse, WorkflowStats, WorkflowTemplate, NodeStatsResponse, WorkflowValidateResponse, WorkflowVersionResponse, WorkflowAnalyticsResponse, WorkflowScheduleResponse, WorkflowScheduleUpdate, WorkflowBatchRunResponse, WorkflowImportResult, NodeTestRequest, NodeTestResponse } from "@/types/workflow"
import type {
  ConversationResponse,
  ConversationDetail,
  ConversationCreate,
  PaginatedResponse,
} from "@/types/conversation"
import type { AgentResponse, AgentCreate, AgentUpdate, AgentTemplate, AICreateAgentResult, AIRefineAgentResult } from "@/types/agent"
import type { SkillResponse, SkillCreate, SkillUpdate, SkillTemplate } from "@/types/skill"
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
  AIAnnotateJobStarted,
  AIAnnotateJobStatus,
  CredentialUpsertRequest,
  MyCredentialStatus,
  ConnectorTemplate,
} from "@/types/connector"
import type { AdminUser, AdminConversation, AdminMessage, StorageStats, InviteCode, IntegrationHealth, AdminModelsResponse, AdminModelCreate, AdminModelUpdate, AdminUserFile, AdminOrganization, OrgMember as AdminOrgMember, ReviewLogItem } from "@/types/admin"
import type { MCPServerResponse, MCPServerCreate, MCPServerUpdate, MCPMyCredentialStatus } from "@/types/mcp-server"
import type { ModelConfigResponse, ModelConfigCreate, ModelConfigUpdate } from "@/types/model_config"
import type {
  EvalDatasetResponse,
  EvalDatasetCreate,
  EvalDatasetUpdate,
  EvalCaseResponse,
  EvalCaseCreate,
  EvalCaseUpdate,
  EvalRunResponse,
  EvalRunCreate,
  EvalRunDetailResponse,
} from "@/types/eval"
import type {
  ModelProvidersListResponse,
  ModelProviderResponse,
  ModelProviderCreate,
  ModelProviderUpdate,
  ModelProviderModelResponse,
  ModelProviderModelCreate,
  ModelProviderModelUpdate,
  ModelGroupsListResponse,
  ModelGroupResponse,
  ModelGroupCreate,
  ModelGroupUpdate,
  ModelActiveConfig,
} from "@/types/model_provider"

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
    // Pydantic validation errors come back as an array of {loc, msg, type} objects.
    // Extract the human-readable `msg` fields instead of dumping raw JSON.
    const message =
      typeof detail === "string"
        ? detail
        : Array.isArray(detail)
          ? detail.map((e: { msg?: string; loc?: string[] }) => e.msg ?? JSON.stringify(e)).join("; ")
          : JSON.stringify(detail)
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

  verify2fa: (body: { temp_token: string; code: string }) =>
    apiFetch<TokenResponse>("/api/auth/login/verify-2fa", {
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

  publish: (id: string, body: { scope: "personal" | "org" | "global"; org_id?: string }) =>
    apiFetch<ApiResponse<AgentResponse>>(`/api/agents/${id}/publish`, {
      method: "POST",
      body: JSON.stringify(body),
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

  aiRefineAgent: (agentId: string, body: { instruction: string; history?: Array<{ role: string; content: string }> }) =>
    apiFetch<ApiResponse<AIRefineAgentResult>>(`/api/agents/${agentId}/ai/refine`, {
      method: "POST",
      body: JSON.stringify(body),
    }).then((r) => r.data),

  resubmit: (id: string) =>
    apiFetch<ApiResponse<AgentResponse>>(`/api/agents/${id}/resubmit`, {
      method: "POST",
    }).then((r) => r.data),

  toggleActive: (id: string, isActive: boolean) =>
    apiFetch<ApiResponse<AgentResponse>>(`/api/agents/${id}`, {
      method: "PUT",
      body: JSON.stringify({ is_active: isActive }),
    }).then((r) => r.data),

  getTemplates: () =>
    apiFetch<ApiResponse<{ templates: AgentTemplate[]; by_category: Record<string, AgentTemplate[]> }>>(
      "/api/agent-templates",
    ).then((r) => r.data.templates),

  createFromTemplate: (templateId: string) =>
    apiFetch<ApiResponse<AgentResponse>>(`/api/agent-templates/${templateId}/create`, {
      method: "POST",
    }).then((r) => r.data),

  forkAgent: (id: string, name?: string) =>
    apiFetch<ApiResponse<AgentResponse>>(`/api/agents/${id}/fork`, {
      method: "POST",
      body: JSON.stringify(name ? { name } : {}),
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

  getContent: (fileId: string, offset?: number, limit?: number) => {
    const params = new URLSearchParams()
    if (offset !== undefined) params.set("offset", String(offset))
    if (limit !== undefined) params.set("limit", String(limit))
    const query = params.toString()
    return apiFetch<ApiResponse<{ content: string; total_length: number; offset: number; returned_length: number }>>(
      `/api/files/${fileId}/content${query ? `?${query}` : ""}`,
    ).then((r) => r.data)
  },
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

  // AI Chat
  aiChat: (kbId: string, message: string, history?: Array<{ role: string; content: string }>) =>
    apiFetch<{ ok: boolean; message: string; action: string }>(
      `/api/knowledge-bases/${kbId}/ai/chat`,
      {
        method: "POST",
        body: JSON.stringify({ message, history: history ?? [] }),
      },
    ),

  publish: (id: string, body: { scope: string; org_id?: string }) =>
    apiFetch<ApiResponse<KBResponse>>(`/api/knowledge-bases/${id}/publish`, {
      method: "POST",
      body: JSON.stringify(body),
    }).then((r) => r.data),

  unpublish: (id: string) =>
    apiFetch<ApiResponse<KBResponse>>(`/api/knowledge-bases/${id}/unpublish`, {
      method: "POST",
    }).then((r) => r.data),

  resubmit: (id: string) =>
    apiFetch<ApiResponse<KBResponse>>(`/api/knowledge-bases/${id}/resubmit`, {
      method: "POST",
    }).then((r) => r.data),

  toggleActive: (id: string, isActive: boolean) =>
    apiFetch<ApiResponse<KBResponse>>(`/api/knowledge-bases/${id}`, {
      method: "PUT",
      body: JSON.stringify({ is_active: isActive }),
    }).then((r) => r.data),
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

  aiRefineAction: (connectorId: string, body: AIRefineActionRequest & { history?: Array<{ role: string; content: string }> }) =>
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

  // Single-table annotate (sync, fast)
  aiAnnotate: (connectorId: string, body: { table_ids: string[] }) =>
    apiFetch<ApiResponse<AIAnnotateResponse>>(
      `/api/connectors/${connectorId}/ai/annotate`,
      { method: "POST", body: JSON.stringify(body) },
    ).then((r) => r.data),

  // Full-schema annotate — returns job_id immediately, runs in background
  aiAnnotateAll: (connectorId: string) =>
    apiFetch<ApiResponse<AIAnnotateJobStarted>>(
      `/api/connectors/${connectorId}/ai/annotate`,
      { method: "POST", body: JSON.stringify({}) },
    ).then((r) => r.data),

  // Poll job status
  getAnnotateStatus: (connectorId: string, jobId: string) =>
    apiFetch<ApiResponse<AIAnnotateJobStatus>>(
      `/api/connectors/${connectorId}/ai/annotate/status/${jobId}`,
    ).then((r) => r.data),

  aiDbChat: (connectorId: string, message: string, history?: Array<{ role: string; content: string }>) =>
    apiFetch<{ ok: boolean; message: string; changes: number; connector: ConnectorResponse | null }>(
      `/api/connectors/${connectorId}/ai/db-chat`,
      { method: "POST", body: JSON.stringify({ message, history: history ?? [] }) },
    ),

  // Per-user credential overrides
  getMyCredentials: (connectorId: string) =>
    apiFetch<ApiResponse<MyCredentialStatus>>(
      `/api/connectors/${connectorId}/my-credentials`,
    ).then((r) => r.data),

  upsertMyCredentials: (connectorId: string, body: CredentialUpsertRequest) =>
    apiFetch<ApiResponse<void>>(
      `/api/connectors/${connectorId}/my-credentials`,
      { method: "PUT", body: JSON.stringify(body) },
    ).then(() => undefined),

  deleteMyCredentials: (connectorId: string) =>
    apiFetch<ApiResponse<void>>(
      `/api/connectors/${connectorId}/my-credentials`,
      { method: "DELETE" },
    ).then(() => undefined),

  // Publish / Unpublish
  publish: (id: string, body: { scope: string; org_id?: string; allow_fallback?: boolean }) =>
    apiFetch<ApiResponse<ConnectorResponse>>(`/api/connectors/${id}/publish`, {
      method: "POST",
      body: JSON.stringify(body),
    }).then((r) => r.data),

  unpublish: (id: string) =>
    apiFetch<ApiResponse<ConnectorResponse>>(`/api/connectors/${id}/unpublish`, {
      method: "POST",
    }).then((r) => r.data),

  resubmit: (id: string) =>
    apiFetch<ApiResponse<ConnectorResponse>>(`/api/connectors/${id}/publish`, {
      method: "POST",
      body: JSON.stringify({ scope: "org", resubmit: true }),
    }).then((r) => r.data),

  toggleActive: (id: string, isActive: boolean) =>
    apiFetch<ApiResponse<ConnectorResponse>>(`/api/connectors/${id}`, {
      method: "PUT",
      body: JSON.stringify({ is_active: isActive }),
    }).then((r) => r.data),

  // Export / Import / Fork
  exportConnector: (id: string) =>
    apiFetch<ApiResponse<Record<string, unknown>>>(`/api/connectors/${id}/export`).then(
      (r) => r.data,
    ),

  importConnector: (data: unknown) =>
    apiFetch<ApiResponse<{ connector: ConnectorResponse; warnings: string[] }>>(
      "/api/connectors/import",
      { method: "POST", body: JSON.stringify(data) },
    ).then((r) => r.data),

  forkConnector: (id: string, name?: string) =>
    apiFetch<ApiResponse<ConnectorResponse>>(`/api/connectors/${id}/fork`, {
      method: "POST",
      body: JSON.stringify(name ? { name } : {}),
    }).then((r) => r.data),

  // Templates
  getTemplates: () =>
    apiFetch<ApiResponse<ConnectorTemplate[]>>("/api/connector-templates").then(
      (r) => r.data,
    ),

  createFromTemplate: (templateId: string) =>
    apiFetch<ApiResponse<ConnectorResponse>>(
      `/api/connector-templates/${templateId}/create`,
      { method: "POST" },
    ).then((r) => r.data),
}

// --- Workflow API ---
export const workflowApi = {
  list: (page = 1, size = 50) =>
    apiFetch<PaginatedResponse<WorkflowResponse>>(
      `/api/workflows?page=${page}&size=${size}`,
    ),

  get: (id: string) =>
    apiFetch<ApiResponse<WorkflowResponse>>(`/api/workflows/${id}`).then((r) => r.data),

  create: (body: WorkflowCreate) =>
    apiFetch<ApiResponse<WorkflowResponse>>("/api/workflows", {
      method: "POST",
      body: JSON.stringify(body),
    }).then((r) => r.data),

  update: (id: string, body: WorkflowUpdate) =>
    apiFetch<ApiResponse<WorkflowResponse>>(`/api/workflows/${id}`, {
      method: "PUT",
      body: JSON.stringify(body),
    }).then((r) => r.data),

  delete: (id: string) =>
    apiFetch<ApiResponse<{ deleted: string }>>(`/api/workflows/${id}`, {
      method: "DELETE",
    }),

  getRuns: (workflowId: string, page = 1, size = 20, status?: string) => {
    const params = new URLSearchParams({ page: String(page), size: String(size) })
    if (status) params.set("status", status)
    return apiFetch<PaginatedResponse<WorkflowRunResponse>>(
      `/api/workflows/${workflowId}/runs?${params.toString()}`,
    )
  },

  getRun: (workflowId: string, runId: string) =>
    apiFetch<ApiResponse<WorkflowRunResponse>>(
      `/api/workflows/${workflowId}/runs/${runId}`,
    ).then((r) => r.data),

  cancelRun: (workflowId: string, runId: string) =>
    apiFetch<ApiResponse<WorkflowRunResponse>>(
      `/api/workflows/${workflowId}/runs/${runId}/cancel`,
      { method: "POST" },
    ).then((r) => r.data),

  deleteRun: (workflowId: string, runId: string) =>
    apiFetch<ApiResponse<{ deleted: string }>>(
      `/api/workflows/${workflowId}/runs/${runId}`,
      { method: "DELETE" },
    ),

  clearRuns: (workflowId: string) =>
    apiFetch<ApiResponse<{ deleted_count: number }>>(
      `/api/workflows/${workflowId}/runs`,
      { method: "DELETE" },
    ),

  exportRuns: (workflowId: string, status?: string, limit?: number) => {
    const params = new URLSearchParams()
    if (status) params.set("status", status)
    if (limit != null) params.set("limit", String(limit))
    const qs = params.toString()
    return apiFetch<{
      workflow_id: string
      workflow_name: string
      exported_at: string
      total_runs: number
      runs: Array<Record<string, unknown>>
    }>(`/api/workflows/${workflowId}/runs/export${qs ? `?${qs}` : ""}`)
  },

  batchRun: (id: string, inputs: Record<string, unknown>[], maxParallel = 3) =>
    apiFetch<ApiResponse<WorkflowBatchRunResponse>>(
      `/api/workflows/${id}/batch-run`,
      {
        method: "POST",
        body: JSON.stringify({ inputs, max_parallel: maxParallel }),
      },
    ).then((r) => r.data),

  export: (id: string) =>
    apiFetch<{ format: string; exported_at: string; workflow: Record<string, unknown> }>(
      `/api/workflows/${id}/export`,
    ),

  import: (fileData: Record<string, unknown>) =>
    apiFetch<ApiResponse<WorkflowImportResult>>("/api/workflows/import", {
      method: "POST",
      body: JSON.stringify(fileData),
    }).then((r) => r.data),

  publish: (id: string, body: { scope: "org" | "global"; org_id?: string }) =>
    apiFetch<ApiResponse<WorkflowResponse>>(`/api/workflows/${id}/publish`, {
      method: "POST",
      body: JSON.stringify(body),
    }).then((r) => r.data),

  unpublish: (id: string) =>
    apiFetch<ApiResponse<WorkflowResponse>>(`/api/workflows/${id}/unpublish`, {
      method: "POST",
    }).then((r) => r.data),

  resubmit: (id: string) =>
    apiFetch<ApiResponse<WorkflowResponse>>(`/api/workflows/${id}/resubmit`, {
      method: "POST",
    }).then((r) => r.data),

  duplicate: (id: string) =>
    apiFetch<ApiResponse<WorkflowResponse>>(`/api/workflows/${id}/fork`, {
      method: "POST",
    }).then((r) => r.data),

  getVariables: (id: string) =>
    apiFetch<ApiResponse<Record<string, { node_type: string; title: string; outputs: Array<{ name: string; type: string; description: string }> }>>>(`/api/workflows/${id}/variables`).then((r) => r.data),

  validate: (blueprint: Record<string, unknown>) =>
    apiFetch<ApiResponse<{
      valid: boolean
      node_count: number
      edge_count: number
      warnings: Array<{ node_id: string | null; code: string; message: string }>
      error?: string
    }>>("/api/workflows/validate", {
      method: "POST",
      body: JSON.stringify({ blueprint }),
    }).then((r) => r.data),

  validateById: (id: string) =>
    apiFetch<ApiResponse<WorkflowValidateResponse>>(`/api/workflows/${id}/validate`, {
      method: "POST",
    }).then((r) => r.data),

  getStats: (id: string) =>
    apiFetch<ApiResponse<WorkflowStats>>(`/api/workflows/${id}/stats`).then((r) => r.data),

  getAnalytics: (id: string, days = 30) =>
    apiFetch<ApiResponse<WorkflowAnalyticsResponse>>(
      `/api/workflows/${id}/analytics?days=${days}`,
    ).then((r) => r.data),

  getTemplates: () =>
    apiFetch<ApiResponse<{ templates: WorkflowTemplate[]; by_category: Record<string, WorkflowTemplate[]> }>>(
      "/api/workflow-templates",
    ).then((r) => r.data.templates),

  createFromTemplate: (templateId: string, name?: string) =>
    apiFetch<ApiResponse<WorkflowResponse>>(
      `/api/workflow-templates/${templateId}/create-workflow`,
      {
        method: "POST",
        body: JSON.stringify({ template_id: templateId, name }),
      },
    ).then((r) => r.data),

  getEnvKeys: (id: string) =>
    apiFetch<ApiResponse<{ keys: string[] }>>(`/api/workflows/${id}/env`).then((r) => r.data),

  updateEnv: (id: string, envVars: Record<string, string>) =>
    apiFetch<ApiResponse<{ keys: string[] }>>(`/api/workflows/${id}/env`, {
      method: "PUT",
      body: JSON.stringify({ env_vars: envVars }),
    }).then((r) => r.data),

  getNodeStats: (id: string, limit = 20) =>
    apiFetch<ApiResponse<NodeStatsResponse>>(
      `/api/workflows/${id}/node-stats?limit=${limit}`,
    ).then((r) => r.data),

  // --- Versions ---
  getVersions: (workflowId: string, page = 1, size = 20) =>
    apiFetch<PaginatedResponse<WorkflowVersionResponse>>(
      `/api/workflows/${workflowId}/versions?page=${page}&size=${size}`,
    ),

  getVersion: (workflowId: string, versionId: string) =>
    apiFetch<ApiResponse<WorkflowVersionResponse>>(
      `/api/workflows/${workflowId}/versions/${versionId}`,
    ).then((r) => r.data),

  restoreVersion: (workflowId: string, versionId: string) =>
    apiFetch<ApiResponse<WorkflowResponse>>(
      `/api/workflows/${workflowId}/versions/${versionId}/restore`,
      { method: "POST" },
    ).then((r) => r.data),

  testWebhook: (id: string) =>
    apiFetch<ApiResponse<{ success: boolean; status_code?: number; error?: string }>>(
      `/api/workflows/${id}/test-webhook`,
      { method: "POST" },
    ).then((r) => r.data),

  // --- Schedule ---
  getSchedule: (id: string) =>
    apiFetch<ApiResponse<WorkflowScheduleResponse>>(
      `/api/workflows/${id}/schedule`,
    ).then((r) => r.data),

  updateSchedule: (id: string, body: WorkflowScheduleUpdate) =>
    apiFetch<ApiResponse<WorkflowScheduleResponse>>(
      `/api/workflows/${id}/schedule`,
      { method: "PUT", body: JSON.stringify(body) },
    ).then((r) => r.data),

  deleteSchedule: (id: string) =>
    apiFetch<ApiResponse<{ deleted: boolean }>>(
      `/api/workflows/${id}/schedule`,
      { method: "DELETE" },
    ).then((r) => r.data),

  // --- API Key ---
  generateApiKey: (id: string) =>
    apiFetch<ApiResponse<{ api_key: string; workflow_id: string }>>(
      `/api/workflows/${id}/generate-api-key`,
      { method: "POST" },
    ).then((r) => r.data),

  revokeApiKey: (id: string) =>
    apiFetch<ApiResponse<{ revoked: boolean }>>(
      `/api/workflows/${id}/api-key`,
      { method: "DELETE" },
    ).then((r) => r.data),

  testNode: (workflowId: string, body: NodeTestRequest) =>
    apiFetch<ApiResponse<NodeTestResponse>>(
      `/api/workflows/${workflowId}/test-node`,
      { method: "POST", body: JSON.stringify(body) },
    ).then((r) => r.data),
}

// --- Skill API ---
export const skillApi = {
  list: (page = 1, size = 50) =>
    apiFetch<PaginatedResponse<SkillResponse>>(
      `/api/skills?page=${page}&size=${size}`,
    ),
  get: (id: string) =>
    apiFetch<ApiResponse<SkillResponse>>(`/api/skills/${id}`).then((r) => r.data),
  create: (body: SkillCreate) =>
    apiFetch<ApiResponse<SkillResponse>>("/api/skills", {
      method: "POST",
      body: JSON.stringify(body),
    }).then((r) => r.data),
  update: (id: string, body: SkillUpdate) =>
    apiFetch<ApiResponse<SkillResponse>>(`/api/skills/${id}`, {
      method: "PUT",
      body: JSON.stringify(body),
    }).then((r) => r.data),
  delete: (id: string) =>
    apiFetch<ApiResponse<{ deleted: string }>>(`/api/skills/${id}`, {
      method: "DELETE",
    }),
  publish: (id: string, body: { scope: "org" | "global"; org_id?: string }) =>
    apiFetch<ApiResponse<SkillResponse>>(`/api/skills/${id}/publish`, {
      method: "POST",
      body: JSON.stringify(body),
    }).then((r) => r.data),
  unpublish: (id: string) =>
    apiFetch<ApiResponse<SkillResponse>>(`/api/skills/${id}/unpublish`, {
      method: "POST",
    }).then((r) => r.data),
  resubmit: (id: string) =>
    apiFetch<ApiResponse<SkillResponse>>(`/api/skills/${id}/resubmit`, {
      method: "POST",
    }).then((r) => r.data),
  toggle: (id: string) =>
    apiFetch<ApiResponse<SkillResponse>>(`/api/skills/${id}/toggle`, {
      method: "POST",
    }).then((r) => r.data),

  getTemplates: () =>
    apiFetch<ApiResponse<SkillTemplate[]>>("/api/skill-templates").then((r) => r.data),

  createFromTemplate: (templateId: string) =>
    apiFetch<ApiResponse<SkillResponse>>(`/api/skill-templates/${templateId}/create`, {
      method: "POST",
    }).then((r) => r.data),

  forkSkill: (id: string, name?: string) =>
    apiFetch<ApiResponse<SkillResponse>>(`/api/skills/${id}/fork`, {
      method: "POST",
      body: JSON.stringify(name ? { name } : {}),
    }).then((r) => r.data),
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
export interface AdminWorkflowInfo {
  id: string; name: string; icon: string | null; description: string | null
  status: string; is_active: boolean; node_count: number
  total_runs: number; success_rate: number | null; last_run_at: string | null
  user_id: string; username: string | null; email: string | null
  created_at: string; updated_at: string
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

// --- New Admin Types ---
export interface AdminSkillInfo {
  id: string; name: string; description: string | null; is_active: boolean
  agents_using: number; user_id: string; username: string | null; email: string | null
  created_at: string
}
export interface AdminSkillDetail extends AdminSkillInfo {
  content: string | null; system_prompt: string | null
}
export interface AdminEvalDataset {
  id: string; name: string; description: string | null; case_count: number
  last_run_at: string | null; user_id: string; username: string | null
  email: string | null; created_at: string
}
export interface AdminEvalRun {
  id: string; dataset_name: string; status: string; pass_rate: number | null
  tokens_used: number; user_id: string; username: string | null
  email: string | null; created_at: string
}
export interface AdminEvalStats {
  total_datasets: number; total_runs: number; avg_pass_rate: number | null
  total_tokens: number
}
export interface AdminCredential {
  id: string; user_id: string | null; username: string | null; email: string | null
  resource_name: string | null; resource_type: 'connector' | 'mcp'; resource_id: string
  updated_at: string | null; created_at: string | null
}
export interface AdminCredentialStats {
  total_credentials: number; connector_credentials: number; mcp_credentials: number; users_with_credentials: number
}
export interface AdminReview {
  id: string; resource_type: string; resource_name: string; org_name: string | null
  submitter_name: string | null; submitted_at: string
}
export interface AdminReviewStats {
  pending: number; avg_review_time_hours: number | null; approval_rate: number | null
}
export interface AdminSchedule {
  id: string; workflow_id: string; workflow_name: string; user_id: string
  username: string | null; email: string | null; cron_expression: string
  timezone: string; is_active: boolean; next_run_at: string | null
  last_run_at: string | null
}
export interface AdminScheduleStats {
  active: number; total: number; next_run_at: string | null; failed_24h: number
}
export interface AdminNotificationConfig {
  enabled: boolean; new_user_registration: boolean; quota_hit: boolean; connector_failure: boolean
  schedule_failure: boolean; login_anomaly: boolean; smtp_configured: boolean
}
export interface AdminNotificationEvent {
  id: string; type: string; description: string; user: string | null
  created_at: string
}
export interface AdminAnalyticsByAgent {
  agent_name: string; owner: string | null; conversations: number
  total_tokens: number; avg_tokens_per_conv: number
}
export interface AdminAnalyticsByConnector {
  connector_name: string; total_calls: number; success_rate: number
  avg_response_time_ms: number; errors: number
}
export interface AdminAnalyticsByWorkflow {
  workflow_name: string; owner: string | null; total_runs: number
  success_rate: number; avg_duration_ms: number
}
export interface AdminCostProjection {
  projected_tokens: number; daily_avg: number; trailing_total: number
}

export interface MarketResourceItem {
  id: string
  resource_type: string
  name: string
  description: string | null
  status: string
  publish_status: string | null
  owner_username: string | null
  created_at: string | null
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

  // --- Model Providers ---
  listModelProviders: () =>
    apiFetch<ModelProvidersListResponse>('/api/admin/model-providers'),
  createModelProvider: (data: ModelProviderCreate) =>
    apiFetch<ModelProviderResponse>('/api/admin/model-providers', {
      method: 'POST',
      body: JSON.stringify(data),
    }),
  updateModelProvider: (id: string, data: ModelProviderUpdate) =>
    apiFetch<ModelProviderResponse>(`/api/admin/model-providers/${id}`, {
      method: 'PUT',
      body: JSON.stringify(data),
    }),
  deleteModelProvider: (id: string) =>
    apiFetch<{ success: boolean }>(`/api/admin/model-providers/${id}`, { method: 'DELETE' }),

  // --- Provider Models ---
  createProviderModel: (providerId: string, data: ModelProviderModelCreate) =>
    apiFetch<ModelProviderModelResponse>(`/api/admin/model-providers/${providerId}/models`, {
      method: 'POST',
      body: JSON.stringify(data),
    }),
  updateProviderModel: (id: string, data: ModelProviderModelUpdate) =>
    apiFetch<ModelProviderModelResponse>(`/api/admin/model-provider-models/${id}`, {
      method: 'PUT',
      body: JSON.stringify(data),
    }),
  deleteProviderModel: (id: string) =>
    apiFetch<{ success: boolean }>(`/api/admin/model-provider-models/${id}`, { method: 'DELETE' }),

  // --- Model Groups ---
  listModelGroups: () =>
    apiFetch<ModelGroupsListResponse>('/api/admin/model-groups'),
  createModelGroup: (data: ModelGroupCreate) =>
    apiFetch<ModelGroupResponse>('/api/admin/model-groups', {
      method: 'POST',
      body: JSON.stringify(data),
    }),
  updateModelGroup: (id: string, data: ModelGroupUpdate) =>
    apiFetch<ModelGroupResponse>(`/api/admin/model-groups/${id}`, {
      method: 'PUT',
      body: JSON.stringify(data),
    }),
  deleteModelGroup: (id: string) =>
    apiFetch<{ success: boolean }>(`/api/admin/model-groups/${id}`, { method: 'DELETE' }),
  activateModelGroup: (id: string) =>
    apiFetch<{ success: boolean }>(`/api/admin/model-groups/${id}/activate`, { method: 'PATCH' }),
  deactivateModelGroups: () =>
    apiFetch<{ success: boolean }>('/api/admin/model-groups/deactivate', { method: 'POST' }),

  // --- Model Active Config ---
  getModelActiveConfig: () =>
    apiFetch<ModelActiveConfig>('/api/admin/model-active-config'),

  // --- Model Config Import/Export ---
  exportModelConfig: async (): Promise<void> => {
    const token = getAccessToken()
    const headers: Record<string, string> = {}
    if (token) headers["Authorization"] = `Bearer ${token}`
    const res = await fetch(
      `${getApiBaseUrl()}/api/admin/model-config/export`,
      { headers },
    )
    if (!res.ok) {
      const body = await res.json().catch(() => ({}))
      throw new ApiError(res.status, body.detail || res.statusText, body.error_code ?? null, body.error_args ?? {})
    }
    const blob = await res.blob()
    const contentDisposition = res.headers.get("content-disposition")
    const utf8Match = contentDisposition?.match(/filename\*=UTF-8''(.+?)(?:;|$)/)
    const asciiMatch = contentDisposition?.match(/filename="?([^";\n]+)"?/)
    const filename = utf8Match ? decodeURIComponent(utf8Match[1]) : asciiMatch?.[1] ?? "model-config.json"
    const url = URL.createObjectURL(blob)
    const a = document.createElement("a")
    a.href = url
    a.download = filename
    document.body.appendChild(a)
    a.click()
    document.body.removeChild(a)
    URL.revokeObjectURL(url)
  },

  importModelConfig: (data: object) =>
    apiFetch<{ data: { created: { providers: number; models: number; groups: number }; skipped: { providers: number; models: number; groups: number }; deleted?: { providers: number; models: number; groups: number }; warnings: string[] } }>('/api/admin/model-config/import', {
      method: 'POST',
      body: JSON.stringify(data),
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

  // --- Workflows ---
  listAllWorkflows: (params?: { page?: number; size?: number; search?: string; status?: string }) => {
    const sp = new URLSearchParams()
    if (params?.page) sp.set('page', String(params.page))
    if (params?.size) sp.set('size', String(params.size))
    if (params?.search) sp.set('search', params.search)
    if (params?.status) sp.set('status', params.status)
    return apiFetch<{ items: AdminWorkflowInfo[]; total: number; page: number; size: number; pages: number }>(`/api/admin/workflows?${sp}`)
  },
  toggleWorkflowActive: (workflowId: string) =>
    apiFetch<{ ok: boolean; is_active: boolean }>(`/api/admin/workflows/${workflowId}/toggle`, { method: 'POST' }),
  adminDeleteWorkflow: (workflowId: string) =>
    apiFetch(`/api/admin/workflows/${workflowId}`, { method: 'DELETE' }),

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

  // --- Organizations (admin) ---
  listOrganizations: (page = 1, size = 20, q?: string) => {
    const sp = new URLSearchParams({ page: String(page), size: String(size) })
    if (q) sp.set('q', q)
    return apiFetch<{ items: AdminOrganization[]; total: number; page: number; size: number; pages: number }>(`/api/admin/organizations?${sp}`)
  },
  adminDeleteOrganization: (orgId: string) =>
    apiFetch(`/api/admin/organizations/${orgId}`, { method: 'DELETE' }),

  // --- Organizations (regular CRUD) ---
  createOrganization: (data: { name: string; description?: string; icon?: string; review_agents?: boolean; review_connectors?: boolean; review_kbs?: boolean; review_mcp_servers?: boolean; review_workflows?: boolean; review_skills?: boolean }) =>
    apiFetch<AdminOrganization>('/api/orgs', { method: 'POST', body: JSON.stringify(data) }),
  updateOrganization: (orgId: string, data: { name?: string; description?: string; icon?: string; review_agents?: boolean; review_connectors?: boolean; review_kbs?: boolean; review_mcp_servers?: boolean; review_workflows?: boolean; review_skills?: boolean }) =>
    apiFetch<AdminOrganization>(`/api/admin/organizations/${orgId}`, { method: 'PUT', body: JSON.stringify(data) }),
  deleteOrganization: (orgId: string) =>
    apiFetch(`/api/orgs/${orgId}`, { method: 'DELETE' }),

  // --- Organization members ---
  listOrgMembers: (orgId: string) =>
    apiFetch<{ data: AdminOrgMember[] }>(`/api/orgs/${orgId}/members`).then(r => r.data ?? []),
  addOrgMember: (orgId: string, data: { username_or_email: string; role: string }) =>
    apiFetch<AdminOrgMember>(`/api/orgs/${orgId}/members`, { method: 'POST', body: JSON.stringify(data) }),
  updateOrgMemberRole: (orgId: string, userId: string, data: { role: string }) =>
    apiFetch<AdminOrgMember>(`/api/orgs/${orgId}/members/${userId}`, { method: 'PATCH', body: JSON.stringify(data) }),
  removeOrgMember: (orgId: string, userId: string) =>
    apiFetch(`/api/orgs/${orgId}/members/${userId}`, { method: 'DELETE' }),

  // --- Review Log ---
  listReviewLog: (params?: { org_id?: string; resource_type?: string; action?: string; limit?: number; offset?: number }) => {
    const sp = new URLSearchParams()
    if (params?.org_id) sp.set('org_id', params.org_id)
    if (params?.resource_type) sp.set('resource_type', params.resource_type)
    if (params?.action) sp.set('action', params.action)
    if (params?.limit !== undefined) sp.set('limit', String(params.limit))
    if (params?.offset !== undefined) sp.set('offset', String(params.offset))
    const qs = sp.toString()
    return apiFetch<{ items: ReviewLogItem[]; total: number; limit: number; offset: number }>(`/api/admin/review-log${qs ? `?${qs}` : ''}`)
  },

  // --- Skills ---
  listAllSkills: (params?: { page?: number; size?: number; search?: string }) => {
    const sp = new URLSearchParams()
    if (params?.page) sp.set('page', String(params.page))
    if (params?.size) sp.set('size', String(params.size))
    if (params?.search) sp.set('search', params.search)
    return apiFetch<{ items: AdminSkillInfo[]; total: number; page: number; size: number; pages: number }>(`/api/admin/skills?${sp}`)
  },
  getSkillDetail: (id: string) =>
    apiFetch<AdminSkillDetail>(`/api/admin/skills/${id}`),
  toggleSkillActive: (id: string, is_active: boolean) =>
    apiFetch<AdminSkillInfo>(`/api/admin/skills/${id}/active`, { method: 'PATCH', body: JSON.stringify({ is_active }) }),
  adminDeleteSkill: (id: string) =>
    apiFetch(`/api/admin/skills/${id}`, { method: 'DELETE' }),
  batchDeleteSkills: (ids: string[]) =>
    apiFetch<{ deleted: number }>('/api/admin/skills/batch-delete', { method: 'POST', body: JSON.stringify({ ids }) }),

  // --- Evaluations ---
  listEvalDatasets: (params?: { page?: number; size?: number }) => {
    const sp = new URLSearchParams()
    if (params?.page) sp.set('page', String(params.page))
    if (params?.size) sp.set('size', String(params.size))
    return apiFetch<{ items: AdminEvalDataset[]; total: number; page: number; size: number; pages: number }>(`/api/admin/eval/datasets?${sp}`)
  },
  listEvalRuns: (params?: { page?: number; size?: number }) => {
    const sp = new URLSearchParams()
    if (params?.page) sp.set('page', String(params.page))
    if (params?.size) sp.set('size', String(params.size))
    return apiFetch<{ items: AdminEvalRun[]; total: number; page: number; size: number; pages: number }>(`/api/admin/eval/runs?${sp}`)
  },
  deleteEvalDataset: (id: string) =>
    apiFetch(`/api/admin/eval/datasets/${id}`, { method: 'DELETE' }),
  deleteEvalRun: (id: string) =>
    apiFetch(`/api/admin/eval/runs/${id}`, { method: 'DELETE' }),
  cleanupEvalRuns: (maxAgeDays: number) =>
    apiFetch<{ deleted: number }>('/api/admin/eval/cleanup', { method: 'POST', body: JSON.stringify({ max_age_days: maxAgeDays }) }),
  getEvalStats: () =>
    apiFetch<AdminEvalStats>('/api/admin/eval/stats'),

  // --- Credentials ---
  listCredentials: (params?: { page?: number; size?: number; type?: string; search?: string }) => {
    const sp = new URLSearchParams()
    if (params?.page) sp.set('page', String(params.page))
    if (params?.size) sp.set('size', String(params.size))
    if (params?.type) sp.set('type', params.type)
    if (params?.search) sp.set('search', params.search)
    return apiFetch<{ items: AdminCredential[]; total: number; page: number; size: number; pages: number }>(`/api/admin/credentials?${sp}`)
  },
  revokeConnectorCredential: (id: string) =>
    apiFetch(`/api/admin/credentials/connector/${id}`, { method: 'DELETE' }),
  revokeMcpCredential: (id: string) =>
    apiFetch(`/api/admin/credentials/mcp/${id}`, { method: 'DELETE' }),
  getCredentialStats: () =>
    apiFetch<AdminCredentialStats>('/api/admin/credentials/stats'),

  // --- Reviews ---
  listPendingReviews: (params?: { page?: number; size?: number; org_id?: string; resource_type?: string }) => {
    const sp = new URLSearchParams()
    if (params?.page) sp.set('page', String(params.page))
    if (params?.size) sp.set('size', String(params.size))
    if (params?.org_id) sp.set('org_id', params.org_id)
    if (params?.resource_type) sp.set('resource_type', params.resource_type)
    return apiFetch<{ items: AdminReview[]; total: number; page: number; size: number; pages: number }>(`/api/admin/reviews/pending?${sp}`)
  },
  batchApproveReviews: (reviewIds: string[]) =>
    apiFetch<{ approved: number }>('/api/admin/reviews/batch-approve', { method: 'POST', body: JSON.stringify({ review_ids: reviewIds }) }),
  batchRejectReviews: (reviewIds: string[], reason?: string) =>
    apiFetch<{ rejected: number }>('/api/admin/reviews/batch-reject', { method: 'POST', body: JSON.stringify({ review_ids: reviewIds, reason }) }),
  getReviewStats: () =>
    apiFetch<AdminReviewStats>('/api/admin/reviews/stats'),

  // --- Schedules ---
  listSchedules: (params?: { page?: number; size?: number }) => {
    const sp = new URLSearchParams()
    if (params?.page) sp.set('page', String(params.page))
    if (params?.size) sp.set('size', String(params.size))
    return apiFetch<{ items: AdminSchedule[]; total: number; page: number; size: number; pages: number }>(`/api/admin/schedules?${sp}`)
  },
  toggleScheduleActive: (workflowId: string) =>
    apiFetch<{ ok: boolean; is_active: boolean }>(`/api/admin/schedules/${workflowId}/active`, { method: 'PATCH' }),
  getScheduleStats: () =>
    apiFetch<AdminScheduleStats>('/api/admin/schedules/stats'),

  // --- Notifications ---
  getNotificationConfig: () =>
    apiFetch<AdminNotificationConfig>('/api/admin/notifications/config'),
  updateNotificationConfig: (config: AdminNotificationConfig) =>
    apiFetch<AdminNotificationConfig>('/api/admin/notifications/config', { method: 'PUT', body: JSON.stringify(config) }),
  listNotificationEvents: (params?: { page?: number; size?: number }) => {
    const sp = new URLSearchParams()
    if (params?.page) sp.set('page', String(params.page))
    if (params?.size) sp.set('size', String(params.size))
    return apiFetch<{ items: AdminNotificationEvent[]; total: number; page: number; size: number; pages: number }>(`/api/admin/notifications/events?${sp}`)
  },
  sendTestNotification: () =>
    apiFetch<{ ok: boolean }>('/api/admin/notifications/test', { method: 'POST' }),

  // --- Enhanced Analytics ---
  getAnalyticsByAgent: (period: string) =>
    apiFetch<AdminAnalyticsByAgent[]>(`/api/admin/analytics/by-agent?period=${period}`),
  getAnalyticsByConnector: (period: string) =>
    apiFetch<AdminAnalyticsByConnector[]>(`/api/admin/analytics/by-connector?period=${period}`),
  getAnalyticsByWorkflow: (period: string) =>
    apiFetch<AdminAnalyticsByWorkflow[]>(`/api/admin/analytics/by-workflow?period=${period}`),
  getCostProjection: () =>
    apiFetch<AdminCostProjection>('/api/admin/analytics/cost-projection'),

  // --- Batch operations for Resources ---
  batchToggleAgents: (ids: string[], isActive: boolean) =>
    apiFetch<{ toggled: number }>('/api/admin/agents/batch-toggle', { method: 'POST', body: JSON.stringify({ ids, is_active: isActive }) }),
  batchDeleteAgents: (ids: string[]) =>
    apiFetch<{ deleted: number }>('/api/admin/agents/batch-delete', { method: 'POST', body: JSON.stringify({ ids }) }),
  batchToggleKBs: (ids: string[], isActive: boolean) =>
    apiFetch<{ toggled: number }>('/api/admin/knowledge-bases/batch-toggle', { method: 'POST', body: JSON.stringify({ ids, is_active: isActive }) }),
  batchDeleteKBs: (ids: string[]) =>
    apiFetch<{ deleted: number }>('/api/admin/knowledge-bases/batch-delete', { method: 'POST', body: JSON.stringify({ ids }) }),

  // --- Batch operations for Connectors ---
  batchToggleConnectors: (ids: string[], isActive: boolean) =>
    apiFetch<{ toggled: number }>('/api/admin/connectors/batch-toggle', { method: 'POST', body: JSON.stringify({ ids, is_active: isActive }) }),
  batchDeleteConnectors: (ids: string[]) =>
    apiFetch<{ deleted: number }>('/api/admin/connectors/batch-delete', { method: 'POST', body: JSON.stringify({ ids }) }),

  // --- Market Templates ---
  importMarketTemplates: () =>
    apiFetch<{ ok: boolean; created: number; updated: number }>('/api/admin/market/import-templates', {
      method: 'POST',
    }),

  listMarketResources: () =>
    apiFetch<{ items: MarketResourceItem[]; total: number }>('/api/admin/market/resources'),

  deleteMarketResource: (resourceType: string, resourceId: string) =>
    apiFetch(`/api/admin/market/resources/${resourceType}/${resourceId}`, {
      method: 'DELETE',
    }),

  unpublishMarketResource: (resourceType: string, resourceId: string) =>
    apiFetch(`/api/admin/market/resources/${resourceType}/${resourceId}/unpublish`, {
      method: 'PATCH',
    }),
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

  resubmit: (id: string) =>
    apiFetch<ApiResponse<MCPServerResponse>>(`/api/mcp-servers/${id}/resubmit`, {
      method: "POST",
    }).then((r) => r.data),

  getMyCredentials: (id: string) =>
    apiFetch<ApiResponse<MCPMyCredentialStatus>>(`/api/mcp-servers/${id}/my-credentials`).then(
      (r) => r.data,
    ),

  upsertMyCredentials: (id: string, body: { env?: Record<string, string>; headers?: Record<string, string> }) =>
    apiFetch<ApiResponse<{ saved: boolean }>>(`/api/mcp-servers/${id}/my-credentials`, {
      method: "PUT",
      body: JSON.stringify(body),
    }).then((r) => r.data),

  publish: (id: string, body: { scope: "org"; org_id: string; allow_fallback?: boolean }) =>
    apiFetch<ApiResponse<MCPServerResponse>>(`/api/mcp-servers/${id}/publish`, {
      method: "POST",
      body: JSON.stringify(body),
    }).then((r) => r.data),

  unpublish: (id: string) =>
    apiFetch<ApiResponse<MCPServerResponse>>(`/api/mcp-servers/${id}/unpublish`, {
      method: "POST",
    }).then((r) => r.data),

  forkMCPServer: (id: string, name?: string) =>
    apiFetch<ApiResponse<MCPServerResponse>>(`/api/mcp-servers/${id}/fork`, {
      method: "POST",
      body: JSON.stringify(name ? { name } : {}),
    }).then((r) => r.data),
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
  role: "owner" | "admin" | "member"
  review_agents: boolean
  review_connectors: boolean
  review_kbs: boolean
  review_mcp_servers: boolean
  review_workflows: boolean
  review_skills: boolean
}

export interface ReviewItem {
  resource_type: string
  resource_id: string
  resource_name: string
  resource_icon: string | null
  owner_username: string | null
  submitted_at: string | null
  publish_status: string
  review_note: string | null
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

  create: (body: { name: string; slug?: string; description?: string | null; icon?: string | null; review_agents?: boolean; review_connectors?: boolean; review_kbs?: boolean; review_mcp_servers?: boolean; review_workflows?: boolean; review_skills?: boolean }) =>
    apiFetch<{ data: UserOrg }>("/api/orgs", {
      method: "POST",
      body: JSON.stringify(body),
    }).then(r => r.data),

  update: (orgId: string, body: { name?: string; description?: string | null; icon?: string | null; review_agents?: boolean; review_connectors?: boolean; review_kbs?: boolean; review_mcp_servers?: boolean; review_workflows?: boolean; review_skills?: boolean }) =>
    apiFetch<{ data: UserOrg }>(`/api/orgs/${orgId}`, {
      method: "PUT",
      body: JSON.stringify(body),
    }).then(r => r.data),

  delete: (orgId: string) =>
    apiFetch<void>(`/api/orgs/${orgId}`, { method: "DELETE" }),

  listMembers: (orgId: string) =>
    apiFetch<{ data: OrgMember[] }>(`/api/orgs/${orgId}/members`).then(r => r.data ?? []),

  addMember: (orgId: string, body: { username_or_email: string; role: string }) => {
    const isEmail = body.username_or_email.includes("@")
    const payload = isEmail
      ? { email: body.username_or_email, role: body.role }
      : { username: body.username_or_email, role: body.role }
    return apiFetch<{ data: OrgMember }>(`/api/orgs/${orgId}/members`, {
      method: "POST",
      body: JSON.stringify(payload),
    }).then(r => r.data)
  },

  changeRole: (orgId: string, userId: string, role: string) =>
    apiFetch<{ data: OrgMember }>(`/api/orgs/${orgId}/members/${userId}`, {
      method: "PATCH",
      body: JSON.stringify({ role }),
    }).then(r => r.data),

  removeMember: (orgId: string, userId: string) =>
    apiFetch<void>(`/api/orgs/${orgId}/members/${userId}`, { method: "DELETE" }),

  listReviews: (orgId: string, params?: { resource_type?: string; status?: string }) => {
    const qs = new URLSearchParams()
    if (params?.resource_type) qs.set("resource_type", params.resource_type)
    if (params?.status) qs.set("status", params.status)
    const query = qs.toString()
    return apiFetch<{ data: ReviewItem[] }>(`/api/orgs/${orgId}/reviews${query ? `?${query}` : ""}`).then(r => r.data ?? [])
  },

  approveReview: (orgId: string, body: { resource_type: string; resource_id: string }) =>
    apiFetch<void>(`/api/orgs/${orgId}/reviews/approve`, {
      method: "POST",
      body: JSON.stringify(body),
    }),

  rejectReview: (orgId: string, body: { resource_type: string; resource_id: string; note?: string }) =>
    apiFetch<void>(`/api/orgs/${orgId}/reviews/reject`, {
      method: "POST",
      body: JSON.stringify(body),
    }),

  listReviewLog: (orgId: string, params?: { resource_type?: string; action?: string; limit?: number; offset?: number }) => {
    const sp = new URLSearchParams()
    if (params?.resource_type) sp.set('resource_type', params.resource_type)
    if (params?.action) sp.set('action', params.action)
    if (params?.limit !== undefined) sp.set('limit', String(params.limit))
    if (params?.offset !== undefined) sp.set('offset', String(params.offset))
    const qs = sp.toString()
    return apiFetch<{ data: import('@/types/admin').ReviewLogItem[] }>(`/api/orgs/${orgId}/reviews/log${qs ? `?${qs}` : ''}`).then(r => r.data ?? [])
  },
}

// --- Eval API ---
export const evalApi = {
  // Datasets
  listDatasets: (page = 1, size = 20) =>
    apiFetch<PaginatedResponse<EvalDatasetResponse>>(`/api/eval/datasets?page=${page}&size=${size}`),
  createDataset: (body: EvalDatasetCreate) =>
    apiFetch<ApiResponse<EvalDatasetResponse>>("/api/eval/datasets", {
      method: "POST",
      body: JSON.stringify(body),
    }).then((r) => (r as ApiResponse<EvalDatasetResponse>).data),
  getDataset: (id: string) =>
    apiFetch<ApiResponse<EvalDatasetResponse>>(`/api/eval/datasets/${id}`).then(
      (r) => (r as ApiResponse<EvalDatasetResponse>).data,
    ),
  updateDataset: (id: string, body: EvalDatasetUpdate) =>
    apiFetch<ApiResponse<EvalDatasetResponse>>(`/api/eval/datasets/${id}`, {
      method: "PUT",
      body: JSON.stringify(body),
    }).then((r) => (r as ApiResponse<EvalDatasetResponse>).data),
  deleteDataset: (id: string) =>
    apiFetch<ApiResponse<{ deleted: string }>>(`/api/eval/datasets/${id}`, { method: "DELETE" }),
  // Cases
  listCases: (datasetId: string, page = 1, size = 50) =>
    apiFetch<PaginatedResponse<EvalCaseResponse>>(
      `/api/eval/datasets/${datasetId}/cases?page=${page}&size=${size}`,
    ),
  createCase: (datasetId: string, body: EvalCaseCreate) =>
    apiFetch<ApiResponse<EvalCaseResponse>>(`/api/eval/datasets/${datasetId}/cases`, {
      method: "POST",
      body: JSON.stringify(body),
    }).then((r) => (r as ApiResponse<EvalCaseResponse>).data),
  updateCase: (datasetId: string, caseId: string, body: EvalCaseUpdate) =>
    apiFetch<ApiResponse<EvalCaseResponse>>(`/api/eval/datasets/${datasetId}/cases/${caseId}`, {
      method: "PUT",
      body: JSON.stringify(body),
    }).then((r) => (r as ApiResponse<EvalCaseResponse>).data),
  deleteCase: (datasetId: string, caseId: string) =>
    apiFetch<ApiResponse<{ deleted: string }>>(
      `/api/eval/datasets/${datasetId}/cases/${caseId}`,
      { method: "DELETE" },
    ),
  // Runs
  listRuns: (page = 1, size = 20) =>
    apiFetch<PaginatedResponse<EvalRunResponse>>(`/api/eval/runs?page=${page}&size=${size}`),
  createRun: (body: EvalRunCreate) =>
    apiFetch<ApiResponse<EvalRunResponse>>("/api/eval/runs", {
      method: "POST",
      body: JSON.stringify(body),
    }).then((r) => (r as ApiResponse<EvalRunResponse>).data),
  getRun: (id: string) =>
    apiFetch<ApiResponse<EvalRunDetailResponse>>(`/api/eval/runs/${id}`).then(
      (r) => (r as ApiResponse<EvalRunDetailResponse>).data,
    ),
  deleteRun: (id: string) =>
    apiFetch<ApiResponse<{ deleted: string }>>(`/api/eval/runs/${id}`, { method: "DELETE" }),
}

// --- Dashboard types ---
export interface DashboardConversation {
  id: string
  title: string
  agent_id: string | null
  agent_name: string | null
  created_at: string
  updated_at: string | null
}

export interface DashboardAgent {
  id: string
  name: string
  icon: string | null
  description: string | null
  conversation_count: number
}

export interface DashboardKB {
  id: string
  name: string
  document_count: number
  total_chunks: number
}

export interface DashboardConnectorHealth {
  id: string
  name: string
  icon: string | null
  type: string
  status: string // "active" | "inactive" | "error"
  call_count_today: number
}

export interface DashboardDayStat {
  date: string
  count: number
  tokens: number
}

export interface DashboardWorkflowRun {
  id: string
  workflow_id: string
  workflow_name: string
  status: string  // "completed" | "failed" | "cancelled" | "running"
  created_at: string
}

export interface DashboardStats {
  total_conversations: number
  total_agents: number
  total_tokens: number
  active_connectors: number
  agent_conversations_today: number
  connector_calls_today: number
  conversations_week_trend: number // percentage, e.g. 12.5 = +12.5%
  tokens_week_trend: number
  recent_conversations: DashboardConversation[]
  top_agents: DashboardAgent[]
  top_kbs: DashboardKB[]
  connector_health: DashboardConnectorHealth[]
  activity_trend: DashboardDayStat[]
  // Workflow stats
  total_workflows: number
  workflow_runs_today: number
  workflow_success_rate: number  // 0-100
  recent_workflow_runs: DashboardWorkflowRun[]
}

// --- Dashboard API ---
export const dashboardApi = {
  stats: () => apiFetch<DashboardStats>("/api/dashboard/stats"),
}

// --- Market Types ---
export interface MarketItem {
  id: string
  name: string
  description: string | null
  icon: string | null
  resource_type: "agent" | "connector" | "knowledge_base" | "mcp_server" | "skill" | "workflow"
  org_id: string
  org_name: string | null
  owner_username: string | null
  is_subscribed: boolean
  is_own: boolean
  publish_status: string | null
  created_at: string | null
}

export interface MarketSubscription {
  resource_type: string
  resource_id: string
  org_id: string
}

export interface DependencyManifest {
  content_deps: Array<{ resource_type: string; resource_id: string; resource_name: string }>
  connection_deps: Array<{ resource_type: string; resource_id: string; resource_name: string; credential_schema: Record<string, unknown>; allow_fallback: boolean }>
}

// --- Market API ---
export const marketApi = {
  browse: async (params?: { resource_type?: string; page?: number; size?: number; scope?: string; category?: string }) => {
    const sp = new URLSearchParams()
    if (params?.scope) sp.set('scope', params.scope)
    if (params?.category) sp.set('category', params.category)
    if (params?.resource_type) sp.set('resource_type', params.resource_type)
    if (params?.page) sp.set('page', String(params.page))
    if (params?.size) sp.set('size', String(params.size))
    const res = await apiFetch<ApiResponse<PaginatedResponse<MarketItem>>>(`/api/market?${sp}`)
    return res.data
  },

  subscribe: (body: { resource_type: string; resource_id: string; org_id?: string }) =>
    apiFetch<ApiResponse<{ subscribed: boolean; dependencies?: DependencyManifest }>>('/api/market/subscribe', {
      method: 'POST',
      body: JSON.stringify({ ...body, org_id: body.org_id ?? MARKET_ORG_ID }),
    }),

  unsubscribe: (body: { resource_type: string; resource_id: string; org_id?: string }) =>
    apiFetch<ApiResponse<unknown>>('/api/market/unsubscribe', {
      method: 'DELETE',
      body: JSON.stringify({ ...body, org_id: body.org_id ?? MARKET_ORG_ID }),
    }),

  listSubscriptions: (resource_type?: string) => {
    const sp = resource_type ? `?resource_type=${resource_type}` : ''
    return apiFetch<ApiResponse<MarketSubscription[]>>(`/api/market/subscriptions${sp}`)
  },

  dependencies: (params: { resource_type: string; resource_id: string }) => {
    const sp = new URLSearchParams()
    sp.set('resource_type', params.resource_type)
    sp.set('resource_id', params.resource_id)
    return apiFetch<ApiResponse<DependencyManifest>>(`/api/market/dependencies?${sp}`)
  },
}

// --- Convenience api object (used by Market page) ---
export const api = {
  browseMarket: marketApi.browse,
  subscribeResource: marketApi.subscribe,
  unsubscribeResource: marketApi.unsubscribe,
  listSubscriptions: marketApi.listSubscriptions,
  getResourceDependencies: marketApi.dependencies,

  setMcpMyCredentials: (serverId: string, body: { env?: Record<string, string>; headers?: Record<string, string> }) =>
    apiFetch<ApiResponse<unknown>>(`/api/mcp-servers/${serverId}/my-credentials`, { method: 'PUT', body: JSON.stringify(body) }),

  toggleConnector: (connectorId: string) =>
    apiFetch<ApiResponse<unknown>>(`/api/connectors/${connectorId}/toggle`, { method: 'POST', body: JSON.stringify({}) }),

  toggleKnowledgeBase: (kbId: string) =>
    apiFetch<ApiResponse<unknown>>(`/api/knowledge-bases/${kbId}/toggle`, { method: 'POST', body: JSON.stringify({}) }),
}
