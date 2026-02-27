export interface KBResponse {
  id: string
  name: string
  description: string | null
  chunk_strategy: string
  chunk_size: number
  chunk_overlap: number
  retrieval_mode: string
  document_count: number
  total_chunks: number
  status: string
  created_at: string
  updated_at: string | null
}

export interface KBDocumentResponse {
  id: string
  kb_id: string
  filename: string
  file_size: number
  file_type: string
  chunk_count: number
  status: string
  error_message: string | null
  created_at: string
}

export interface KBCreate {
  name: string
  description?: string
  chunk_strategy?: string
  chunk_size?: number
  chunk_overlap?: number
  retrieval_mode?: string
}

export interface KBUpdate {
  name?: string
  description?: string
  chunk_strategy?: string
  chunk_size?: number
  chunk_overlap?: number
  retrieval_mode?: string
}

export interface KBRetrieveResult {
  content: string
  metadata: Record<string, unknown>
  score: number
}
