import { API_BASE_URL, ACCESS_TOKEN_KEY, REFRESH_TOKEN_KEY } from "./constants"
import type { TokenResponse, LoginRequest, RegisterRequest } from "@/types/auth"
import type {
  ConversationResponse,
  ConversationDetail,
  ConversationCreate,
  PaginatedResponse,
} from "@/types/conversation"
import type { AgentResponse, AgentCreate, AgentUpdate } from "@/types/agent"
import type { FileUploadResponse, FileListItem } from "@/types/file"
import type { KBResponse, KBCreate, KBUpdate, KBDocumentResponse, KBRetrieveResult } from "@/types/kb"

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
      const res = await fetch(`${API_BASE_URL}/api/auth/refresh`, {
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

  let res = await fetch(`${API_BASE_URL}${path}`, { ...options, headers })

  if (res.status === 401 && token) {
    const refreshed = await refreshAccessToken()
    if (refreshed) {
      headers["Authorization"] = `Bearer ${refreshed.access_token}`
      res = await fetch(`${API_BASE_URL}${path}`, { ...options, headers })
    } else {
      authFailureCallback?.()
      throw new ApiError(401, "Session expired")
    }
  }

  if (!res.ok) {
    const body = await res.json().catch(() => ({}))
    throw new ApiError(res.status, body.detail || body.error || res.statusText)
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
}

// --- Conversation API ---
interface ApiResponse<T> {
  success: boolean
  data: T
  error: string | null
}

export const conversationApi = {
  list: (page = 1, size = 50) =>
    apiFetch<PaginatedResponse<ConversationResponse>>(
      `/api/conversations?page=${page}&size=${size}`,
    ),

  create: (body: ConversationCreate) =>
    apiFetch<ApiResponse<ConversationResponse>>("/api/conversations", {
      method: "POST",
      body: JSON.stringify(body),
    }).then((r) => r.data),

  get: (id: string) =>
    apiFetch<ApiResponse<ConversationDetail>>(
      `/api/conversations/${id}`,
    ).then((r) => r.data),

  update: (id: string, body: { title?: string; status?: string }) =>
    apiFetch<ApiResponse<ConversationResponse>>(
      `/api/conversations/${id}`,
      { method: "PATCH", body: JSON.stringify(body) },
    ).then((r) => r.data),

  delete: (id: string) =>
    apiFetch<ApiResponse<{ deleted: string }>>(
      `/api/conversations/${id}`,
      { method: "DELETE" },
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
}

// --- File API ---
export const fileApi = {
  upload: async (file: File): Promise<FileUploadResponse> => {
    const token = getAccessToken()
    const formData = new FormData()
    formData.append("file", file)
    const headers: Record<string, string> = {}
    if (token) headers["Authorization"] = `Bearer ${token}`
    const res = await fetch(`${API_BASE_URL}/api/files/upload`, {
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
  listDocuments: (kbId: string) =>
    apiFetch<ApiResponse<KBDocumentResponse[]>>(
      `/api/knowledge-bases/${kbId}/documents`,
    ).then((r) => r.data),

  uploadDocument: async (kbId: string, file: File): Promise<KBDocumentResponse> => {
    const token = getAccessToken()
    const formData = new FormData()
    formData.append("file", file)
    const headers: Record<string, string> = {}
    if (token) headers["Authorization"] = `Bearer ${token}`
    const res = await fetch(
      `${API_BASE_URL}/api/knowledge-bases/${kbId}/documents`,
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
